#!/usr/bin/python3
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: discover_remote_digests
short_description: List image tags and parallel skopeo inspect aggregated by digest
version_added: "1.0.0"
description:
  - Lists tags with C(skopeo list-tags) unless C(tags) is provided.
  - Runs C(skopeo inspect) for each tag concurrently with retries and backoff.
  - Retries remaining failures serially to reduce CDN contention.
  - Permanent registry policy errors (for example rejected C(latest)) are
    returned separately and are not treated as transient failures.
  - Transient failures that remain after retries are returned in C(failed_tags)
    and must not be treated as deleted by the caller.
options:
  image:
    description: Image repository path (without tag), e.g. registry.example/ns/name.
    required: true
    type: str
  tags:
    description:
      - Optional explicit tag list. When omitted, tags are loaded via
        C(skopeo list-tags).
    required: false
    type: list
    elements: str
  exclude_patterns:
    description:
      - Regex patterns; tags matching any pattern are skipped before inspect.
    required: false
    type: list
    elements: str
    default: []
  jobs:
    description: Maximum parallel skopeo inspect workers.
    required: false
    type: int
    default: 4
  timeout:
    description: Per-tag skopeo inspect timeout in seconds.
    required: false
    type: int
    default: 120
  retries:
    description:
      - Extra attempts after the first failure for transient inspect/list-tags
        errors (EOF, timeouts, 5xx, etc.).
    required: false
    type: int
    default: 5
  retry_delay:
    description: Initial delay in seconds before the first retry.
    required: false
    type: float
    default: 1.0
  retry_backoff:
    description: Multiplier applied to the delay after each failed attempt.
    required: false
    type: float
    default: 2.0
author:
  - Lenny Shirley (@lennysh)
"""

EXAMPLES = r"""
- name: List tags and inspect in parallel
  discover_remote_digests:
    image: registry.redhat.io/ansible-automation-platform/ee-minimal-rhel8
    exclude_patterns:
      - "-source"
      - "sha256"
      - "^latest$"
    jobs: 4
    retries: 5
  register: remote_inspect
"""

RETURN = r"""
images_by_digest:
  description: Map of digest to image_tags and created.
  returned: success
  type: dict
  sample:
    sha256:abc:
      image_tags: ["2.16", "latest"]
      created: "2026-01-01 00:00:00 UTC"
listed_tags:
  description: Tags discovered before exclude filtering.
  returned: success
  type: list
  elements: str
tags:
  description: Tags inspected after exclude filtering.
  returned: success
  type: list
  elements: str
tag_count:
  description: Number of tags inspected.
  returned: success
  type: int
digest_count:
  description: Number of unique digests discovered.
  returned: success
  type: int
failed_tags:
  description:
    - Tags where skopeo inspect still failed after retries (transient errors).
    - Do not treat these as deleted.
  returned: success
  type: list
  elements: str
failed_tag_errors:
  description: Map of failed tag to last error message.
  returned: success
  type: dict
permanent_failed_tags:
  description:
    - Tags rejected by registry policy or permanent not-found style errors.
    - Skipped after classification; not counted as transient failures.
  returned: success
  type: list
  elements: str
permanent_failed_tag_errors:
  description: Map of permanently failed tag to error message.
  returned: success
  type: dict
"""

import concurrent.futures
import json
import random
import re
import subprocess
import time

from ansible.module_utils.basic import AnsibleModule


# Quay CDN / network flakiness under parallel load
_TRANSIENT_MARKERS = (
    "EOF",
    "connection reset",
    "i/o timeout",
    "TLS handshake timeout",
    "temporary failure",
    "too many requests",
    "Client.Timeout",
    "context deadline exceeded",
    "broken pipe",
    "connection refused",
    "network is unreachable",
    "http2: stream closed",
    "server closed idle connection",
    "use of closed network connection",
    "502 Bad Gateway",
    "503 Service Unavailable",
    "504 Gateway Timeout",
    "429 Too Many Requests",
    "cdn01.quay.io",
    "cdn02.quay.io",
)

# Registry policy / hard absences — do not burn retries
_PERMANENT_MARKERS = (
    "unsupported:",
    "manifest unknown",
    "name unknown",
    "repository name not known",
    "denied:",
)


def _classify_error(stderr):
    """Return 'permanent' or 'transient' for a skopeo error string."""
    err = stderr or ""
    err_l = err.lower()
    for marker in _PERMANENT_MARKERS:
        if marker.lower() in err_l:
            return "permanent"
    for marker in _TRANSIENT_MARKERS:
        if marker.lower() in err_l:
            return "transient"
    # Unknown errors: retry — incomplete data is worse than a few extra attempts
    return "transient"


def _sleep_backoff(attempt, delay, backoff):
    """Sleep delay * backoff^attempt plus a small jitter."""
    base = float(delay) * (float(backoff) ** int(attempt))
    jitter = random.uniform(0.0, min(0.5, base * 0.25 + 0.05))
    time.sleep(base + jitter)


def _run_skopeo(cmd, timeout):
    """Run skopeo; return (rc, stdout, stderr_or_exc_str)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 1, "", "timeout: skopeo exceeded {0}s".format(timeout)
    except OSError as exc:
        return 1, "", "os error: {0}".format(exc)
    return result.returncode, result.stdout or "", (result.stderr or "").strip()


def _list_tags(skopeo_path, image, timeout, retries, retry_delay, retry_backoff):
    """Return all tags for image via skopeo list-tags (with retries)."""
    cmd = [skopeo_path, "list-tags", "docker://{0}".format(image)]
    last_err = ""
    attempts = max(0, int(retries)) + 1

    for attempt in range(attempts):
        rc, stdout, stderr = _run_skopeo(cmd, timeout)
        if rc == 0:
            try:
                payload = json.loads(stdout or "{}")
            except ValueError as exc:
                raise RuntimeError(
                    "skopeo list-tags returned invalid JSON: {0}".format(exc)
                )
            tags = payload.get("Tags") or []
            return [str(tag) for tag in tags if tag]

        last_err = stderr or "rc {0}".format(rc)
        if _classify_error(last_err) == "permanent" or attempt >= attempts - 1:
            break
        _sleep_backoff(attempt, retry_delay, retry_backoff)

    raise RuntimeError(
        "skopeo list-tags failed after {0} attempt(s): {1}".format(attempts, last_err)
    )


def _inspect_tag_once(skopeo_path, image, tag, timeout):
    """Single inspect attempt. Return (digest, created, error_str)."""
    cmd = [
        skopeo_path,
        "inspect",
        "--format",
        '{{.Digest}},{{.Created.Format "2006-01-02 15:04:05 MST"}}',
        "docker://{0}:{1}".format(image, tag),
    ]
    rc, stdout, stderr = _run_skopeo(cmd, timeout)
    line = (stdout or "").strip()
    if rc != 0 or not line or "," not in line:
        return None, None, stderr or "inspect failed (rc {0}, empty output)".format(rc)

    digest, created = line.split(",", 1)
    digest = digest.strip()
    created = created.strip()
    if not digest:
        return None, None, "inspect returned empty digest"
    return digest, created, ""


def _inspect_tag(skopeo_path, image, tag, timeout, retries, retry_delay, retry_backoff):
    """
    Inspect with retries.

    Returns (tag, digest, created, error, kind) where kind is None on success,
    or 'permanent' / 'transient' on failure.
    """
    last_err = ""
    attempts = max(0, int(retries)) + 1

    for attempt in range(attempts):
        digest, created, err = _inspect_tag_once(skopeo_path, image, tag, timeout)
        if digest is not None:
            return tag, digest, created, "", None

        last_err = err
        kind = _classify_error(err)
        if kind == "permanent" or attempt >= attempts - 1:
            return tag, None, None, last_err, kind
        _sleep_backoff(attempt, retry_delay, retry_backoff)

    return tag, None, None, last_err, "transient"


def _compile_exclude_patterns(exclude_patterns):
    compiled = []
    for pattern in exclude_patterns or []:
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            raise ValueError(
                "invalid exclude_patterns entry {0!r}: {1}".format(pattern, exc)
            )
    return compiled


def _filter_tags(tags, exclude_patterns):
    if not exclude_patterns:
        return list(tags)
    compiled = _compile_exclude_patterns(exclude_patterns)
    kept = []
    for tag in tags:
        if any(regex.search(tag) for regex in compiled):
            continue
        kept.append(tag)
    return kept


def _merge_result(remote, tag, digest, created):
    entry = remote.setdefault(
        digest,
        {"image_tags": [], "created": created},
    )
    if tag not in entry["image_tags"]:
        entry["image_tags"].append(tag)
    if created:
        entry["created"] = created


def _aggregate(
    skopeo_path,
    image,
    tags,
    jobs,
    timeout,
    retries,
    retry_delay,
    retry_backoff,
):
    remote = {}
    failed_tags = []
    failed_tag_errors = {}
    permanent_failed_tags = []
    permanent_failed_tag_errors = {}

    if not tags:
        return (
            remote,
            failed_tags,
            failed_tag_errors,
            permanent_failed_tags,
            permanent_failed_tag_errors,
        )

    pending = list(tags)

    # Pass 1: parallel with per-tag retries
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [
            pool.submit(
                _inspect_tag,
                skopeo_path,
                image,
                tag,
                timeout,
                retries,
                retry_delay,
                retry_backoff,
            )
            for tag in pending
        ]
        retry_serial = []
        for future in concurrent.futures.as_completed(futures):
            tag, digest, created, err, kind = future.result()
            if digest is not None:
                _merge_result(remote, tag, digest, created)
                continue
            if kind == "permanent":
                permanent_failed_tags.append(tag)
                permanent_failed_tag_errors[tag] = err
            else:
                retry_serial.append(tag)
                failed_tag_errors[tag] = err

    # Pass 2: serial cleanup for remaining transient failures (less CDN pressure)
    still_failed = []
    for tag in sorted(retry_serial, key=str):
        tag, digest, created, err, kind = _inspect_tag(
            skopeo_path,
            image,
            tag,
            timeout,
            retries,
            retry_delay,
            retry_backoff,
        )
        if digest is not None:
            _merge_result(remote, tag, digest, created)
            failed_tag_errors.pop(tag, None)
            continue
        if kind == "permanent":
            permanent_failed_tags.append(tag)
            permanent_failed_tag_errors[tag] = err
            failed_tag_errors.pop(tag, None)
        else:
            still_failed.append(tag)
            failed_tag_errors[tag] = err

    for entry in remote.values():
        entry["image_tags"] = sorted(entry["image_tags"], key=str)

    failed_tags = sorted(still_failed, key=str)
    permanent_failed_tags = sorted(set(permanent_failed_tags), key=str)
    return (
        remote,
        failed_tags,
        failed_tag_errors,
        permanent_failed_tags,
        permanent_failed_tag_errors,
    )


def run_module():
    module = AnsibleModule(
        argument_spec=dict(
            image=dict(type="str", required=True),
            tags=dict(type="list", elements="str", required=False, default=None),
            exclude_patterns=dict(type="list", elements="str", required=False, default=[]),
            jobs=dict(type="int", required=False, default=4),
            timeout=dict(type="int", required=False, default=120),
            retries=dict(type="int", required=False, default=5),
            retry_delay=dict(type="float", required=False, default=1.0),
            retry_backoff=dict(type="float", required=False, default=2.0),
        ),
        supports_check_mode=True,
    )

    image = module.params["image"]
    jobs = max(1, int(module.params["jobs"]))
    timeout = max(1, int(module.params["timeout"]))
    retries = max(0, int(module.params["retries"]))
    retry_delay = max(0.0, float(module.params["retry_delay"]))
    retry_backoff = max(1.0, float(module.params["retry_backoff"]))
    exclude_patterns = module.params["exclude_patterns"] or []

    skopeo_path = module.get_bin_path("skopeo", required=True)

    try:
        if module.params["tags"] is None:
            listed_tags = _list_tags(
                skopeo_path,
                image,
                timeout,
                retries,
                retry_delay,
                retry_backoff,
            )
        else:
            listed_tags = [tag for tag in module.params["tags"] if tag]
    except RuntimeError as exc:
        module.fail_json(msg=str(exc))

    try:
        tags = _filter_tags(listed_tags, exclude_patterns)
    except ValueError as exc:
        module.fail_json(msg=str(exc))

    if module.check_mode:
        module.exit_json(
            changed=False,
            images_by_digest={},
            listed_tags=listed_tags,
            tags=tags,
            tag_count=len(tags),
            digest_count=0,
            failed_tags=[],
            failed_tag_errors={},
            permanent_failed_tags=[],
            permanent_failed_tag_errors={},
        )

    (
        images_by_digest,
        failed_tags,
        failed_tag_errors,
        permanent_failed_tags,
        permanent_failed_tag_errors,
    ) = _aggregate(
        skopeo_path,
        image,
        tags,
        jobs,
        timeout,
        retries,
        retry_delay,
        retry_backoff,
    )
    module.exit_json(
        changed=False,
        images_by_digest=images_by_digest,
        listed_tags=listed_tags,
        tags=tags,
        tag_count=len(tags),
        digest_count=len(images_by_digest),
        failed_tags=failed_tags,
        failed_tag_errors=failed_tag_errors,
        permanent_failed_tags=permanent_failed_tags,
        permanent_failed_tag_errors=permanent_failed_tag_errors,
    )


def main():
    run_module()


if __name__ == "__main__":
    main()

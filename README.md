# Red Hat Ansible Automation Platform Execution Environment Version Matrix

[![GitHub last commit](https://img.shields.io/github/last-commit/lennysh/aap-ee-version-matrix.svg)](https://github.com/lennysh/aap-ee-version-matrix/commits/main) [![GitHub license](https://img.shields.io/github/license/lennysh/aap-ee-version-matrix.svg)](https://github.com/lennysh/aap-ee-version-matrix/blob/main/LICENSE) [![Contributions welcome](https://img.shields.io/badge/contributions-welcome-brightgreen.svg)](https://github.com/lennysh/aap-ee-version-matrix/pulls) ![GitHub contributors](https://img.shields.io/github/contributors/lennysh/aap-ee-version-matrix) ![GitHub Issues or Pull Requests](https://img.shields.io/github/issues/lennysh/aap-ee-version-matrix)

This repository is a community-driven version matrix for Red Hat Ansible Automation Platform (AAP) Execution Environments (EEs). It provides a quick reference for the package versions (`ansible-core`, `python`), included Ansible Collections, and OS details for each official EE image.

## 📋 Available Execution Environments

The tables below are automatically generated. Click on an image name to see detailed information about its contents.

[comment]: <> (BEGIN Ansible Managed)

| Image Name (Click for more details) |
| :---------------------------------- |
| [registry.redhat.io/ansible-automation-platform/ee-minimal-rhel8](./images/registry.redhat.io/ansible-automation-platform/ee-minimal-rhel8/README.md) |
| [registry.redhat.io/ansible-automation-platform/ee-minimal-rhel9](./images/registry.redhat.io/ansible-automation-platform/ee-minimal-rhel9/README.md) |
| [registry.redhat.io/ansible-automation-platform-24/ee-minimal-rhel8](./images/registry.redhat.io/ansible-automation-platform-24/ee-minimal-rhel8/README.md) |
| [registry.redhat.io/ansible-automation-platform-24/ee-minimal-rhel9](./images/registry.redhat.io/ansible-automation-platform-24/ee-minimal-rhel9/README.md) |
| [registry.redhat.io/ansible-automation-platform-24/ee-supported-rhel8](./images/registry.redhat.io/ansible-automation-platform-24/ee-supported-rhel8/README.md) |
| [registry.redhat.io/ansible-automation-platform-24/ee-supported-rhel9](./images/registry.redhat.io/ansible-automation-platform-24/ee-supported-rhel9/README.md) |
| [registry.redhat.io/ansible-automation-platform-25/ee-minimal-rhel8](./images/registry.redhat.io/ansible-automation-platform-25/ee-minimal-rhel8/README.md) |
| [registry.redhat.io/ansible-automation-platform-25/ee-minimal-rhel9](./images/registry.redhat.io/ansible-automation-platform-25/ee-minimal-rhel9/README.md) |
| [registry.redhat.io/ansible-automation-platform-25/ee-supported-rhel8](./images/registry.redhat.io/ansible-automation-platform-25/ee-supported-rhel8/README.md) |
| [registry.redhat.io/ansible-automation-platform-25/ee-supported-rhel9](./images/registry.redhat.io/ansible-automation-platform-25/ee-supported-rhel9/README.md) |
| [registry.redhat.io/ansible-automation-platform-26/ee-minimal-rhel9](./images/registry.redhat.io/ansible-automation-platform-26/ee-minimal-rhel9/README.md) |
| [registry.redhat.io/ansible-automation-platform-26/ee-supported-rhel9](./images/registry.redhat.io/ansible-automation-platform-26/ee-supported-rhel9/README.md) |
| [registry.redhat.io/ansible-automation-platform-27/ee-minimal-rhel9](./images/registry.redhat.io/ansible-automation-platform-27/ee-minimal-rhel9/README.md) |
| [registry.redhat.io/ansible-automation-platform-27/ee-supported-rhel9](./images/registry.redhat.io/ansible-automation-platform-27/ee-supported-rhel9/README.md) |
| [registry.redhat.io/ansible-automation-platform-tech-preview/ee-minimal-rhel8](./images/registry.redhat.io/ansible-automation-platform-tech-preview/ee-minimal-rhel8/README.md) |
| [registry.redhat.io/ansible-automation-platform-tech-preview/ee-minimal-rhel9](./images/registry.redhat.io/ansible-automation-platform-tech-preview/ee-minimal-rhel9/README.md) |

[comment]: <> (END Ansible Managed)

## 🛠 Updating the version matrix

The tables above are generated from Ansible vars files. Do **not** edit `images/**/README.md`
directly — run the playbooks below instead.

### Prerequisites

- Ansible 2.15+ with the `community.general` collection
- `podman` and `skopeo` on your PATH
- Access to `registry.redhat.io` (`podman login registry.redhat.io`)

### Workflow

Run from the repository root:

```bash
# 1. Discover registry tags/digests and write/update per-digest vars files
ansible-playbook image_inspector.yml --tags discover

# 2. (Optional) Pull images and inspect package/collection contents — slow
ansible-playbook image_inspector.yml --tags discover,details

# 3. Regenerate README.md for each image path from the vars files
ansible-playbook md_generator.yml
```

**Discover** (`--tags discover`) queries the registry and writes one Ansible vars file
per digest to `images/<image_path>/vars/<digest-hex>.yml` with `digest`, `image_tags`,
and `created`. It retries flaky registry/CDN errors, skips permanent policy rejects
(such as disallowed `latest`), and by default aborts if any tag still fails after
retries so prune never runs on incomplete data. It is safe to run regularly to pick
up new tags.

**Details** (`--tags details`) pulls each digest with `podman`, runs inspection commands
inside the container, and fills in `ansible_core_version`, collections, RPM/pip lists, etc.
Only digests missing data or with prior failures are re-inspected.

**Markdown generation** reads all `vars/*.yml` files under each image path and writes
`images/<image_path>/README.md`.

### Common options

```bash
# Drop digest vars files that no longer exist in the registry
ansible-playbook image_inspector.yml --tags discover -e prune_images=true

# Single image path for this run
ansible-playbook image_inspector.yml --tags discover \
  -e '{"image_inspector_image_paths": [{"path": "registry.redhat.io/ansible-automation-platform/ee-minimal-rhel8", "exclude_patterns": ["^latest$"]}]}'

# Validate no duplicate tags across digests
python3 scripts/check_duplicate_tags.py
```

See also:

- [`roles/image_inspector/README.md`](roles/image_inspector/README.md) — discover/details tags and role variables
- [`roles/md_generator/README.md`](roles/md_generator/README.md) — markdown generation
- [`roles/image_detail_report/README.md`](roles/image_detail_report/README.md) — ad-hoc single-image reports under `output/` (optional, local use)

## 🤝 Contributing

Contributions are highly encouraged! If you find a mistake or have an update for a new release, please help improve this resource.

To add or update an execution environment:

1. Add the image to `image_inspector_image_paths` in `roles/image_inspector/defaults/main.yml`
   (and in `image_inspector.yml` if you override paths there), using `path:` and optional
   `exclude_patterns:`.
2. Run the workflow above (`discover`, optionally `details`, then `md_generator`).
3. Submit a pull request with the updated vars files and generated READMEs.

To contribute, please **submit a pull request with your changes to the Ansible roles, playbooks, and generated data under `images/`**. Do not edit the Markdown files in the `images/` directory by hand, as they are regenerated by `md_generator`. Manual edits to those READMEs will be overwritten and should not be submitted alone.

## ✍️ Authors

* [LennySh](https://github.com/lennysh)

## 📜 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
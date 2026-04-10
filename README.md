# k8s-website-l10n-tools

Prototype scripts for reviewing localization outdatedness in the `kubernetes/website` repo.

This repo currently contains:

- `detect_l10n_drift_structural.py` — structural l10n drift detector
- `report_l10n_status.py` — maintainer-facing Markdown report generator

## Intended environment

These scripts are meant to be tested inside a local checkout of the `kubernetes/website` repo.

Place both scripts under:

```bash
website/scripts/
```

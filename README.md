# k8s-website-l10n-tool

A review helper for localization teams working on
[`kubernetes/website`](https://github.com/kubernetes/website). It flags
localized pages that may no longer match the current English content, so
maintainers can decide what to review first.

**Status:** prototype / proof-of-concept.

---

## Contents

- [Why this exists](#why-this-exists)
- [What it does](#what-it-does)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Generated reports](#generated-reports)
- [How classification works](#how-classification-works)
- [Limitations](#limitations)
- [Contributing](#contributing)
- [License](#license)

---

## Why this exists

A lot of localization work is not translating new text — it is checking
whether localized pages are still in step with upstream English. Two
freshness-based workflows already exist for this:

- **Git history** — comparing the localized file's last commit against
  upstream English changes, similar to `kubernetes/website/scripts/lsync.sh`.
- **Hugo `page-lastmod`** — Hugo can resolve a localized page as older
  than English at render time (see `kubernetes/website` PR #41768),
  which now affects all localizations.

Both are useful, and both fit naturally into existing workflows. But
they answer the same question — *did something change upstream?* — and
freshness alone can be hard to interpret. A small local cleanup can
make a stale page look recent; a formatting or maintenance edit on the
English side can make every localization look urgent even when there is
little or nothing meaningful to update.

This tool tries a different, complementary signal. Instead of
timestamps or commit order, it compares the **current English page**
with the **current localized page** and looks for visible structural
gaps: missing headings, missing code blocks, missing anchors, newer
`v1.XX` mentions that didn't get carried over, paired drift in
feature-state and `apiVersion` / `kind` tokens. It is not a translation-
quality check, and it is not meant to replace the freshness-based
workflows above. It is a triage helper that answers the next review
question after Git or Hugo says "English is newer": **does the
localized page also show visible signs that it may need attention?**

For a closer look at how these three signals overlap and disagree
across real localized files, see [`3way-traige-results.md`](3way-traige-results.md).

## What it does

For every English file under `content/en/`, the script looks for a
matching translation under `content/<locale>/`, gathers a small set of
named **indicators** on the pair, and runs an ordered list of rules to
classify the localized file as one of:

| Status | Meaning |
|---|---|
| `Outdated`          | Substantive drift — translate / refresh soon |
| `Possibly outdated` | Some drift — worth a look, lower urgency |
| `Up to date`        | No meaningful drift detected |

Localized files with no English source are reported separately as
**orphans** (likely renamed or removed upstream).

The comparison is lightweight by design — it runs across the full
`content/` tree in seconds and needs only the Python standard library.

---

## Requirements

- Python 3.8 or newer (standard library only — no third-party packages)
- A local checkout of
  [`kubernetes/website`](https://github.com/kubernetes/website)

---

## Installation

Clone this repo and copy the script into your local `kubernetes/website`
checkout under `scripts/`:

```bash
git clone https://github.com/apullo777/k8s-website-l10n-tool.git
cp k8s-website-l10n-tool/l10n-outdatedness-triage.py \
   /path/to/kubernetes/website/scripts/
```

Alternatively, keep the script here and point it at your
`kubernetes/website` checkout with `--repo-root` (see [Usage](#usage)).

---

## Usage

From inside a `kubernetes/website` checkout:

```bash
# All non-English locales (default when no option is given)
python3 scripts/l10n-outdatedness-triage.py

# Single locale
python3 scripts/l10n-outdatedness-triage.py --lang ko

# Multiple locales (space-separated)
python3 scripts/l10n-outdatedness-triage.py --lang ko zh-cn ja

# Explicitly scan all locales
python3 scripts/l10n-outdatedness-triage.py --all

# Show every indicator per file (default prints one compact line)
python3 scripts/l10n-outdatedness-triage.py --lang ko --verbose

# Write reports somewhere other than the current directory
python3 scripts/l10n-outdatedness-triage.py --output-dir /tmp/l10n

# Add clickable links next to each file entry
python3 scripts/l10n-outdatedness-triage.py --lang ko --link web    # GitHub URLs
python3 scripts/l10n-outdatedness-triage.py --lang ko --link local  # local Markdown paths
```

From anywhere, with an explicit repo root:

```bash
python3 l10n-outdatedness-triage.py --lang ko --repo-root /path/to/website
```

All options:

```text
--lang CODE [CODE ...]  One or more locales to scan (e.g. --lang ko  or
                        --lang ko zh-cn ja)
--all                   All locales under content/ except en (default
                        when no option is given)
--repo-root DIR         Path to kubernetes/website repo root (auto-detected
                        by walking up from the current directory)
--output-dir, -o DIR    Directory for report files (default: .)
--verbose, -v           Show all reasons per file plus the named indicators
                        that fired (default: one compact line, first reason)
--link MODE             Add [(en)] and [(<locale>)] links after each file
                        entry. MODE: 'web' for GitHub URLs (opens code view);
                        'local' for paths relative to the output directory
--branch BRANCH         Branch for GitHub links when --link web (default: main)
```

---

## Generated reports

Each run writes Markdown reports to `--output-dir` (the current directory
by default):

| File | When it is written | Contents |
|---|---|---|
| `l10n-status-<locale>.md` | Always, one per locale scanned | Status counts, top affected doc areas, an orphan section, and per-file entries grouped by status |
| `l10n-status-all.md` | Only when more than one locale is scanned | Roll-up table linking each per-locale report with evaluated / `Up to date` / orphan / `Outdated` / `Possibly outdated` counts |

A per-locale report looks roughly like:

```markdown
## Localization status: `ko`

| Status | Count |
|---|---:|
| Evaluated         | 412 |
| Up to date        | 377 |
| Orphan            | 3   |
| Outdated          | 11  |
| Possibly outdated | 24  |

**Top affected areas (flagged files):**
- `tasks/`: 9 files
- `concepts/`: 7 files
...

### Orphan localized files, no English source (3)
- `content/ko/docs/...md`
...

### Outdated (11)
- `content/ko/docs/...md` — Outdated: Localized file is missing
  headings present in source (3 H2)
...
```

Default output is one compact line per file with the first reason.
`--verbose` expands each entry to its full reason list plus the named
indicators that fired, so a reviewer can see exactly which signals
classified the page.

`--link web` and `--link local` add a second line under each file entry
with `[(en)]` and `[(<locale>)]` links — `web` points at the source on
GitHub (defaults to `main`, override with `--branch`), `local` points at
the Markdown files in your checkout via paths relative to the output
directory.

---

## How classification works

The script does **not** assign points or thresholds. It collects a small
set of named indicators per file, then runs an ordered list of rules.
The first matching rule wins, and every status decision can be traced
back to the indicators that fired.

### Indicator buckets

**Strong** (any one is a serious signal on its own):

| Indicator | Trigger |
|---|---|
| `empty_stub`               | Localized body empty, source body non-empty |
| `severe_heading_loss`      | ≥ 2 H2 missing (or ≥ 1 H2 + ≥ 5 H3 missing) |
| `severe_code_loss`         | ≥ 3 code blocks missing |
| `severe_anchor_loss`       | ≥ 5 explicit `{#anchor}` IDs missing |
| `severe_version_mismatch`  | ≥ 3 newer `v1.X` versions referenced in source are missing |

**Supporting** (corroborate together):

| Indicator | Trigger |
|---|---|
| `large_length_gap`           | l10n-to-source visible-line ratio < 0.50 |
| `moderate_length_gap`        | ratio in [0.50, 0.65) |
| `moderate_heading_loss`      | 1 H2 missing, or 2–4 H3 missing |
| `moderate_code_loss`         | 1–2 code blocks missing |
| `moderate_anchor_loss`       | 1–4 explicit anchor IDs missing |
| `moderate_version_mismatch`  | 1–2 missing newer `v1.X` versions |

**Special signals**:

| Indicator | Role |
|---|---|
| `severe_api_and_feature_mismatch` | Both feature-state AND `apiVersion` / `kind` values present in source are missing — direct-`Outdated` on its own |
| `small_length_gap` | ratio in [0.65, 0.80), demotion-only — pulls an `Up to date` file to `Possibly outdated`, but only when at least one non-length-gap indicator (or a content-token mismatch) also fires |

### Classification rules (in order)

```
1. empty_stub or severe_api_and_feature_mismatch       → Outdated
2. ≥ 2 strong                                          → Outdated
3. ≥ 1 strong + ≥ 1 supporting                         → Outdated
4. large_length_gap + ≥ 1 non-length-gap supporting    → Outdated
   (with Latin translated-anchor false-alarm guard)
5. ≥ 3 supporting                                      → Outdated
6. ≥ 1 strong OR ≥ 1 supporting                        → Possibly outdated
7. small_length_gap                                    → Possibly outdated
8. otherwise                                           → Up to date
```

### File-shape guards

Several guards silence indicators on file shapes where they're known to
mislead, so legitimate language and style differences don't surface as
false alarms:

- **Short EN files** — length gap requires a corroborating non-length
  indicator below 40 EN lines (56 for CJK).
- **JA compactness** — JA-only override drops length gap when no other
  indicator fires (empirically a false alarm in JA).
- **Latin compactness** — for `pt-br`, `es`, `de`, `fr`, `it`, length
  gap is dropped when body word volume matches a full translation
  (≥ 0.90 ratio).
- **zh-cn H2-as-H3** — bilingual zh-cn pages sometimes render EN H2 as
  H3 with the EN heading kept in a comment; the apparent H2 deficit is
  collapsed to 1 with a verification note.
- **HTML-comment stripping** — so zh-cn bilingual pages don't fake
  zero-drift by hiding the EN copy in `<!-- … -->`.
- **Latin translated-anchor guard** — some Latin locales translate
  anchor IDs (e.g. `{#pourquoi-kubernetes}`); rule 4 is suppressed when
  the only non-length supporting indicator is `moderate_anchor_loss`
  on a full-volume translation.

See the docstring at the top of `l10n-outdatedness-triage.py` and the
inline comments for the exact thresholds. For a longer walkthrough of
why the v2 classifier replaces the older score-based approach, see
[`v2-script.md`](v2-script.md).

---

## Limitations

- **Pure body-text rewrites.** If English rewrites a paragraph without
  changing headings, code, or length, the detector will not see it.
  These drifts still need a Git-history-based complement.
- **Very short files.** Under ~15 lines, the tool cannot reliably tell
  natural compactness from genuine staleness, and length gap is gated
  on corroboration.
- **Translated anchor IDs.** When a locale translates the anchor ID
  itself (e.g. `{#pourquoi-kubernetes}`), it is flagged as missing even
  though the content is fine. The Latin translated-anchor guard
  prevents this from promoting a file to `Outdated`, but the
  anchor still surfaces in the reasons list.
- **Scope.** File-level triage only — not a translation-quality check
  and not a replacement for human review.

---

## Contributing

Issues and pull requests are welcome. When reporting a false positive or
a missed drift, it helps to include:

- the locale and file path (e.g. `content/ja/docs/...`)
- the status and indicators the tool produced (run with `--detailed`)
- a short note on what the correct outcome should be

New guards should be validated against real files before landing —
confirm both that the false-positive pattern is real *and* that the fix
doesn't hide genuine drift.

---

## License

TBD — a `LICENSE` file will be added before the project is published
more widely.

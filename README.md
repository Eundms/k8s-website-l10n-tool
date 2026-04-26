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
| `highly_outdated`   | Substantive drift — translate / refresh soon |
| `possibly_outdated` | Some drift — worth a look, lower urgency |
| `current`           | No meaningful drift detected |

Localized files with no English counterpart are reported separately as
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
# Single locale
python3 scripts/l10n-outdatedness-triage.py --lang ko

# Multiple locales
python3 scripts/l10n-outdatedness-triage.py --langs ko,zh-cn,ja

# All non-English locales
python3 scripts/l10n-outdatedness-triage.py --all-langs

# Show every indicator per file (default prints one compact line)
python3 scripts/l10n-outdatedness-triage.py --lang ko --detailed

# Write reports somewhere other than the current directory
python3 scripts/l10n-outdatedness-triage.py --all-langs --output-dir /tmp/l10n
```

From anywhere, with an explicit repo root:

```bash
python3 l10n-outdatedness-triage.py --lang ko --repo-root /path/to/website
```

All options:

```text
--lang CODE          Single locale (e.g. ko)
--langs CODES        Comma-separated locales (e.g. ko,zh-cn,ja)
--all-langs          All locales under content/ except en
--repo-root DIR      Path to kubernetes/website repo root (auto-detected
                     by walking up from the current directory)
--output-dir DIR     Directory for report files (default: .)
--detailed           Show all reasons per file plus the named indicators
                     that fired (default: one compact line, first reason)
```

---

## Generated reports

Each run writes Markdown reports to `--output-dir` (the current directory
by default):

| File | When it is written | Contents |
|---|---|---|
| `l10n-indicators-<locale>.md` | Always, one per locale scanned | Status counts, top affected doc areas, per-file entries grouped by status, and a separate orphan section |
| `l10n-indicators-index.md` | Only when more than one locale is scanned | Roll-up table linking each per-locale report with `highly_outdated` / `possibly_outdated` / `current` / orphan counts |

A per-locale report looks roughly like:

```markdown
## Localization status (file-level): `ko`

| Status | Count |
|---|---:|
| Evaluated localized files | 412 |
| highly_outdated   | 11  |
| possibly_outdated | 24  |
| current           | 377 |
| Orphans (no EN)   | 3   |

**Top affected areas (non-current files):**
- `tasks/`: 9 files
- `concepts/`: 7 files
...

### Highly outdated (11)
- `content/ko/docs/...md` — highly_outdated: Localized file is missing
  headings present in EN (3 H2)
...

### Orphan localized files, no EN counterpart (3)
- `content/ko/docs/...md`
...
```

Default output is one compact line per file with the first reason.
`--detailed` expands each entry to its full reason list plus the named
indicators that fired, so a reviewer can see exactly which signals
classified the page.

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
| `empty_stub`           | Localized body empty, EN body non-empty |
| `severe_heading_loss`  | ≥ 2 H2 missing (or ≥ 1 H2 + ≥ 5 H3 missing) |
| `major_code_loss`      | ≥ 3 code blocks missing |
| `heavy_anchor_loss`    | ≥ 5 explicit `{#anchor}` IDs missing |
| `heavy_version_drift`  | ≥ 3 newer `v1.X` versions referenced in EN are missing |

**Supporting** (corroborate together):

| Indicator | Trigger |
|---|---|
| `large_length_gap`        | l10n-to-EN visible-line ratio < 0.50 |
| `moderate_length_gap`     | ratio in [0.50, 0.65) |
| `moderate_heading_loss`   | 1 H2 missing, or 2–4 H3 missing |
| `moderate_code_loss`      | 1–2 code blocks missing |
| `moderate_anchor_loss`    | 1–4 explicit anchor IDs missing |
| `moderate_version_drift`  | 1–2 missing newer `v1.X` versions |

**Special signals**:

| Indicator | Role |
|---|---|
| `severe_api_and_feature_drift` | Both feature-state AND `apiVersion` / `kind` values present in EN are missing — direct-`highly_outdated` on its own |
| `small_length_gap` | ratio in [0.65, 0.80), demotion-only — pulls a `current` file to `possibly_outdated`, but only when at least one non-length-gap indicator (or a content-token mismatch) also fires |

### Classification rules (in order)

```
1. empty_stub or severe_api_and_feature_drift          → highly_outdated
2. ≥ 2 strong                                          → highly_outdated
3. ≥ 1 strong + ≥ 1 supporting                         → highly_outdated
4. large_length_gap + ≥ 1 non-length-gap supporting    → highly_outdated
   (with Latin translated-anchor false-alarm guard)
5. ≥ 3 supporting                                      → highly_outdated
6. ≥ 1 strong OR ≥ 1 supporting                        → possibly_outdated
7. small_length_gap                                    → possibly_outdated
8. otherwise                                           → current
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
  prevents this from promoting a file to `highly_outdated`, but the
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

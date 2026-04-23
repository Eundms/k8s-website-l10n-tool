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
- [How scoring works](#how-scoring-works)
- [Limitations](#limitations)
- [Contributing](#contributing)
- [License](#license)

---

## Why this exists

A lot of localization work is not translating new text — it is checking
whether localized pages are still in step with upstream English. Teams
usually lean on Git history for this, but Git mainly tells us *that
something changed*, not *whether the translation still matches the
current English page*. A small local cleanup can make a stale page look
recent; a small upstream formatting change can look urgent even when no
translation work is needed.

This tool tries a different signal. Instead of timestamps or commit
order, it compares the **current English page** with the **current
localized page** and looks for visible structural gaps — missing
headings, missing code blocks, missing anchors, newer `v1.XX` mentions
that didn't get carried over. It is not a translation-quality check. It
is a triage helper that answers one practical question: **which pages
should we review first?**

## What it does

For every English file under `content/en/`, the script looks for a
matching translation under `content/<locale>/`, scores the pair on a
small set of content indicators, and writes a Markdown report grouped by
priority. The comparison is lightweight by design — it runs across the
full `content/` tree in seconds and needs only the Python standard
library.

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
cp k8s-website-l10n-tool/triage-by-content-signals.py \
   /path/to/kubernetes/website/scripts/
```

Alternatively, keep the script here and point it at your
`kubernetes/website` checkout with `--repo-root` (see [Usage](#usage)).

---

## Usage

From inside a `kubernetes/website` checkout:

```bash
# Single locale
python3 scripts/triage-by-content-signals.py --lang ko

# Multiple locales
python3 scripts/triage-by-content-signals.py --langs ko,zh-cn,ja

# All non-English locales
python3 scripts/triage-by-content-signals.py --all-langs

# Show every indicator per file (default prints one compact line)
python3 scripts/triage-by-content-signals.py --lang ko --detailed

# Write reports somewhere other than the current directory
python3 scripts/triage-by-content-signals.py --all-langs --output-dir /tmp/l10n
```

From anywhere, with an explicit repo root:

```bash
python3 triage-by-content-signals.py --lang ko --repo-root /path/to/website
```

All options:

```text
--lang CODE          Single locale (e.g. ko)
--langs CODES        Comma-separated locales (e.g. ko,zh-cn,ja)
--repo-root DIR      Path to kubernetes/website repo root (auto-detected
                     by walking up from the current directory)
--output-dir DIR     Directory for report files (default: .)
--detailed           Show all indicator lines per file
```

---

## Generated reports

Each run writes Markdown reports to `--output-dir` (the current directory
by default):

| File | When it is written | Contents |
|---|---|---|
| `l10n-outdated-report-<locale>.md` | Always, one per locale scanned | Summary table (counts by priority), top affected doc areas, and per-file entries grouped by priority |
| `l10n-outdated-report-index.md` | Only when more than one locale is scanned | Roll-up table linking each per-locale report with High / Medium / Low / Up-to-date counts |

A per-locale report looks roughly like:

```markdown
## Localization status (file-level): `ko`

| | Count |
|---|---|
| Scanned pairs   | 412 |
| High priority   | 11  |
| Medium priority | 24  |
| Low priority    | 57  |
| Up to date      | 320 |

**Top affected areas:**
- `tasks/`: 9 files
- `concepts/`: 7 files
...

### High priority (11)
- `content/ko/docs/...md` — score 72: missing 3 H2 sections, 18 anchors, 2 k8s versions
...
```

Each file lands in one of four priority buckets by score:

| Priority | Score | Meaning |
|---|---|---|
| **High** | ≥ 50 | Serious content drift; review first |
| **Medium** | ≥ 20 | Meaningful gaps |
| **Low** | ≥ 5 | Minor issues |
| **Up to date** | < 5 | — |

---

## How scoring works

Each English / localized file pair is scored on six content indicators:

1. **Line ratio** — is the translation roughly the same length?
2. **Section headings** — are H2 / H3 headings missing?
3. **Code blocks** — are code examples missing?
4. **Anchor links** — are `{#section-id}` anchors missing?
5. **Untranslated paragraphs** (CJK languages) — are English paragraphs
   left inside the translation?
6. **Kubernetes versions** — is the translation missing newer `v1.XX`
   mentions?

The indicators sum into a single score that maps to the priority bucket
above. Several guards (length thresholds for short files, a word-count
check for Latin-script wrap-style differences, a long-file gate for
Japanese, HTML-comment stripping for bilingual Chinese pages) reduce
false alarms from legitimate language and style differences. See the
docstring at the top of `triage-by-content-signals.py` and the inline
comments for the exact thresholds.

---

## Limitations

- **Pure body-text rewrites.** If English rewrites a paragraph without
  changing headings, code, or length, the detector will not see it.
  These drifts still need a Git-history-based complement.
- **Very short files.** Under ~15 lines, the tool cannot reliably tell
  natural compactness from genuine staleness.
- **Translated anchor IDs.** When a locale translates the anchor ID
  itself (e.g. `{#pourquoi-kubernetes}`), it is flagged as missing even
  though the content is fine. Low reader impact.
- **Scope.** File-level triage only — not a translation-quality check
  and not a replacement for human review.

---

## Contributing

Issues and pull requests are welcome. When reporting a false positive or
a missed drift, it helps to include:

- the locale and file path (e.g. `content/ja/docs/...`)
- the score and indicators the tool produced
- a short note on what the correct outcome should be

New guards should be validated against real files before landing —
confirm both that the false-positive pattern is real *and* that the fix
doesn't hide genuine drift.

---

## License

TBD — a `LICENSE` file will be added before the project is published
more widely.

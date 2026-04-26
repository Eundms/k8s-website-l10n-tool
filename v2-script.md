# The v2 localization outdatedness detector

A brief walkthrough of what the v2 script `l10n-outdatedness-triage.py` does, how it
differs from the older `triage-by-content-signals.py` script, what we kept and cut
along the way, and why v2 script still ends up with a comparable line count
even though it's the simpler design.

---
## TLDL:

- v2 script is a **rule-based** classifier for localized Kubernetes pages: it
  emits indicators, then a small ordered set of rules decides one of
  three statuses: `highly_outdated`, `possibly_outdated`, or `current`.
- It replaces an older **score-based** triage script that summed
  weighted points and bucketed them into high / medium / low /
  up-to-date — easy to explain mechanically, but easy to over-flag.
- Every classification in v2 is traceable to the indicators that fired,
  not to a numeric total.
- We started with a much wider set of token indicators ("missing
  shortcode names", "missing CLI flags", etc.). Audits showed most were
  noisy or low-value, so they were cut. Only **feature-state** and
  **API kind / apiVersion** tokens earned their place — and only as a
  paired signal.
- v2 is **conservative by design**: a single token mismatch never flags
  a page; an unsupported length gap on a tiny EN file never flags a
  page; Latin / JA / zh-cn shape quirks are silenced before
  classification.
- v2 doesn't save raw lines of code over the older script. It saves
  conceptual complexity: fewer arbitrary weights, fewer noisy
  indicators, more explicit rules.

---

## 1. What is the current v2 script?

### Pipeline

For every locale under `content/<lang>/`, v2 walks the tree and runs
this pipeline per Markdown file:

1. **Find the EN counterpart** under `content/en/`. If the localized
   file has no EN counterpart and lives under `docs/` (and isn't a
   skip-listed basename like `README.md` or `_search.md`), it's
   recorded as an orphan — kept out of the classifier and rendered
   in its own section of the report with a short explanatory note.
2. **Parse both files**. Extract visible-line count, body word count,
   H2 / H3 counts, fenced code-block count, explicit `{#anchor}` IDs,
   `v1.X` Kubernetes version references, and content tokens
   (`for_k8s_version="…"`, `feature_gate_name="…"`, `apiVersion:`,
   `kind:`).
3. **Compute drift stats** — line-ratio, body-word ratio, and missing
   counts of each structural element.
4. **Apply file-shape guards** that silence indicators on file shapes
   where they're known to mislead (JA compactness, Latin compactness,
   short-EN corroboration, zh-cn H2-as-H3, etc.).
5. **Gather indicators** into three buckets.
6. **Run the rule-based classifier**. The first rule that matches wins.
7. **Render per-language reports** plus an index report when more than
   one language was scanned. Each per-language report has a status-count
   summary (with orphans counted alongside the three classified
   statuses), a "top affected doc areas" breakdown, the
   `highly_outdated` and `possibly_outdated` file lists, and a
   separate orphan section. Default output is compact — one line per
   file with the first reason. `--detailed` expands each file to its
   full reason list plus the named indicators that fired, so a
   reviewer can see exactly which signals classified the page.

### Rule-based, not score-based

v2 does not assign points. There is no total, no threshold. Each page
collects a small set of named indicators, and a fixed ordered list of
rules decides the status. This means every status decision can be
explained by saying "rule N fired because indicators X and Y were
present."

### The three statuses

| Status | Meaning |
|---|---|
| `highly_outdated`   | Substantive drift — translate / refresh soon |
| `possibly_outdated` | Some drift — worth a look, lower urgency |
| `current`           | No meaningful drift detected |

### The indicator buckets

**Strong** (5 — any one is a serious signal on its own):

| Indicator | Trigger |
|---|---|
| `empty_stub`           | Localized body empty, EN body non-empty |
| `severe_heading_loss`  | ≥ 2 H2 missing (or ≥ 1 H2 + ≥ 5 H3 missing) |
| `major_code_loss`      | ≥ 3 code blocks missing |
| `heavy_anchor_loss`    | ≥ 5 explicit `{#anchor}` IDs missing |
| `heavy_version_drift`  | ≥ 3 distinct `v1.X` versions referenced in EN are missing |

**Supporting** (6 — corroborate together):

| Indicator | Trigger |
|---|---|
| `large_length_gap`        | l10n-to-EN visible-line ratio < 0.50 |
| `moderate_length_gap`     | ratio in [0.50, 0.65) |
| `moderate_heading_loss`   | 1 H2 missing, or 2–4 H3 missing |
| `moderate_code_loss`      | 1–2 code blocks missing |
| `moderate_anchor_loss`    | 1–4 explicit anchor IDs missing |
| `moderate_version_drift`  | 1–2 missing `v1.X` versions |

**Special signals** (not in either bucket):

| Indicator | Role |
|---|---|
| `severe_api_and_feature_drift` | Both feature-state AND apiVersion / kind values present in EN are missing from localized — direct-HIGH on its own |
| `small_length_gap`       | ratio in [0.65, 0.80), demotion-only — pulls a `current` file to `possibly_outdated`, but only when at least one non-length-gap indicator (or a single content-token mismatch) is also present |

### The classification rules (in order)

```
1.   empty_stub or severe_api_and_feature_drift          → highly_outdated
2.   ≥ 2 strong                                          → highly_outdated
3.   ≥ 1 strong + ≥ 1 supporting                         → highly_outdated
4.   large_length_gap + ≥ 1 non-length-gap supporting    → highly_outdated
     (with Latin translated-anchor false-alarm guard)
5.   ≥ 3 supporting                                      → highly_outdated
6.   ≥ 1 strong OR ≥ 1 supporting                        → possibly_outdated
7.   small_length_gap                                    → possibly_outdated
8.   otherwise                                           → current
```

severe_api_and_feature_drift is a v2 refinement. Earlier iterations had two separate
content-token indicators and an explicit AND-rule; v2 collapses them
into one semantic indicator (`severe_api_and_feature_drift`) so the paired
drift is one signal, and treats it as direct-HIGH because audits
showed that combination is essentially always real.

---

## 2. How v2 differs from the old `content-signals` script

The older `triage-by-content-signals2.py` is the predecessor. It
scores. v2 doesn't.

### Old: numeric scoring

The old script computed:

| Indicator | Weight | Cap |
|---|---|---|
| Line ratio          | 40 / 25 / 10 / 3 by tier | — |
| Headings (H2 × 7 + H3) | per missing heading | 25 |
| Code blocks         | × 2 per missing       | 10 |
| Anchors             | × 2 per missing       | 10 |
| Untranslated paragraphs (CJK) | × 5 per paragraph | 10 |
| Missing newer versions  | × 2 per missing | 10 |

Sum the points, then bucket:

| Score | Priority |
|---|---|
| ≥ 50 | `high` |
| ≥ 20 | `medium` |
| ≥ 5  | `low` |
| < 5  | `up_to_date` |

The score is easy to explain mechanically: "this file got 47 points,
under the 50 threshold, so it's medium." But the cliff at 50 is
arbitrary, and weights can pad each other in ways that don't match
audited reality.

### v2: rule-based classification

v2 emits named indicators and runs ordered rules. No point sums, no
arbitrary thresholds, just "if X and Y, then highly_outdated."

It also adds two things the old script doesn't have:

- **Content-token paired drift** (`severe_api_and_feature_drift`) — catches
  stale-API documentation that structural signals miss.
- **Small-length-gap special case** (rule 7) — recovers
  corroborated low-priority drift in the silent 65–80% line-ratio suppresion
  without softening the broader classifier.

It removes:

- **Untranslated-paragraph indicator** (CJK only) — audited at 88%
  false-alarm rate in the older corpus; mostly fired on Markdown table
  rows, list items with link text, API endpoint signatures, and image-
  only paragraphs.
- **Structure-mismatch advisory** — fired only when a strong indicator
  had already classified the file; zero independent value.

### Compact comparison

| Property | Old `content-signals` | v2 |
|---|---|---|
| Decision model     | Numeric score → bucket          | Named indicators → ordered rules |
| Statuses / buckets | high / medium / low / up_to_date | highly_outdated / possibly_outdated / current |
| Length-ratio suppress | 4 (40 / 25 / 10 / 3 pts)        | 2 active (large, moderate) + 1 demotion-only (small) |
| Heading drift      | H2 × 7 + H3, capped at 25       | severe / moderate (no point sum) |
| Content tokens     | None                            | Paired feature-state + API/kind, direct-HIGH |
| Untranslated paras | Scored (CJK)                    | Removed — 88% false-alarm rate in audits |
| Orphan detection   | None                            | Localized files with no EN counterpart listed in a separate orphan section with an explanatory note |
| False-alarm guards | Ratio-only suppressions         | Same set + Latin translated-anchor guard |
| Why something flagged | Sum of weighted indicators   | The set of named indicators that fired |
| Easy to explain mechanically | Yes — points add up   | Yes — rules either fire or don't |
| Conservative      | Mid (point padding can promote)  | High (single token can't flag, supports must corroborate) |

The honest summary: the old script is easier to explain in one
sentence ("score over 50 = high"). v2 is easier to **audit** — every
status decision points to a specific rule and a specific set of
indicators, and the cuts below mean far fewer false positives reach
the report at all.

---

## 3. How we decided which suggested indicators to keep or cut

The v2 indicator proposal (the one that motivated the rewrite) was
explicitly **additive**: it suggested layering many new content-token
signals on top of the existing scoring machinery, feature-state,
apiVersion, kind, JSONPath, Hugo `ref` targets, port/protocol pairs,
shortcode names, CLI flags, fenced-block languages, broad English-
anchor drift, and more.

After auditing each one against real localized pages, this is where
they landed.

### Kept

**Strong structural signals**: high precision in audits, each one
unique when ablated.

- `empty_stub`
- `severe_heading_loss`
- `major_code_loss`
- `heavy_anchor_loss`
- `heavy_version_drift`

**Supporting structural signals**: each one cost audited real-drift
cases when removed; ablation showed each carries independent
classification weight.

- `large_length_gap`, `moderate_length_gap`
- `moderate_heading_loss`
- `moderate_code_loss`
- `moderate_anchor_loss`
- `moderate_version_drift`

**Feature-state / API-kind paired drift**: reintroduced as one
narrow signal (`severe_api_and_feature_drift`) after a fuller experiment
removed content tokens entirely and missed real severe drift.

### Cut as noisy

| Suggested signal | Why cut |
|---|---|
| `missing_shortcode_names`   | Loudest indicator (~820 fires); only ~6% had real classification value |
| `missing_cli_flags`         | ~250 fires, near-zero classification value |
| `missing_fence_languages`   | ~180 fires, ~2% unique value |
| `missing_ref_targets`, `missing_port_protocols` | Almost never fired meaningfully on this corpus |
| `untranslated_paras` (CJK)  | 88% false-alarm rate — fired on table rows, list-item link text, API endpoint signatures, image-only paragraphs, deprecated `includes/` snippets |
| Broad **English-anchor drift** | Detector cannot distinguish a true stale EN anchor from a language-only anchor without git history; reason text would mislead. Kept the narrower **missing explicit EN anchors** instead |
| `mild_ratio` (65–80%) and `silent_ratio` (80–90%) (as full-strength signals) | All ~330 + ~350 audited fires were possibly→current downgrades only. The 65–80% suppression survives only as the demotion-only `small_length_gap` |

### Cut as low-value / small benefit

| Suggested signal | Why cut |
|---|---|
| `small_heading_loss` (1 H3, no H2) | Removing it moved 3 files highly→possibly. They still get flagged, and cutting it collapses an entire indicator tier so the classifier is easier to explain |
| `missing_jsonpath_exprs` | 7-file impact total, no sentinel regressions. Narrow signal that didn't justify its own regex |

### Deferred — judgment call

| Signal | Trade-off |
|---|---|
| `moderate_length_gap` (50–65%) | Drop it: silences 22 borderline real-but-mild cases AND closes 2 known KO compactness false alarms. Keep it: preserves 17 audited real-drift cases at HIGH instead of softening to POSSIBLY. Genuinely a reviewer policy call, not an empirical one. v2 keeps it |

---

## 4. Why v2 still has many lines of code

| Script | Lines |
|---|---:|
| `l10n-outdatedness-triage.py` | ~912 |
| `triage-by-content-signals2.py`               | ~801 |


v2 saves **conceptual complexity**, not raw lines.

The old script's complexity lived in *weights and thresholds we have
to memorize*: what a missing H2 is worth, where the medium cliff
sits, why the H2 cap is 25, why the ratio scores 40 / 25 / 10 / 3.

v2's complexity lives in *explicit named rules and indicators*. Each
rule is a few lines. Each guard is a small function. Nothing is
weighted. Lines of code go up; cognitive load per decision goes down.

### Where the v2 lines go

| Source of code | What it covers |
|---|---|
| **Shared pipeline**           | CLI argument parsing, repo-root auto-detection, language resolution, file walking, EN ↔ l10n pairing, per-language + index report rendering. The old script also pays this. |
| **File-shape guards**         | JA long-file compactness, Latin-language compactness (with body-word ratio floor), short-EN corroboration, zh-cn H2-as-H3 mitigation, HTML-comment stripping (so zh-cn bilingual files don't fake zero-drift), Latin translated-anchor guard. Each is one small predicate, but they add up. |
| **Rule-based classifier infrastructure** | Frozen sets for strong / supporting / non-length-gap-supporting indicator buckets. Indicator gathering with ordered emission. The classifier itself with rules in order. The small-length-gap recovery rule with its corroboration check. |
| **Orphan detection / reporting** | Walk localized tree for files with no EN counterpart, skip known special basenames, collect them as a fourth category, render them in a dedicated orphan section with a short explanatory note. The status table also surfaces the orphan count alongside the three classified statuses. |
| **Content-token extraction**  | Regexes for `for_k8s_version="v1.X"`, `feature_gate_name="…"`, `apiVersion:`, `kind:`. Comment-stripping pre-pass so zh-cn bilingual EN-in-comments doesn't mask all token drift. Prefixed token names so version and gate-name spaces don't collide. Used only by the narrow `severe_api_and_feature_drift` signal. |

v2 doesn't trade fewer lines for fewer ideas. It trades **arbitrary
numeric tuning** (weights, caps, thresholds, point cliffs) for
**explicit rules and named guards**. The line count stays roughly
flat, but every decision in v2 is something we can point to in code
and explain in one sentence.


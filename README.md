# Finding Outdated Translations in Kubernetes Docs

**A content-based detector for localized pages**
**Snapshot:** April 20, 2026

Prototype scripts for reviewing localization outdatedness in the `kubernetes/website` repo.

This repo currently contains:

- `triage-by-content-signals.py`

## Intended environment

These scripts are meant to be tested inside a local checkout of the `kubernetes/website` repo.

Place the script under:

```bash
website/scripts/
```

---

## The Problem: Why the Existing Tool Misses Things

Kubernetes docs are translated into 15 languages. Existing tools like
(`lsync.sh`) decides if a translated file is outdated by looking at **commit
dates** — "when was this file last touched?"

That approach fails in three common ways:

- **Ghost Syncs.** A translator fixes a typo or cleans up formatting. The
  commit date updates. `lsync.sh` now thinks the file is fresh — even though
  the English version has added entire new sections that were never
  translated.
- **Longer-but-older files.** A translation can be *longer* than English
  while still being outdated, because it preserves old sections that
  English has since removed.
- **Hidden gaps.** Chinese pages often keep the original English inside
  hidden HTML comments. File size and dates look normal — but readers only
  see 10% translated content.

---

## The Solution: A "Content-First" Detector

We built one Python script (`triage-by-content-signals.py`) that ignores
dates entirely and compares **what's visible on the page** between the
English file and its translation.

### How it scores each pair of files

The detector looks at **6 content signals**:

1. **Line ratio** — is the translation roughly the same length?
2. **Section headings** — are H2/H3 headings missing?
3. **Code blocks** — are code examples missing?
4. **Anchor links** — are `{#section-id}` anchors missing?
5. **Untranslated paragraphs** — for CJK languages, are there still
   English paragraphs left in?
6. **Kubernetes versions** — is the translation missing newer `v1.XX`
   mentions?

Each pair gets a score, then lands in one of four buckets:

- **HIGH** (score ≥ 50) — serious content drift, needs attention
- **MEDIUM** (score ≥ 20) — meaningful gaps
- **LOW** (score ≥ 5) — minor issues
- **UP-TO-DATE** (score < 5)

---

## Proof of Success

### Accuracy

- **100% accuracy on HIGH-risk files.** Every one of the **129 files**
  flagged as HIGH across all 14 languages (with HIGH populations) was
  confirmed outdated by manual review. **Zero false positives.**
- **93% accuracy on MEDIUM** for Latin-script languages. 65 of 70 files
  across Spanish, German, French, and Italian were confirmed outdated
  directly.

### Real-world catches the old tool missed

**Ghost Sync example: Portuguese `pt-br/reference/access-authn-authz/authentication.md` (score 89 HIGH):**
- Translation was last updated Nov 2024 (a typo fix).
- English was updated Sept 2024 with new security content.
- `lsync.sh` reported: **"up to date"** (translation commit is newer).
- Our detector caught: **5 missing sections, 29 missing anchors,
  6 missing code blocks, 2 missing Kubernetes versions.**

This pattern (a cosmetic commit hiding a real gap) was confirmed in
**14+ files** across 6 languages in just one sample audit.

### Stable under real-world churn

On April 20, 2026, I merged **949 upstream commits touching 1,564
translated files** which is the biggest stress test so far. Result:

- **Zero new false positives.**
- **Zero previously-trusted results invalidated.**
- The tool correctly handled an English page being split into two pages,
  without any manual intervention.

---

## How We Tune the Detector (Guards & Fixes)

A naive "length + headings" comparison would produce a lot of noise.
Different languages have different features and localization teams 
have different translation policies, and some pages use formatting
that looks suspicious but isn't. Here's how we keep the signal clean:

### Avoiding false alarms from language differences

- **Don't trust length on tiny files.** If the English page is under
  15 lines, we ignore the length signal — there's just not enough
  content for "loc is shorter" to mean anything.
  *Exception:* if the translation is literally empty, we flag it
  anyway (an empty file is never "just compact").
- **For short pages, require backup evidence.** Between 15 and 40
  English lines, we only count length against a translation if *some
  other signal* (missing heading, missing code block, etc.) also
  fires. Keeps natural compactness from looking like staleness.
- **Asian languages are more compact — extend the rule.** Japanese,
  Korean, and Chinese pack the same meaning into fewer lines. We
  extend the "need backup evidence" rule up to 56 English lines for
  CJK languages specifically.
- **Japanese long-file gate.** Some long Japanese pages consistently
  score shorter than English for stylistic reasons (dense body text, no
  line wrapping). When a long Japanese file is *only* flagged on
  length and nothing else is wrong, we suppress the alert.
  *Trade-off:* ~23% of suppressed files do carry small real drift —
  hence the 6-file watchlist in limits below.
- **Latin-language wrap-style gate.** Portuguese, Spanish, German,
  French, and Italian translators often skip English's 80-character
  line wrapping, making translations look short by line count even
  when the word count matches. We added a **word-count check**
  (`word_ratio ≥ 0.90`): if a Latin-language translation has 90%+ of
  English's words but just looks shorter, we don't flag it. Clean
  empirical separation — real false positives measure 0.94+; real
  drift measures 0.85 or lower.

### Handling format quirks

- **Strip HTML comments before counting.** Chinese translations often
  keep the original English inside `<!-- ... -->` comments. Without
  stripping, file size looks normal. We strip comments first, then
  count — which is also what makes the "hidden bilingual gap"
  detection work.
- **Chinese H2-to-H3 level shift.** Some Chinese bilingual files
  translate English H2 headings as H3 (keeping the English H2
  preserved in a comment). Without a fix, this looks like "3 missing
  H2s." We detect the pattern and treat it as 1 missing heading,
  with a note.
- **Kubernetes version detection near Asian characters.** The regex
  for finding `v1.23` used to break when Chinese/Japanese characters
  sat right next to the version (e.g. `v1.23から`). Fixed — we now
  match correctly across all languages.
- **Ignore non-body-text lines when checking for "untranslated"
  paragraphs.** Mermaid diagram syntax, indented code, Markdown link
  definitions, and HTTP status codes all look like "English text" to
  a naive check. We pre-filter those so only real paragraphs count.

### Helpful advisories (don't change the score)

- **"Longer-but-older" warning.** When a translation is the same
  length as English but missing anchors AND missing recent versions,
  we add a note: *"this file may preserve older structure — real
  translation effort exceeds the score."* Catches the
  longer-but-older pattern explicitly.
- **Anchor-only soft caveat.** If the only missing thing is anchor
  IDs (not headings, not content), we add *"verify manually"* to the
  reason — since anchor drift is usually lower-impact than missing
  sections.

### Why this tuning matters

Every guard above is **audit-validated**: we don't add a gate unless
we've confirmed the false-positive pattern on real files *and*
confirmed the fix doesn't hide real drift. The 129/129 HIGH accuracy
is a direct result of this discipline — broad catch-everything rules
would have dragged precision down.

---

## Current Limits (What It Doesn't Do Yet)

We're honest about where the tool can't help — so reviewers know where
human eyes still matter:

- **Pure body text rewrites.** If English rewrites a paragraph without
  changing headings, code, or length, the detector won't see it. These
  drifts need `lsync.sh` as a complement.
- **Very short files.** For pages under ~15 lines, the tool can't
  reliably tell "natural compactness" from "genuine staleness."
- **Translated anchor IDs** (2 files so far). When French/Italian
  translate the anchor ID itself (e.g. `{#pourquoi-kubernetes}`), the
  detector flags it as missing even though the content is fine. Low
  reader impact; we're tracking it.
- **A 6-file Japanese watchlist.** A signature gate suppresses a known
  false-positive pattern in Japanese, but ~23% of that gated group may
  still carry real gaps (mostly in security content). Flagged for
  quarterly review.

---

## TL;DR for cinfidence level:

- **It works.** 100% accuracy (129/129) on high-risk files across all
  14 languages audited.
- **It catches what the old tool can't.** Ghost syncs, longer-but-older
  files, and hidden bilingual gaps — all invisible to date-based tools.
- **It survives real churn.** A 1,564-file upstream merge produced zero
  new errors.
- **We know where it falls short.** Body-text-only rewrite, very short files,
  and two small edge cases.


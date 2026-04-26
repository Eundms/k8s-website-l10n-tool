## What the 3-way triage results suggest

### Why compare three signals

The table below compares three different review signals:

- `content-indicators`: checks the current localized and English pages for visible signs of mismatch — large length gaps, missing H2/H3 headings, missing code blocks, missing section anchors (`{#id}`), missing newer Kubernetes version references, or drift in `apiVersion` / `kind` / feature-state shortcode tokens
- `git-history`: checks whether English changed after the localized file’s last commit, similar to existing Git-based workflows such as `kubernetes/website/scripts/lsync.sh`
- `page-lastmod`: checks whether Hugo resolves the localized page as older than the English page, following the same page-level outdatedness idea discussed in `kubernetes/website` PR #41768, which now affects Kubernetes localization pages at render time

These three signals do not answer exactly the same question.

The two existing signals, `git-history` and `page-lastmod`, are both freshness-based. They are useful because they fit naturally into current localization workflows:

- `git-history` matches the kind of upstream tracking some teams already use today
- `page-lastmod` reflects how Hugo itself can decide that a localized page is older than English at render time, which affects all localizations

By contrast, `content-indicators` is meant to complement those workflows. It does not ask whether a page was touched recently. It asks whether the current localized page shows visible signs of being out of step with the current English page.

More concretely, `content-indicators` parses both pages and compares structural and content cues — heading counts (H2 / H3), code-block counts, section anchors (`{#id}`), body-length ratio, Kubernetes `v1.X` version references, and `apiVersion` / `kind` / feature-state shortcode tokens — then classifies each page as `highly_outdated`, `possibly_outdated`, or `current` based on which indicators fire and how strongly. A pair is flagged in the table below when the localized page is classified as anything other than `current`.

That difference matters because freshness alone can be hard to interpret.

A page can look current in Git history because of a small local cleanup while still missing important upstream changes. In the other direction, English can receive formatting or maintenance edits that make every localization look stale even when there is little or nothing meaningful to update.

## 3-way localization triage — index

Generated: 2026-04-26

Signals compared: `content-indicators`, `git-history`, `page-lastmod`.

| locale | total | `all_flagged` | `content+git` | `content+lastmod` | `git+lastmod` | `content_only` | `git_only` | `lastmod_only` | `clean` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `bn` | 111 | 13 | 1 | 0 | 16 | 2 | 4 | 0 | 75 |
| `de` | 123 | 37 | 1 | 0 | 22 | 0 | 3 | 0 | 60 |
| `es` | 198 | 68 | 2 | 0 | 40 | 3 | 9 | 0 | 76 |
| `fr` | 333 | 79 | 1 | 0 | 52 | 3 | 8 | 0 | 190 |
| `hi` | 104 | 13 | 2 | 0 | 6 | 2 | 6 | 0 | 75 |
| `id` | 254 | 120 | 2 | 0 | 38 | 9 | 52 | 0 | 33 |
| `it` | 61 | 14 | 4 | 0 | 8 | 0 | 1 | 0 | 34 |
| `ja` | 568 | 112 | 2 | 0 | 79 | 45 | 6 | 2 | 322 |
| `ko` | 535 | 105 | 1 | 1 | 179 | 10 | 14 | 1 | 224 |
| `pl` | 84 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 83 |
| `pt-br` | 295 | 46 | 2 | 0 | 54 | 14 | 8 | 0 | 171 |
| `ru` | 129 | 42 | 0 | 0 | 23 | 4 | 2 | 1 | 57 |
| `uk` | 76 | 14 | 0 | 0 | 5 | 2 | 1 | 1 | 53 |
| `vi` | 204 | 20 | 0 | 0 | 11 | 5 | 14 | 0 | 154 |
| `zh-cn` | 1865 | 21 | 2 | 2 | 45 | 77 | 6 | 9 | 1703 |
| `zh-tw` | 21 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 20 |


### What the current index shows

Across most locales, the largest non-clean bucket is `git+lastmod`: both freshness-style signals agree that English is newer, while `content-indicators` stays quiet.

That pattern suggests a large class of pages where upstream English has moved ahead in Git/Hugo terms, but the localized page does not yet show obvious visible-content gaps. In practice, these are likely to include a mix of genuinely stale pages and low-impact upstream churn such as maintenance edits, formatting changes, shortcode work, or metadata updates.

The strongest review queue is the `all_flagged` bucket, where all three signals agree. These are the pages most likely to deserve immediate attention. Locales such as Japanese, Korean, Indonesian, French, and Spanish have especially large `all_flagged` buckets, which suggests a larger set of pages where visible-content mismatch and both freshness signals all point in the same direction.

The `content_only` bucket is also important. These are pages where `content-indicators` flags a likely visible mismatch even though both freshness-style signals stay quiet. This is the clearest sign of the kind of problem this prototype is trying to surface: pages that can look current from history or page-date signals, but still appear behind when comparing the current English and localized page structures. This is especially visible in locales such as Simplified Chinese and Japanese.

The disagreement buckets between `git-history` and `page-lastmod` are smaller, but they are still useful. Their presence shows that Hugo’s resolved `.Lastmod` is not identical to a simple Git-history comparison. That matters because it means the report is not just comparing two copies of the same freshness signal. It is comparing two related but distinct ways the current workflow can judge whether a page looks older than English. :contentReference[oaicite:0]{index=0}

### Why this matters for localization teams

The point of this prototype is not to replace existing workflows.

Instead, it helps show how `content-indicators` can complement the signals localization teams already encounter:

- `git-history` remains useful as a simple upstream-change signal and aligns with current practice in some teams
- `page-lastmod` matters because Hugo can use it to surface outdatedness at the page level across all localizations
- `content-indicators` adds a lightweight visible-content check that helps reviewers focus on pages that may really need attention

In that sense, this prototype helps with two practical problems:

1. It can reduce overreaction to freshness-only noise by showing where English looks newer in Git/Hugo terms but the localized page does not yet show obvious visible gaps.
2. It can rescue “ghost sync” cases where a localized page looks recently touched, but still seems visibly behind the current English page.

### Why Korean is a useful case

This is especially relevant for Korean localization.

Korean is one of the clearest examples in the current dataset of a locale where Git-based tracking alone can become hard to interpret. In the index, Korean has a large `all_flagged` bucket, a very large `git+lastmod` bucket, and a smaller but still meaningful `content_only` bucket. That mix shows all three kinds of review situations at once:

- pages where every signal agrees the page is behind
- pages where freshness signals say English is newer but visible mismatch is less obvious
- pages where visible mismatch appears even though freshness signals do not flag them

That makes Korean a useful case for evaluating whether a content-aware review helper can improve triage on top of existing Git-based workflows.

### What stands out across locales

Beyond Korean, several other locales help illustrate the different roles of the three signals.

Simplified Chinese is the clearest large-scale example of why a content-aware signal is useful. It has a very large `content_only` bucket, which suggests many pages that still show visible-content mismatch even when both freshness-based methods stay quiet. It also has the largest `lastmod_only` count, which shows that Hugo-resolved `page-lastmod` can add information that is not captured by a simple Git-history check.

Japanese is another strong example because it shows multiple review patterns at once. It has a large `all_flagged` bucket, a very large `content_only` bucket, and nonzero `lastmod_only`. That makes it a good illustration of both high-confidence outdated pages and pages that appear visibly behind even when freshness-based methods do not fully explain the difference.

Indonesian is a useful contrast case because it has both a large `all_flagged` bucket and a large `git_only` bucket. That combination shows why Git-history alone can be hard to interpret: some pages are strong outdated candidates under all three signals, while many others are only flagged by Git-history and may need more careful review before being treated as urgent.

Locales such as French, Spanish, and Portuguese also show the broader pattern of large `git+lastmod` buckets. This suggests that freshness-based methods can produce many review candidates even where visible-content mismatch is less obvious.

### Takeaway

The main takeaway from this index is that `content-indicators` does not duplicate existing freshness-based methods.

Instead, it complements them.

`git-history` and `page-lastmod` are useful because they show that English changed or now looks newer. `content-indicators` is useful because it helps answer the next review question: does the localized page also show visible signs that it may need attention?

That makes this approach useful as a lightweight triage aid for localization teams: not a replacement for human review, but a way to prioritize where review effort is likely to matter most.
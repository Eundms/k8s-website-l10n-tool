#!/usr/bin/env python3
"""File-level localization outdatedness detector.

Signals: line ratio, H2/H3 diff, code-block diff, missing anchors,
untranslated CJK paragraphs, missing newer k8s version strings.
Buckets: high (>=50) | medium (>=20) | low (>=5) | up_to_date (<5).

Usage:
    python3 scripts/triage-by-content-signals.py --lang ko [--detailed]
    python3 scripts/triage-by-content-signals.py --langs ko,zh-cn,ja
    python3 scripts/triage-by-content-signals.py --all-langs --output-dir /tmp/l10n
    python3 triage-by-content-signals.py --lang ko --repo-root /path/to/website
"""

import argparse
import datetime
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import FrozenSet, List, Tuple


# =============================================================================
# Constants
# =============================================================================

_HIGH_THRESHOLD = 50
_MEDIUM_THRESHOLD = 20
_LOW_THRESHOLD = 5

# Ratio gates. All bypassed when loc.lines == 0 (empty stub stays flagged).
_MIN_EN_VISIBLE_LINES = 15          # below: ratio always suppressed
_BORDERLINE_EN_LINES = 40           # 15-39: ratio needs corroboration
_CJK_BORDERLINE_EN_LINES = 56       # 40-55: same, CJK-only
_LATIN_COMPACTNESS_MIN_EN_LINES = 55

_CJK_LANGS: FrozenSet[str] = frozenset({"ko", "ja", "zh-cn", "zh-tw"})

# Latin scripts whose word counts behave like EN's. Excludes ru: real
# mismatches at this signature with notably lower word_ratio.
_LATIN_COMPACTNESS_LANGS: FrozenSet[str] = frozenset(
    {"pt-br", "es", "de", "fr", "it"}
)

# Sits in the empirical gap between Latin compactness FPs (>=0.94) and
# ru TPs (<=0.85) at the six-signal-zero signature.
_LATIN_COMPACTNESS_MIN_WORD_RATIO = 0.90

# ASCII-only boundaries: Python's `\b` treats CJK as word chars, so plain
# `\bv1\.\b` silently missed `v1.22以降` etc.
_VERSION_RE = re.compile(r"(?<![A-Za-z0-9])v1\.(\d{2,3})(?!\d)")

_ANCHOR_RE = re.compile(r"\{#([^}]+)\}")
_STRIP_FM = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_STRIP_CODE = re.compile(r"```.*?```", re.DOTALL)
_STRIP_CMNT = re.compile(r"<!--.*?-->", re.DOTALL)
_STRIP_INLINE = re.compile(r"`[^`\n]+`")
_WORD_RE = re.compile(r"\b[a-zA-Z]{3,}\b")

# Unicode-aware: keeps `informação` and Cyrillic as single tokens.
# Distinct from `_WORD_RE`, which must stay ASCII-only to detect
# English-only paragraphs in CJK files.
_BODY_TOKEN_RE = re.compile(r"[^\W_]{2,}", re.UNICODE)

# `201 (<a ...>): Created`-style API response lines stay English in CJK docs.
_HTTP_STATUS_RE = re.compile(r"^\d{3}\s+\(")
_MDREF_RE = re.compile(r"^\[.+?\]:\s+\S")  # [label]: URL

_MERMAID_PREFIXES: Tuple[str, ...] = (
    "classDef ", "class ", "click ",
    "flowchart", "graph ", "sequenceDiagram", "stateDiagram", "erDiagram",
    "journey", "gantt", "%%",
)


# =============================================================================
# Data classes
# =============================================================================


@dataclass(frozen=True)
class ParsedFile:
    lines: int
    h2: int
    h3: int
    code_blocks: int
    anchors: FrozenSet[str]
    versions: FrozenSet[str]
    untranslated_paras: int = 0
    body_words: int = 0  # Unicode-aware token count of visible body text


@dataclass
class FileStats:
    en_lines: int
    loc_lines: int
    line_ratio: float
    word_ratio: float          # loc/en body-word ratio; 1.0 when EN has none
    h2_diff: int
    h3_diff: int
    code_diff: int
    missing_anchors: int
    untranslated_paras: int
    missing_new_versions: int


@dataclass
class ScoreBreakdown:
    """Post-gate scores + notes; reason strings rendered separately."""
    ratio_score: int
    ratio_reason: str               # "" when minor tier or suppressed
    effective_h2_diff: int          # after zh-cn H2-as-H3 adjustment
    heading_score: int
    code_score: int
    anchor_score: int
    untranslated_score: int
    version_score: int
    only_anchor_signal: bool
    h2_as_h3_note: str
    structure_mismatch_note: str

    @property
    def total_score(self) -> int:
        return (self.ratio_score + self.heading_score + self.code_score
                + self.anchor_score + self.untranslated_score + self.version_score)


@dataclass
class FileScore:
    localized_path: str
    en_path: str
    stats: FileStats
    score: int
    priority: str
    reasons: List[str] = field(default_factory=list)


# =============================================================================
# Parsing
# =============================================================================


def _count_visible_lines(text: str) -> int:
    # Keeps code-block contents: code volume is legitimate content.
    t = _STRIP_FM.sub("", text, count=1)
    t = _STRIP_CMNT.sub("", t)
    return sum(1 for line in t.splitlines() if line.strip())


def _is_mermaid_para(para: str) -> bool:
    for line in para.splitlines():
        s = line.strip()
        if s and any(s.startswith(p) for p in _MERMAID_PREFIXES):
            return True
    return False


def _is_indented_code_block(raw_para: str) -> bool:
    # Caller must pass the pre-strip paragraph — leading indent is the signal.
    lines = raw_para.splitlines()
    non_blank = [line for line in lines if line.strip()]
    return bool(non_blank) and all(line[:4] == "    " for line in non_blank)


def _count_body_words(text: str) -> int:
    # Volume signal for the Latin compactness gate. Strips structural/code
    # noise; whatever survives counts as body text.
    t = _STRIP_FM.sub("", text, count=1)
    t = _STRIP_CMNT.sub("", t)
    t = _STRIP_CODE.sub("", t)
    kept = []
    for raw in re.split(r"\n{2,}", t):
        if _is_indented_code_block(raw):
            continue
        kept.append(raw)
    t = "\n\n".join(kept)
    t = _STRIP_INLINE.sub("", t)
    return len(_BODY_TOKEN_RE.findall(t))


def _count_untranslated_paragraphs(text: str, lang: str) -> int:
    if lang not in _CJK_LANGS:
        return 0

    text = _STRIP_FM.sub("", text, count=1)
    text = _STRIP_CODE.sub("", text)
    text = _STRIP_CMNT.sub("", text)

    count = 0
    for raw_para in re.split(r"\n{2,}", text):
        if _is_indented_code_block(raw_para):
            continue
        para = raw_para.strip()
        if len(para) < 40 or para[0] in "#{}<!-|*":
            continue
        if _is_mermaid_para(para) or _MDREF_RE.match(para) or _HTTP_STATUS_RE.match(para):
            continue
        clean = _STRIP_INLINE.sub("", para)
        non_ascii_alpha = sum(1 for c in clean if ord(c) > 127 and c.isalpha())
        ascii_words = len(_WORD_RE.findall(clean))
        if non_ascii_alpha == 0 and ascii_words >= 8:
            count += 1
    return count


def _scan_structure(text: str) -> Tuple[int, int, int, FrozenSet[str]]:
    # Strip comments first: zh-cn bilingual files hide EN fences inside
    # <!-- -->, which would desync `in_code`. Toggle on 0-3-space fences
    # (CommonMark) but only count column-0 — indented fences are noisy.
    lines = _STRIP_CMNT.sub("", text).splitlines()
    h2 = h3 = fences = 0
    anchors = set()
    in_code = False

    for line in lines:
        stripped = line.lstrip(' ')
        leading = len(line) - len(stripped)
        if leading < 4 and stripped.startswith("```"):
            if leading == 0:
                fences += 1
            in_code = not in_code
            continue
        if in_code:
            continue
        if line.startswith("### "):
            h3 += 1
        elif line.startswith("## "):
            h2 += 1
        if line.startswith("#"):
            for m in _ANCHOR_RE.finditer(line):
                anchors.add(m.group(1).strip().lower())

    return h2, h3, fences // 2, frozenset(anchors)


def parse_markdown(path: str, lang: str = "") -> ParsedFile:
    # Pass lang="" for the EN side: skips the CJK-only untranslated count.
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    h2, h3, code_blocks, anchors = _scan_structure(text)
    return ParsedFile(
        lines=_count_visible_lines(text),
        h2=h2, h3=h3, code_blocks=code_blocks,
        anchors=anchors,
        versions=frozenset(f"v1.{minor}" for minor in _VERSION_RE.findall(text)),
        untranslated_paras=_count_untranslated_paragraphs(text, lang) if lang else 0,
        body_words=_count_body_words(text),
    )


# =============================================================================
# Stats
# =============================================================================


def _version_minor(v: str) -> int:
    m = re.match(r"v1\.(\d+)", v)
    return int(m.group(1)) if m else 0


def count_missing_new_versions(en_versions: FrozenSet[str], loc_versions: FrozenSet[str]) -> int:
    if not loc_versions:
        return len(en_versions)
    loc_max = max(_version_minor(v) for v in loc_versions)
    return sum(1 for v in en_versions if _version_minor(v) > loc_max)


def compute_stats(en: ParsedFile, loc: ParsedFile, lang: str) -> FileStats:
    # Ratio capped at 2.0; 1.0 when EN is empty.
    line_ratio = min(loc.lines / en.lines, 2.0) if en.lines > 0 else 1.0
    word_ratio = (loc.body_words / en.body_words) if en.body_words > 0 else 1.0
    return FileStats(
        en_lines=en.lines,
        loc_lines=loc.lines,
        line_ratio=line_ratio,
        word_ratio=word_ratio,
        h2_diff=max(0, en.h2 - loc.h2),
        h3_diff=max(0, en.h3 - loc.h3),
        code_diff=max(0, en.code_blocks - loc.code_blocks),
        missing_anchors=len(en.anchors - loc.anchors),
        untranslated_paras=loc.untranslated_paras,
        missing_new_versions=count_missing_new_versions(en.versions, loc.versions),
    )


# =============================================================================
# Scoring — per-signal helpers
# =============================================================================
# Weights and caps are audit-tuned; the combined heading cap keeps large-TOC
# pages from dominating the score.


def _score_heading(effective_h2_diff: int, h3_diff: int) -> int:
    return min(effective_h2_diff * 7 + h3_diff, 25)


def _score_code(code_diff: int) -> int:
    return min(code_diff * 2, 10)


def _score_anchor(missing_anchors: int) -> int:
    return min(missing_anchors * 2, 10)


def _score_untranslated(untranslated_paras: int) -> int:
    return min(untranslated_paras * 5, 10)


def _score_version(missing_new_versions: int) -> int:
    return min(missing_new_versions * 2, 10)


# =============================================================================
# Scoring — bands, guards, gates
# =============================================================================


def _priority_for(score: int) -> str:
    if score >= _HIGH_THRESHOLD:
        return "high"
    if score >= _MEDIUM_THRESHOLD:
        return "medium"
    if score >= _LOW_THRESHOLD:
        return "low"
    return "up_to_date"


def _score_ratio(
    stats: FileStats, en: ParsedFile, loc: ParsedFile
) -> Tuple[int, str]:
    # `loc.lines == 0` bypasses the short-EN floor: compactness can shorten
    # lines, not erase them — empty stubs must still reach MEDIUM.
    if not (en.lines >= _MIN_EN_VISIBLE_LINES or loc.lines == 0):
        return 0, ""

    ratio = stats.line_ratio
    if ratio < 0.50:
        inv = f"{1 / ratio:.1f}×" if ratio > 0 else "∞"
        return 40, f"EN is {inv} longer than localized (line ratio {ratio:.2f})"
    if ratio < 0.65:
        return 25, f"EN significantly longer than localized (line ratio {ratio:.2f})"
    if ratio < 0.80:
        return 10, f"EN moderately longer than localized (line ratio {ratio:.2f})"
    if ratio < 0.90:
        return 3, ""   # minor tier: scored silently
    return 0, ""


def _adjust_h2_as_h3(
    stats: FileStats, en: ParsedFile, loc: ParsedFile, lang: str
) -> Tuple[int, str]:
    # zh-cn bilingual files sometimes render EN H2 as H3. Detect via H3
    # surplus covering the H2 deficit; suppress all but 1 unit. Guards
    # keep it from triggering on genuine small H2 deficits.
    if not (lang == "zh-cn"
            and stats.h2_diff >= 3
            and loc.h3 > en.h3
            and (loc.h3 - en.h3) >= stats.h2_diff
            and stats.line_ratio >= 0.70
            and stats.untranslated_paras <= 1
            and stats.code_diff <= 1):
        return stats.h2_diff, ""

    effective = min(1, stats.h2_diff)
    suppressed = stats.h2_diff - effective
    note = (
        f"heading-level note: {suppressed} of {stats.h2_diff} apparent missing "
        f"H2(s) may have been translated as H3(s) in the zh-cn file "
        f"(zh-cn has {loc.h3 - en.h3} more H3s than EN) — score reduced; verify manually"
    )
    return effective, note


def _no_other_signals(stats: FileStats) -> bool:
    # Shared signature for the compactness gates: every non-ratio signal zero.
    return (stats.h2_diff == 0
            and stats.h3_diff == 0
            and stats.code_diff == 0
            and stats.missing_anchors == 0
            and stats.untranslated_paras == 0
            and stats.missing_new_versions == 0)


def _suppress_ratio_short_file(
    ratio_score: int, en: ParsedFile, loc: ParsedFile, lang: str, support_score: int
) -> bool:
    # Ratio without heading/code/version support is suppressed for EN 15-39
    # (all locales) and 40-55 (CJK only). `loc.lines == 0` bypasses.
    if ratio_score <= 0 or loc.lines <= 0 or support_score != 0:
        return False
    return (en.lines < _BORDERLINE_EN_LINES
            or (lang in _CJK_LANGS and en.lines < _CJK_BORDERLINE_EN_LINES))


def _suppress_ratio_ja_longfile(
    ratio_score: int, stats: FileStats, en: ParsedFile, loc: ParsedFile, lang: str
) -> bool:
    # JA-only: CJK body-text compactness on long files. Cannot widen to all
    # CJK — zh-cn has real TPs at this exact signature.
    return (ratio_score >= 25
            and loc.lines > 0
            and lang == "ja"
            and en.lines >= _CJK_BORDERLINE_EN_LINES
            and _no_other_signals(stats))


def _suppress_ratio_latin_compactness(
    ratio_score: int, stats: FileStats, en: ParsedFile, loc: ParsedFile, lang: str
) -> bool:
    # word_ratio corroborator distinguishes a complete translation with
    # different line wrapping (high body-word volume) from a thinned-out one.
    return (ratio_score >= 25
            and loc.lines > 0
            and lang in _LATIN_COMPACTNESS_LANGS
            and en.lines >= _LATIN_COMPACTNESS_MIN_EN_LINES
            and _no_other_signals(stats)
            and stats.word_ratio >= _LATIN_COMPACTNESS_MIN_WORD_RATIO)


def _get_structure_mismatch_note(stats: FileStats, en: ParsedFile) -> str:
    # Advisory only (no score): anchor + version mismatch on a large file
    # whose ratio/H2/code look intact — likely preserves older structure.
    if (stats.line_ratio >= 0.80
            and stats.h2_diff == 0
            and stats.code_diff == 0
            and en.lines >= 100
            and stats.missing_anchors >= 4
            and stats.missing_new_versions >= 2):
        return (
            f"structure mismatch note: line ratio ({stats.line_ratio:.2f}), "
            "H2 counts, and code-block counts look close to EN, but both "
            "anchor mismatches and missing versions are present — localized file "
            "likely preserves older structure; actual translation effort may "
            "exceed score"
        )
    return ""


def compute_scores(
    stats: FileStats, en: ParsedFile, loc: ParsedFile, lang: str
) -> ScoreBreakdown:
    ratio_score, ratio_reason = _score_ratio(stats, en, loc)
    effective_h2_diff, h2_as_h3_note = _adjust_h2_as_h3(stats, en, loc, lang)

    heading_score = _score_heading(effective_h2_diff, stats.h3_diff)
    code_score = _score_code(stats.code_diff)
    anchor_score = _score_anchor(stats.missing_anchors)
    untranslated_score = _score_untranslated(stats.untranslated_paras)
    version_score = _score_version(stats.missing_new_versions)

    # The only-anchor-signal check uses the *pre-gate* ratio_reason — an empty
    # string here means no ratio scored OR the silent 3-pt tier scored.
    non_ratio_total = (heading_score + code_score + anchor_score
                       + untranslated_score + version_score)
    only_anchor_signal = (anchor_score > 0
                          and non_ratio_total == anchor_score
                          and not ratio_reason)

    support_score = heading_score + code_score + version_score
    if _suppress_ratio_short_file(ratio_score, en, loc, lang, support_score):
        ratio_score, ratio_reason = 0, ""
    if _suppress_ratio_ja_longfile(ratio_score, stats, en, loc, lang):
        ratio_score, ratio_reason = 0, ""
    if _suppress_ratio_latin_compactness(ratio_score, stats, en, loc, lang):
        ratio_score, ratio_reason = 0, ""

    return ScoreBreakdown(
        ratio_score=ratio_score,
        ratio_reason=ratio_reason,
        effective_h2_diff=effective_h2_diff,
        heading_score=heading_score,
        code_score=code_score,
        anchor_score=anchor_score,
        untranslated_score=untranslated_score,
        version_score=version_score,
        only_anchor_signal=only_anchor_signal,
        h2_as_h3_note=h2_as_h3_note,
        structure_mismatch_note=_get_structure_mismatch_note(stats, en),
    )


# =============================================================================
# Reason formatting
# =============================================================================


_ONLY_ANCHOR_SIGNAL_SUFFIX = (
    " (only signal — may reflect anchor naming differences, "
    "typo variants, or structure mismatch; verify manually)"
)


def format_reasons(
    stats: FileStats, breakdown: ScoreBreakdown, lang: str
) -> List[str]:
    # Order: ratio -> heading -> H2-as-H3 -> code -> anchor ->
    # untranslated -> version -> structure-mismatch.
    reasons: List[str] = []

    if breakdown.ratio_reason:
        reasons.append(breakdown.ratio_reason)

    if breakdown.heading_score:
        parts = []
        if breakdown.effective_h2_diff:
            parts.append(f"{breakdown.effective_h2_diff} H2")
        if stats.h3_diff:
            parts.append(f"{stats.h3_diff} H3")
        reasons.append(f"EN has more headings than localized ({', '.join(parts)} more)")

    if breakdown.h2_as_h3_note:
        reasons.append(breakdown.h2_as_h3_note)

    if breakdown.code_score:
        reasons.append(f"EN has {stats.code_diff} more code block(s) than localized")

    if breakdown.anchor_score:
        r = f"{stats.missing_anchors} EN section anchor(s) absent from localized file"
        if breakdown.only_anchor_signal:
            r += _ONLY_ANCHOR_SIGNAL_SUFFIX
        reasons.append(r)

    if breakdown.untranslated_score:
        reasons.append(
            f"{stats.untranslated_paras} paragraph(s) in localized file "
            f"appear untranslated (no {lang.upper()} characters)"
        )

    if breakdown.version_score:
        reasons.append(
            f"EN references {stats.missing_new_versions} Kubernetes version(s) "
            f"not found in localized file"
        )

    if breakdown.structure_mismatch_note:
        reasons.append(breakdown.structure_mismatch_note)

    return reasons


# =============================================================================
# Pipeline + scanning + reporting + CLI
# =============================================================================


def score_file_pair(en_path: str, loc_path: str, lang: str) -> FileScore:
    en = parse_markdown(en_path)
    loc = parse_markdown(loc_path, lang)
    stats = compute_stats(en, loc, lang)
    breakdown = compute_scores(stats, en, loc, lang)
    reasons = format_reasons(stats, breakdown, lang)
    return FileScore(
        localized_path=loc_path,
        en_path=en_path,
        stats=stats,
        score=breakdown.total_score,
        priority=_priority_for(breakdown.total_score),
        reasons=reasons,
    )


def scan_locale(lang: str, repo_root: str) -> List[FileScore]:
    lang_dir = os.path.join(repo_root, "content", lang)
    if not os.path.isdir(lang_dir):
        print(f"error: language directory not found: {lang_dir}", file=sys.stderr)
        sys.exit(1)

    pairs: List[Tuple[str, str]] = []
    for root, _, files in os.walk(lang_dir):
        for fname in sorted(files):
            if not fname.endswith(".md"):
                continue
            loc_path = os.path.join(root, fname)
            en_path = re.sub(r"content/[^/]+/", "content/en/", loc_path)
            if os.path.exists(en_path):
                pairs.append((en_path, loc_path))

    if not pairs:
        return []

    total = len(pairs)
    print(f"  [{lang}] scoring {total} file pairs ...", file=sys.stderr)

    scored = []
    for i, (en_path, loc_path) in enumerate(pairs, 1):
        if i % 100 == 0:
            print(f"  [{lang}] {i}/{total}", file=sys.stderr)
        scored.append(score_file_pair(en_path, loc_path, lang))

    scored.sort(key=lambda s: (-s.score, s.localized_path))
    return scored


def _rel(path: str, repo_root: str) -> str:
    try:
        return os.path.relpath(path, repo_root)
    except ValueError:
        return path


def _area(path: str, lang: str) -> str:
    m = re.search(rf"content/{re.escape(lang)}/([^/]+(?:/[^/]+)?)", path)
    if not m:
        return "(other)/"
    seg = m.group(1)
    if "." in seg.split("/")[-1]:
        seg = "/".join(seg.split("/")[:-1])
    return seg.rstrip("/") + "/"


def _bucket_counts(scored: List[FileScore]) -> Tuple[int, int, int, int]:
    c = Counter(s.priority for s in scored)
    return c["high"], c["medium"], c["low"], c["up_to_date"]


def format_report_md(
    lang: str,
    scored: List[FileScore],
    repo_root: str,
    detailed: bool,
    date: str,
) -> str:
    buckets = {name: [] for name in ("high", "medium", "low", "up_to_date")}
    for s in scored:
        buckets[s.priority].append(s)

    area_counts: Counter = Counter(
        _area(s.localized_path, lang)
        for s in scored
        if s.priority != "up_to_date"
    )

    lines: List[str] = [
        f"## Localization status (file-level): `{lang}`",
        "",
        f"Generated: {date}  ",
        "Method: content-based signals only  ",
        "Script: `scripts/triage-by-content-signals.py`",
        "",
        "| | Count |",
        "|---|---|",
        f"| Scanned pairs   | {len(scored)} |",
        f"| High priority   | {len(buckets['high'])} |",
        f"| Medium priority | {len(buckets['medium'])} |",
        f"| Low priority    | {len(buckets['low'])} |",
        f"| Up to date      | {len(buckets['up_to_date'])} |",
        "",
    ]

    if area_counts:
        lines += ["**Top affected areas:**", ""]
        for area, count in area_counts.most_common(8):
            lines.append(f"- `{area}`: {count} files")
        lines.append("")

    def add_section(title: str, items: List[FileScore], show_reasons: bool) -> None:
        lines.extend([f"### {title} ({len(items)})", ""])
        if not items:
            lines.extend(["_None_", ""])
            return

        for s in items:
            path = _rel(s.localized_path, repo_root)
            if show_reasons and s.reasons:
                lines.append(f"**`{path}`** — score {s.score}")
                lines.extend(f"- {reason}" for reason in s.reasons)
                lines.append("")
            else:
                first = s.reasons[0] if s.reasons else "(no scored signals)"
                lines.append(f"- `{path}` — score {s.score}: {first}")
        if not show_reasons:
            lines.append("")

    add_section("High priority", buckets["high"], detailed)
    add_section("Medium priority", buckets["medium"], detailed)
    add_section("Low priority", buckets["low"], False)

    return "\n".join(lines)


def format_index_md(results: List[Tuple[str, List[FileScore]]], date: str) -> str:
    lines = [
        "## Localization file-level status index",
        "",
        f"Generated: {date}",
        "",
        "| Locale | Report | Pairs | High | Medium | Low | Up to date |",
        "|---|---|---|---|---|---|---|",
    ]

    for lang, scored in results:
        high, medium, low, utd = _bucket_counts(scored)
        fname = f"l10n-{lang}.md"
        lines.append(
            f"| `{lang}` | [{fname}]({fname}) | {len(scored)} |"
            f" {high} | {medium} | {low} | {utd} |"
        )

    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "File-level localization outdatedness detector for Kubernetes docs.\n"
            "Uses lightweight content signals — no section alignment required."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--lang", metavar="CODE", help="Single locale (e.g. ko)")
    group.add_argument(
        "--langs",
        metavar="CODES",
        help="Comma-separated locales (e.g. ko,zh-cn,ja)",
    )
    group.add_argument(
        "--all-langs",
        action="store_true",
        help="All locales under content/ except en",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        metavar="DIR",
        help="Path to kubernetes/website repo root (auto-detected if omitted)",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        metavar="DIR",
        help="Directory for report files (default: .)",
    )
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Show all signal lines per file (default: one compact line)",
    )
    args = parser.parse_args()

    if args.repo_root:
        repo_root = os.path.abspath(args.repo_root)
    else:
        # Auto-detect: walk up from cwd looking for content/en
        d = os.path.abspath(os.getcwd())
        repo_root = None
        while True:
            if os.path.isdir(os.path.join(d, "content", "en")):
                repo_root = d
                break
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        if repo_root is None:
            print(
                "error: could not auto-detect repo root (no content/en found "
                "above cwd). Use --repo-root to specify it explicitly.",
                file=sys.stderr,
            )
            sys.exit(1)
    date = datetime.date.today().isoformat()

    if args.lang:
        langs = [args.lang]
    elif args.langs:
        langs = [lang.strip() for lang in args.langs.split(",") if lang.strip()]
    else:
        content_dir = os.path.join(repo_root, "content")
        langs = sorted(
            d for d in os.listdir(content_dir)
            if os.path.isdir(os.path.join(content_dir, d)) and d != "en"
        )

    os.makedirs(args.output_dir, exist_ok=True)
    all_results: List[Tuple[str, List[FileScore]]] = []

    for lang in langs:
        print(f"Scanning content/{lang}/ ...", file=sys.stderr)
        scored = scan_locale(lang, repo_root)
        all_results.append((lang, scored))

        out_path = os.path.join(args.output_dir, f"l10n-outdated-report-{lang}.md")
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(format_report_md(lang, scored, repo_root, args.detailed, date))

        high, medium, low, utd = _bucket_counts(scored)
        print(
            f"Wrote {out_path}  "
            f"({high} high, {medium} medium, {low} low, {utd} up-to-date)",
            file=sys.stderr,
        )

    if len(langs) > 1:
        index_path = os.path.join(args.output_dir, "l10n-outdated-report-index.md")
        with open(index_path, "w", encoding="utf-8") as fh:
            fh.write(format_index_md(all_results, date))
        print(f"Wrote {index_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Localization outdatedness detector.

Compares each localized Markdown page against its English source using
content-based indicators (length gap, missing headings/code/anchors,
version mismatch, API/feature-state token mismatch) and classifies it as
`highly_outdated`, `possibly_outdated`, or `up_to_date`.

Usage:
    # Inside the kubernetes/website repo
    python3 scripts/l10n-outdatedness-triage.py                            # all locales (default)
    python3 scripts/l10n-outdatedness-triage.py --lang ko
    python3 scripts/l10n-outdatedness-triage.py --lang ko zh-cn ja
    python3 scripts/l10n-outdatedness-triage.py --lang ko --verbose --output-dir /tmp/l10n

    # Outside the repo (relative or absolute path to the repo root)
    python3 l10n-outdatedness-triage.py --repo-root ../website
"""

import argparse
import datetime
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

# --- Constants ---
_LENGTH_GAP_MIN_EN_LINES = 15
_LENGTH_GAP_REQUIRES_SUPPORT_BELOW_EN_LINES = 40
_CJK_LENGTH_GAP_REQUIRES_SUPPORT_BELOW_EN_LINES = 56
_COMPACTNESS_CHECK_MIN_EN_LINES = 55

# Empirical gap: Latin false alarms (>=0.94) vs ru drift (<=0.85) at clean shape.
_FULL_TRANSLATION_BODY_WORD_RATIO_MIN = 0.90

_LENGTH_GAP_NONE = ""
_LENGTH_GAP_SILENT = "silent"
_LENGTH_GAP_SMALL = "small"
_LENGTH_GAP_MODERATE = "moderate"
_LENGTH_GAP_LARGE = "large"

_CJK_LOCALES: FrozenSet[str] = frozenset({"ko", "ja", "zh-cn", "zh-tw"})

# Latin-script locales whose word counts behave like EN's. Excludes ru:
# real drift at the same pattern sits at notably lower body_word_ratio.
_LATIN_COMPACTNESS_LOCALES: FrozenSet[str] = frozenset(
    {"pt-br", "es", "de", "fr", "it"}
)

# ASCII-only boundary — Python's `\b` treats CJK as word chars, so plain
# `\bv1\.\b` silently missed `v1.22以降` etc.
_VERSION_RE = re.compile(r"(?<![A-Za-z0-9])v1\.(\d{2,3})(?!\d)")
_ANCHOR_RE = re.compile(r"\{#([^}]+)\}")
_STRIP_FM = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_STRIP_CODE = re.compile(r"```.*?```", re.DOTALL)
_STRIP_CMNT = re.compile(r"<!--.*?-->", re.DOTALL)
_STRIP_INLINE = re.compile(r"`[^`\n]+`")

# Unicode-aware body-word counter for the Latin-compactness check; keeps
# `informação` and Cyrillic words intact.
_BODY_WORD_RE = re.compile(r"[^\W_]{2,}", re.UNICODE)

_FS_VERSION_RE = re.compile(r'for_k8s_version="(v\d+\.\d+)"')
_FS_GATE_RE = re.compile(r'feature_gate_name="([A-Za-z_][A-Za-z0-9_]*)"')
_APIVERSION_LINE_RE = re.compile(
    r"^[ \t]*-?[ \t]*apiVersion:\s*\"?([A-Za-z0-9./_-]+)\"?\s*$", re.MULTILINE
)
_KIND_LINE_RE = re.compile(
    r"^[ \t]*-?[ \t]*kind:\s*\"?([A-Z][A-Za-z0-9]+)\"?\s*$", re.MULTILINE
)

_ORPHAN_SKIP_BASENAMES: FrozenSet[str] = frozenset({
    "README.md", "_search.md", "sitemap.md", "_redirects",
})
_ORPHAN_REASON = (
    "No English source found; verify whether this file was renamed/removed "
    "upstream or is intentionally locale-specific."
)

STATUS_HIGHLY_OUTDATED = "highly_outdated"
STATUS_POSSIBLY_OUTDATED = "possibly_outdated"
STATUS_CURRENT = "up_to_date"

_STRONG_H2_THRESHOLD = 2
_STRONG_H3_WITH_H2_THRESHOLD = 5
_STRONG_CODE_THRESHOLD = 3
_STRONG_ANCHOR_THRESHOLD = 5
_STRONG_VERSION_THRESHOLD = 3

_STRONG_INDICATORS: FrozenSet[str] = frozenset({
    "empty_stub", "severe_heading_loss", "severe_code_loss",
    "severe_anchor_loss", "severe_version_mismatch",
})
_SUPPORTING_INDICATORS: FrozenSet[str] = frozenset({
    "large_length_gap", "moderate_length_gap",
    "moderate_heading_loss", "moderate_code_loss",
    "moderate_anchor_loss", "moderate_version_mismatch",
})
# `small_length_gap` is excluded from `_SUPPORTING_INDICATORS`: it only
# triggers the dedicated possibly_outdated rule, never pairs with strong
# indicators or pads ≥3-supporting / large_length_gap promotions.
_LENGTH_GAP_INDICATORS: FrozenSet[str] = frozenset(
    {"large_length_gap", "moderate_length_gap", "small_length_gap"}
)
# Supporting set for `small_length_gap` emission. `gather_indicators` also
# accepts raw `missing_feature_state` / `missing_api_or_kind` token counts
# as evidence (gate-only — never emitted as indicators).
_NON_LENGTH_INDICATORS: FrozenSet[str] = frozenset({
    "moderate_heading_loss", "severe_heading_loss",
    "moderate_code_loss", "severe_code_loss",
    "moderate_anchor_loss", "severe_anchor_loss",
    "moderate_version_mismatch", "severe_version_mismatch",
    "severe_api_and_feature_mismatch",
})

_STATUS_SORT_KEY = {
    STATUS_HIGHLY_OUTDATED: 0,
    STATUS_POSSIBLY_OUTDATED: 1,
    STATUS_CURRENT: 2,
}

# --- Data classes ---

@dataclass(frozen=True)
class ParsedFile:
    visible_lines: int
    h2: int
    h3: int
    code_blocks: int
    anchors: FrozenSet[str]
    versions: FrozenSet[str]
    body_words: int = 0
    feature_state_tokens: FrozenSet[str] = frozenset()
    api_kind_tokens: FrozenSet[str] = frozenset()


@dataclass
class FileStats:
    l10n_to_en_line_ratio: float
    l10n_to_en_body_word_ratio: float
    missing_h2: int
    missing_h3: int
    missing_code_blocks: int
    missing_anchors: int
    missing_new_versions: int
    missing_feature_state: int = 0
    missing_api_or_kind: int = 0

@dataclass
class FileIndicatorSummary:
    length_gap_level: str
    length_gap_reason: str
    effective_missing_h2: int
    only_anchor_indicator: bool
    h2_as_h3_note: str

@dataclass
class FileReport:
    localized_path: str
    status: str
    reasons: List[str] = field(default_factory=list)
    indicators: List[str] = field(default_factory=list)

# --- Parsing ---

def _is_indented_code_block(raw_para: str) -> bool:
    lines = raw_para.splitlines()
    non_blank = [line for line in lines if line.strip()]
    return bool(non_blank) and all(line[:4] == "    " for line in non_blank)

def _count_visible_lines(text: str) -> int:
    # Code-block contents kept: code volume is legitimate content.
    t = _STRIP_FM.sub("", text, count=1)
    t = _STRIP_CMNT.sub("", t)
    return sum(1 for line in t.splitlines() if line.strip())

def _count_body_words(text: str) -> int:
    # For the Latin-compactness check: separates loose-wrapped full
    # translations from thinned content.
    t = _STRIP_FM.sub("", text, count=1)
    t = _STRIP_CMNT.sub("", t)
    t = _STRIP_CODE.sub("", t)
    kept: List[str] = []
    for raw in re.split(r"\n{2,}", t):
        if _is_indented_code_block(raw):
            continue
        kept.append(raw)
    t = _STRIP_INLINE.sub("", "\n\n".join(kept))
    return len(_BODY_WORD_RE.findall(t))

def _scan_structure(text: str) -> Tuple[int, int, int, FrozenSet[str]]:
    # Strip comments first: zh-cn `<!-- -->` blocks would desync `in_code`.
    # Toggle on 0-3-space fences (CommonMark); only count column-0 fences.
    lines = _STRIP_CMNT.sub("", text).splitlines()
    h2 = h3 = fences = 0
    anchors: set = set()
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

def _extract_feature_state_tokens(text: str) -> FrozenSet[str]:
    # Strip comments first: zh-cn `<!-- -->` blocks would mask token drift.
    # Prefixes keep version/gate name spaces from colliding.
    no_cmt = _STRIP_CMNT.sub("", text)
    tokens = [f"version:{v}" for v in _FS_VERSION_RE.findall(no_cmt)]
    tokens.extend(f"gate:{g}" for g in _FS_GATE_RE.findall(no_cmt))
    return frozenset(tokens)

def _extract_api_kind_tokens(text: str) -> FrozenSet[str]:
    # Same comment-stripping rationale as feature_state. Prefixes keep
    # apiVersion and kind value spaces separate.
    no_cmt = _STRIP_CMNT.sub("", text)
    tokens = [f"api:{v}" for v in _APIVERSION_LINE_RE.findall(no_cmt)]
    tokens.extend(f"kind:{k}" for k in _KIND_LINE_RE.findall(no_cmt))
    return frozenset(tokens)

def parse_markdown(path: str, locale: str = "") -> ParsedFile:
    # `locale` is retained for call-site stability; no longer consumed.
    del locale
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    h2, h3, code_blocks, anchors = _scan_structure(text)
    return ParsedFile(
        visible_lines=_count_visible_lines(text),
        h2=h2, h3=h3, code_blocks=code_blocks,
        anchors=anchors,
        versions=frozenset(f"v1.{minor}" for minor in _VERSION_RE.findall(text)),
        body_words=_count_body_words(text),
        feature_state_tokens=_extract_feature_state_tokens(text),
        api_kind_tokens=_extract_api_kind_tokens(text),
    )

# --- Stats ---

def _version_minor(v: str) -> int:
    m = re.match(r"v1\.(\d+)", v)
    return int(m.group(1)) if m else 0

def _count_missing_new_versions(
    en_versions: FrozenSet[str], l10n_versions: FrozenSet[str],
) -> int:
    if not l10n_versions:
        return len(en_versions)
    l10n_max = max(_version_minor(v) for v in l10n_versions)
    return sum(1 for v in en_versions if _version_minor(v) > l10n_max)

def compute_stats(en: ParsedFile, l10n: ParsedFile) -> FileStats:
    line_ratio = (
        min(l10n.visible_lines / en.visible_lines, 2.0)
        if en.visible_lines > 0 else 1.0
    )
    body_word_ratio = (
        l10n.body_words / en.body_words if en.body_words > 0 else 1.0
    )

    if l10n.visible_lines > 0:
        missing_feature_state = len(en.feature_state_tokens - l10n.feature_state_tokens)
        missing_api_or_kind = len(en.api_kind_tokens - l10n.api_kind_tokens)
    else:
        missing_feature_state = 0
        missing_api_or_kind = 0

    return FileStats(
        l10n_to_en_line_ratio=line_ratio,
        l10n_to_en_body_word_ratio=body_word_ratio,
        missing_h2=max(0, en.h2 - l10n.h2),
        missing_h3=max(0, en.h3 - l10n.h3),
        missing_code_blocks=max(0, en.code_blocks - l10n.code_blocks),
        missing_anchors=len(en.anchors - l10n.anchors),
        missing_new_versions=_count_missing_new_versions(en.versions, l10n.versions),
        missing_feature_state=missing_feature_state,
        missing_api_or_kind=missing_api_or_kind,
    )

# --- Indicators and suppression checks ---

def _classify_length_gap(l10n_to_en_line_ratio: float) -> str:
    if l10n_to_en_line_ratio < 0.50:
        return _LENGTH_GAP_LARGE
    if l10n_to_en_line_ratio < 0.65:
        return _LENGTH_GAP_MODERATE
    if l10n_to_en_line_ratio < 0.80:
        return _LENGTH_GAP_SMALL
    if l10n_to_en_line_ratio < 0.90:
        return _LENGTH_GAP_SILENT
    return _LENGTH_GAP_NONE

def _length_gap_reason(level: str, l10n_to_en_line_ratio: float) -> str:
    if level == _LENGTH_GAP_LARGE:
        inv = (
            f"{1 / l10n_to_en_line_ratio:.1f}×"
            if l10n_to_en_line_ratio > 0 else "∞"
        )
        return (
            f"Localized file is {inv} shorter than EN"
            f"(l10n-to-EN line ratio {l10n_to_en_line_ratio:.2f})"
        )
    if level == _LENGTH_GAP_MODERATE:
        return (
            f"Localized file is substantially shorter than EN"
            f"(l10n-to-EN line ratio {l10n_to_en_line_ratio:.2f})"
        )
    if level == _LENGTH_GAP_SMALL:
        return (
            f"Localized file is moderately shorter than EN"
            f"(l10n-to-EN line ratio {l10n_to_en_line_ratio:.2f})"
        )
    return ""

def _adjust_h2_as_h3(
    stats: FileStats, en: ParsedFile, l10n: ParsedFile, locale: str,
) -> Tuple[int, str]:
    # zh-cn bilingual files sometimes render source H2 as H3 (source heading
    # kept in a comment). When H3 surplus covers the H2 deficit, count as 1
    # missing H2; predicates guard against genuine small H2 deficits.
    if not (locale == "zh-cn"
            and stats.missing_h2 >= 3
            and l10n.h3 > en.h3
            and (l10n.h3 - en.h3) >= stats.missing_h2
            and stats.l10n_to_en_line_ratio >= 0.70
            and stats.missing_code_blocks <= 1):
        return stats.missing_h2, ""
    effective = min(1, stats.missing_h2)
    suppressed = stats.missing_h2 - effective
    note = (
        f"Possible heading-level mismatch: {suppressed} of {stats.missing_h2} "
        f"apparent missing H2(s) may have been translated as H3(s) in the "
        f"zh-cn file (zh-cn has {l10n.h3 - en.h3} more H3s than source) — "
        "counted as 1 H2; verify manually"
    )
    return effective, note

def _has_only_length_gap_indicator(stats: FileStats) -> bool:
    return (stats.missing_h2 == 0
            and stats.missing_h3 == 0
            and stats.missing_code_blocks == 0
            and stats.missing_anchors == 0
            and stats.missing_new_versions == 0)

def _should_suppress_length_gap_short_en(
    en: ParsedFile, l10n: ParsedFile, locale: str,
    level: str, has_support: bool,
) -> bool:
    """Drop length gap when EN<40 (or <56 for CJK) and no heading/code/
    version indicator backs up the mismatch."""
    if level == _LENGTH_GAP_NONE or l10n.visible_lines == 0 or has_support:
        return False
    return (en.visible_lines < _LENGTH_GAP_REQUIRES_SUPPORT_BELOW_EN_LINES
            or (locale in _CJK_LOCALES
                and en.visible_lines < _CJK_LENGTH_GAP_REQUIRES_SUPPORT_BELOW_EN_LINES))

def _should_suppress_length_gap_ja_only(
    stats: FileStats, en: ParsedFile, l10n: ParsedFile,
    locale: str, level: str,
) -> bool:
    """JA-only override: drop length gap when no other indicator fires.
    Empirically a false alarm in JA; zh-cn has real drift at this shape."""
    return (level in (_LENGTH_GAP_MODERATE, _LENGTH_GAP_LARGE)
            and l10n.visible_lines > 0
            and locale == "ja"
            and en.visible_lines >= _CJK_LENGTH_GAP_REQUIRES_SUPPORT_BELOW_EN_LINES
            and _has_only_length_gap_indicator(stats))

def _should_suppress_length_gap_latin_compactness(
    stats: FileStats, en: ParsedFile, l10n: ParsedFile,
    locale: str, level: str,
) -> bool:
    """Latin override: drop length gap when body word volume matches a
    full translation. The 0.90 floor separates loose-wrapped translations
    (>=0.94) from ru drift (<=0.85)."""
    return (level in (_LENGTH_GAP_MODERATE, _LENGTH_GAP_LARGE)
            and l10n.visible_lines > 0
            and locale in _LATIN_COMPACTNESS_LOCALES
            and en.visible_lines >= _COMPACTNESS_CHECK_MIN_EN_LINES
            and _has_only_length_gap_indicator(stats)
            and stats.l10n_to_en_body_word_ratio >= _FULL_TRANSLATION_BODY_WORD_RATIO_MIN)

def analyze_file_indicators(
    stats: FileStats, en: ParsedFile, l10n: ParsedFile, locale: str,
) -> FileIndicatorSummary:
    # Empty-stub bypass on the short-EN floor so empty stubs still reach a level.
    if en.visible_lines >= _LENGTH_GAP_MIN_EN_LINES or l10n.visible_lines == 0:
        level = _classify_length_gap(stats.l10n_to_en_line_ratio)
        length_gap_reason = _length_gap_reason(level, stats.l10n_to_en_line_ratio)
    else:
        level, length_gap_reason = _LENGTH_GAP_NONE, ""

    eff_h2, h2_as_h3_note = _adjust_h2_as_h3(stats, en, l10n, locale)

    has_support = (
        eff_h2 > 0 or stats.missing_h3 > 0
        or stats.missing_code_blocks > 0 or stats.missing_new_versions > 0
    )

    only_anchor_indicator = (
        stats.missing_anchors >= 1
        and eff_h2 == 0 and stats.missing_h3 == 0
        and stats.missing_code_blocks == 0
        and stats.missing_new_versions == 0
        and not length_gap_reason
    )

    if _should_suppress_length_gap_short_en(en, l10n, locale, level, has_support):
        level, length_gap_reason = _LENGTH_GAP_NONE, ""

    if _should_suppress_length_gap_ja_only(stats, en, l10n, locale, level):
        level, length_gap_reason = _LENGTH_GAP_NONE, ""

    if _should_suppress_length_gap_latin_compactness(stats, en, l10n, locale, level):
        level, length_gap_reason = _LENGTH_GAP_NONE, ""

    return FileIndicatorSummary(
        length_gap_level=level,
        length_gap_reason=length_gap_reason,
        effective_missing_h2=eff_h2,
        only_anchor_indicator=only_anchor_indicator,
        h2_as_h3_note=h2_as_h3_note,
    )

# --- Reason rendering ---

_ONLY_ANCHOR_INDICATOR_SUFFIX = (
    " (only indicator — may reflect anchor naming differences, "
    "typo variants, or structure mismatch; verify manually)"
)

_FALLBACK_REASON = "Content indicators suggest localized file may be outdated"

def format_file_reasons(
    stats: FileStats, summary: FileIndicatorSummary,
) -> List[str]:
    reasons: List[str] = []
    if summary.length_gap_reason:
        reasons.append(summary.length_gap_reason)

    if summary.effective_missing_h2 > 0 or stats.missing_h3 > 0:
        parts = []
        if summary.effective_missing_h2:
            parts.append(f"{summary.effective_missing_h2} H2")
        if stats.missing_h3:
            parts.append(f"{stats.missing_h3} H3")
        reasons.append(
            f"Localized file is missing headings present in source ({', '.join(parts)})"
        )

    if summary.h2_as_h3_note:
        reasons.append(summary.h2_as_h3_note)

    if stats.missing_code_blocks:
        reasons.append(
            f"Localized file is missing {stats.missing_code_blocks} "
            f"code block(s) present in source"
        )

    if stats.missing_anchors:
        r = (
            f"Localized file is missing {stats.missing_anchors} "
            f"section anchor(s) present in source"
        )
        if summary.only_anchor_indicator:
            r += _ONLY_ANCHOR_INDICATOR_SUFFIX
        reasons.append(r)

    if stats.missing_new_versions:
        reasons.append(
            f"Localized file is missing {stats.missing_new_versions} "
            f"Kubernetes version reference(s) present in source"
        )

    if stats.missing_feature_state and stats.missing_api_or_kind:
        reasons.append(
            "Content mismatch: feature-state AND apiVersion/kind value(s) "
            "present in source are missing from localized"
        )

    return reasons or [_FALLBACK_REASON]

# --- Indicator gathering + classifier ---

def gather_indicators(
    stats: FileStats, summary: FileIndicatorSummary,
    en: ParsedFile, l10n: ParsedFile,
) -> List[str]:
    indicators: List[str] = []

    if l10n.visible_lines == 0 and en.visible_lines >= 1:
        indicators.append("empty_stub")

    if (summary.length_gap_level == _LENGTH_GAP_LARGE
            and l10n.visible_lines > 0):
        indicators.append("large_length_gap")
    elif summary.length_gap_level == _LENGTH_GAP_MODERATE:
        indicators.append("moderate_length_gap")

    # Use the H2-as-H3-adjusted count so zh-cn level-shift files aren't
    # over-promoted to a strong indicator.
    eff_h2 = summary.effective_missing_h2
    if (eff_h2 >= _STRONG_H2_THRESHOLD
            or (eff_h2 >= 1
                and stats.missing_h3 >= _STRONG_H3_WITH_H2_THRESHOLD)):
        indicators.append("severe_heading_loss")
    elif eff_h2 >= 1 or stats.missing_h3 >= 2:
        indicators.append("moderate_heading_loss")

    if stats.missing_code_blocks >= _STRONG_CODE_THRESHOLD:
        indicators.append("severe_code_loss")
    elif stats.missing_code_blocks >= 1:
        indicators.append("moderate_code_loss")

    if stats.missing_anchors >= _STRONG_ANCHOR_THRESHOLD:
        indicators.append("severe_anchor_loss")
    elif stats.missing_anchors >= 1:
        indicators.append("moderate_anchor_loss")

    if stats.missing_new_versions >= _STRONG_VERSION_THRESHOLD:
        indicators.append("severe_version_mismatch")
    elif stats.missing_new_versions >= 1:
        indicators.append("moderate_version_mismatch")

    if stats.missing_feature_state and stats.missing_api_or_kind:
        indicators.append("severe_api_and_feature_mismatch")

    # Emit small_length_gap only with a non-length supporting indicator
    # or raw missing_feature_state / missing_api_or_kind (gate-only).
    if (summary.length_gap_level == _LENGTH_GAP_SMALL
            and l10n.visible_lines > 0
            and (any(ind in _NON_LENGTH_INDICATORS for ind in indicators)
                 or stats.missing_feature_state > 0
                 or stats.missing_api_or_kind > 0)):
        indicators.append("small_length_gap")

    return indicators

def _is_latin_translated_anchor_false_alarm(
    non_length_gap_supporting: Set[str], locale: str,
    l10n_to_en_body_word_ratio: float, missing_anchors: int,
) -> bool:
    # Some Latin locales translate anchor IDs instead of preserving the
    # source identifier — looks compact with a small anchor mismatch but
    # carries full word volume. Without this guard, rule 4 falsely promotes them.
    return (
        locale in _LATIN_COMPACTNESS_LOCALES
        and l10n_to_en_body_word_ratio >= _FULL_TRANSLATION_BODY_WORD_RATIO_MIN
        and missing_anchors <= 2
        and len(non_length_gap_supporting) >= 1
        and all(s == "moderate_anchor_loss" for s in non_length_gap_supporting)
    )

def classify_file_status(
    indicators: List[str], *,
    locale: str, l10n_to_en_body_word_ratio: float, missing_anchors: int,
) -> str:
    """Classification rules, in order:

    1. `empty_stub` or `severe_api_and_feature_mismatch` → highly_outdated.
    2. ≥2 strong → highly_outdated.
    3. ≥1 strong + ≥1 supporting → highly_outdated.
    4. `large_length_gap` + ≥1 non-length-gap supporting → highly_outdated
       (guarded against the Latin translated-anchor false-alarm).
    5. ≥3 supporting → highly_outdated.
    6. ≥1 strong or ≥1 supporting → possibly_outdated.
    7. `small_length_gap` → possibly_outdated.
    8. Otherwise → up_to_date.
    """
    if not indicators:
        return STATUS_CURRENT

    indset = set(indicators)
    strong = indset & _STRONG_INDICATORS
    supporting = indset & _SUPPORTING_INDICATORS

    if "empty_stub" in indset:
        return STATUS_HIGHLY_OUTDATED
    if "severe_api_and_feature_mismatch" in indset:
        return STATUS_HIGHLY_OUTDATED
    if len(strong) >= 2:
        return STATUS_HIGHLY_OUTDATED
    if strong and supporting:
        return STATUS_HIGHLY_OUTDATED
    if "large_length_gap" in indset:
        non_length_gap = supporting - _LENGTH_GAP_INDICATORS
        if non_length_gap and not _is_latin_translated_anchor_false_alarm(
                non_length_gap, locale,
                l10n_to_en_body_word_ratio, missing_anchors):
            return STATUS_HIGHLY_OUTDATED
    if len(supporting) >= 3:
        return STATUS_HIGHLY_OUTDATED
    if strong or supporting:
        return STATUS_POSSIBLY_OUTDATED
    if "small_length_gap" in indset:
        return STATUS_POSSIBLY_OUTDATED
    return STATUS_CURRENT

# --- Report formatting ---

def _rel(path: str, repo_root: str) -> str:
    try:
        return os.path.relpath(path, repo_root)
    except ValueError:
        return path

def _doc_area(path: str, locale: str) -> str:
    m = re.search(rf"content/{re.escape(locale)}/([^/]+(?:/[^/]+)?)", path)
    if not m:
        return "(other)/"
    seg = m.group(1)
    if "." in seg.split("/")[-1]:
        seg = "/".join(seg.split("/")[:-1])
    return seg.rstrip("/") + "/"

def count_files_by_status(
    evaluated: List[FileReport],
) -> Tuple[int, int, int]:
    c = Counter(fr.status for fr in evaluated)
    return (
        c[STATUS_HIGHLY_OUTDATED],
        c[STATUS_POSSIBLY_OUTDATED],
        c[STATUS_CURRENT],
    )

def build_locale_report(
    locale: str,
    evaluated: List[FileReport],
    orphans: List[str],
    repo_root: str,
    date: str,
    detailed: bool,
) -> str:
    hi, poss, curr = count_files_by_status(evaluated)
    lines: List[str] = [
        f"## Localization status: `{locale}`",
        "",
        f"Generated: {date}  ",
        "Method: content-based indicators only  ",
        "Script: `l10n-outdatedness-triage.py`",
        "",
        "| Status | Count |",
        "|---|---:|",
        f"| Evaluated localized files | {len(evaluated)} |",
        f"| highly_outdated   | {hi} |",
        f"| possibly_outdated | {poss} |",
        f"| up_to_date        | {curr} |",
        f"| Orphans (no source) | {len(orphans)} |",
        "",
    ]

    area_counts: Counter = Counter(
        _doc_area(fr.localized_path, locale)
        for fr in evaluated
        if fr.status != STATUS_CURRENT
    )
    if area_counts:
        lines.extend(["**Top affected areas (flagged files):**", ""])
        for area, count in area_counts.most_common(8):
            lines.append(f"- `{area}`: {count} files")
        lines.append("")

    by_status: Dict[str, List[FileReport]] = {
        STATUS_HIGHLY_OUTDATED: [],
        STATUS_POSSIBLY_OUTDATED: [],
        STATUS_CURRENT: [],
    }
    for fr in evaluated:
        by_status[fr.status].append(fr)

    for title, key in (
        ("Highly outdated", STATUS_HIGHLY_OUTDATED),
        ("Possibly outdated", STATUS_POSSIBLY_OUTDATED),
    ):
        items = by_status[key]
        lines.extend([f"### {title} ({len(items)})", ""])
        if not items:
            lines.extend(["_None_", ""])
            continue
        for fr in items:
            path = _rel(fr.localized_path, repo_root)
            if detailed and fr.reasons:
                lines.append(f"**`{path}`** — status: {fr.status}")
                lines.extend(f"- {r}" for r in fr.reasons)
                if fr.indicators:
                    lines.append(f"- Indicators: {', '.join(fr.indicators)}")
                lines.append("")
            else:
                first = fr.reasons[0] if fr.reasons else "(no indicators)"
                lines.append(f"- `{path}` — {fr.status}: {first}")
        if not detailed:
            lines.append("")

    lines.extend([
        f"### Orphan localized files, no English source ({len(orphans)})",
        "",
    ])
    if orphans:
        lines.append(f"_{_ORPHAN_REASON}_")
        lines.append("")
        for path in orphans:
            lines.append(f"- `{_rel(path, repo_root)}`")
        lines.append("")
    else:
        lines.extend(["_None_", ""])

    return "\n".join(lines)

def build_index_report(
    results: List[Tuple[str, List[FileReport], List[str]]], date: str,
) -> str:
    lines = [
        "## Localization status index",
        "",
        f"Generated: {date}",
        "",
        "| Locale | Report | Files | highly_outdated | possibly_outdated | up_to_date | Orphans |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for locale, evaluated, orphans in results:
        hi, poss, curr = count_files_by_status(evaluated)
        fname = f"l10n-indicators-{locale}.md"
        lines.append(
            f"| `{locale}` | [{fname}]({fname}) | {len(evaluated)} |"
            f" {hi} | {poss} | {curr} | {len(orphans)} |"
        )
    lines.append("")
    return "\n".join(lines)

# --- CLI / orchestration ---

def evaluate_file_pair(
    en_path: str, l10n_path: str, locale: str,
) -> FileReport:
    en = parse_markdown(en_path)
    l10n = parse_markdown(l10n_path, locale)
    stats = compute_stats(en, l10n)
    summary = analyze_file_indicators(stats, en, l10n, locale)
    indicators = gather_indicators(stats, summary, en, l10n)
    status = classify_file_status(
        indicators,
        locale=locale,
        l10n_to_en_body_word_ratio=stats.l10n_to_en_body_word_ratio,
        missing_anchors=stats.missing_anchors,
    )
    reasons = (
        format_file_reasons(stats, summary)
        if status != STATUS_CURRENT else []
    )
    return FileReport(
        localized_path=l10n_path,
        status=status,
        reasons=reasons,
        indicators=indicators,
    )

def _is_candidate_orphan(l10n_path: str, locale: str) -> bool:
    fname = os.path.basename(l10n_path)
    if fname in _ORPHAN_SKIP_BASENAMES:
        return False
    rel = l10n_path.split(f"content/{locale}/", 1)[-1]
    return rel.startswith("docs/")

def scan_locale(
    locale: str, repo_root: str,
) -> Tuple[List[FileReport], List[str]]:
    locale_dir = os.path.join(repo_root, "content", locale)
    if not os.path.isdir(locale_dir):
        print(f"error: locale directory not found: {locale_dir}", file=sys.stderr)
        sys.exit(1)

    pairs: List[Tuple[str, str]] = []
    orphan_paths: List[str] = []
    for root, _, files in os.walk(locale_dir):
        for fname in sorted(files):
            if not fname.endswith(".md"):
                continue
            l10n_path = os.path.join(root, fname)
            en_path = re.sub(r"content/[^/]+/", "content/en/", l10n_path)
            if os.path.exists(en_path):
                pairs.append((en_path, l10n_path))
            elif _is_candidate_orphan(l10n_path, locale):
                orphan_paths.append(l10n_path)

    reports: List[FileReport] = []

    total = len(pairs)
    if total:
        print(f"  [{locale}] evaluating {total} file pairs ...", file=sys.stderr)
    for i, (en_path, l10n_path) in enumerate(pairs, 1):
        if i % 100 == 0:
            print(f"  [{locale}] {i}/{total}", file=sys.stderr)
        reports.append(evaluate_file_pair(en_path, l10n_path, locale))

    reports.sort(
        key=lambda fr: (_STATUS_SORT_KEY[fr.status], fr.localized_path)
    )
    orphan_paths.sort()
    return reports, orphan_paths

def _auto_detect_repo_root() -> Optional[str]:
    d = os.path.abspath(os.getcwd())
    while True:
        if os.path.isdir(os.path.join(d, "content", "en")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent

def _resolve_locales(args: argparse.Namespace, repo_root: str) -> List[str]:
    if args.lang:
        return args.lang
    content_dir = os.path.join(repo_root, "content")
    return sorted(
        d for d in os.listdir(content_dir)
        if os.path.isdir(os.path.join(content_dir, d)) and d != "en"
    )

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Localization outdatedness detector. Classifies "
            "localized files by status using content-based indicators "
            "and emits compact reports; orphan localized docs are "
            "listed in a separate section."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--lang", metavar="CODE", nargs="+",
        help="One or more locales to scan (e.g. --lang ko  or  --lang ko zh-cn ja)",
    )
    group.add_argument(
        "--all", action="store_true",
        help="Scan all locales under content/ except en (default when no option given)",
    )
    parser.add_argument(
        "--repo-root", default=None, metavar="DIR",
        help=(
            "Path to kubernetes/website repo root (auto-detected if omitted). "
            "Accepts relative paths, e.g. --repo-root ../website"
        ),
    )
    parser.add_argument(
        "--output-dir", "-o", default=".", metavar="DIR",
        help="Directory for report files (default: current directory)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show all indicator lines per file (default: one compact line)",
    )
    return parser

def main() -> None:
    args = _build_arg_parser().parse_args()

    if args.repo_root:
        repo_root = os.path.abspath(args.repo_root)
    else:
        repo_root = _auto_detect_repo_root()
        if repo_root is None:
            print(
                "error: could not auto-detect repo root (no content/en found "
                "above cwd). Use --repo-root to specify it explicitly.",
                file=sys.stderr,
            )
            sys.exit(1)

    date = datetime.date.today().isoformat()
    locales = _resolve_locales(args, repo_root)
    os.makedirs(args.output_dir, exist_ok=True)

    all_results: List[Tuple[str, List[FileReport], List[str]]] = []
    for locale in locales:
        print(f"Scanning content/{locale}/ ...", file=sys.stderr)
        evaluated, orphans = scan_locale(locale, repo_root)
        all_results.append((locale, evaluated, orphans))
        out_path = os.path.join(
            args.output_dir, f"l10n-indicators-{locale}.md"
        )
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(build_locale_report(
                locale, evaluated, orphans, repo_root, date, args.verbose,
            ))
        hi, poss, curr = count_files_by_status(evaluated)
        print(
            f"Wrote {out_path}  "
            f"({hi} highly_outdated, {poss} possibly_outdated, "
            f"{curr} up_to_date, {len(orphans)} orphans)",
            file=sys.stderr,
        )

    if len(locales) > 1:
        index_path = os.path.join(args.output_dir, "l10n-indicators-index.md")
        with open(index_path, "w", encoding="utf-8") as fh:
            fh.write(build_index_report(all_results, date))
        print(f"Wrote {index_path}", file=sys.stderr)

if __name__ == "__main__":
    main()

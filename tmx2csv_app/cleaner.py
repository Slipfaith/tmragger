from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from tmx2csv_app.io_pairs import PairRow, analyze_pair_file, build_cleaned_output_path, iter_pair_rows, open_pair_writer
from tmx2csv_app.language_profiles import LanguageProfile, get_profile

_TOKEN_PATTERN = re.compile(r"\{\{[^{}]+\}\}|\{[^{}\n]+\}")
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
_SPACE_RUN_RE = re.compile(r" {2,}")
_ELLIPSIS_RE = re.compile(r"\.{2,}")
_REPEAT_PUNCT_RE = re.compile(r"([,;:!?])\1+")
_DOUBLE_QUOTE_RE = re.compile(r'[«»“”„‟"]')
_FRENCH_OPEN_RE = re.compile(r"«\s*")
_FRENCH_CLOSE_RE = re.compile(r"\s*»")
_SPACED_DASH_RE = re.compile(r"(?<=\s)[-–—‑−](?=\s)")
_STANDALONE_DASH_RE = re.compile(r"(?:(?<=^)|(?<=\s))[–—‑−](?=\S)")
_FINAL_PUNCT_RE = re.compile(r'([.!?:;])(?:["»”)\]]*)\s*$')


@dataclass(slots=True)
class CleanerOptions:
    remove_empty_target: bool = True
    trim_edges: bool = True
    normalize_whitespace: bool = True
    normalize_punctuation: bool = True
    normalize_quotes: bool = False
    normalize_dashes: bool = False
    normalize_final_punctuation: bool = False
    dedupe_pairs: bool = True
    preview_limit: int = 200


@dataclass(slots=True)
class CleanIssue:
    rule_id: str
    severity: str
    message: str


@dataclass(slots=True)
class CleanResultRow:
    row_index: int
    source: str
    target_original: str
    target_cleaned: str
    changed: bool
    removed: bool
    status: str
    rules_applied: list[str] = field(default_factory=list)
    issues: list[CleanIssue] = field(default_factory=list)


@dataclass(slots=True)
class FileCleanResult:
    input_file: Path
    output_file: Path | None
    source_lang: str
    target_lang: str
    rows_in: int
    rows_out: int
    rows_changed: int
    rows_removed: int
    duplicates_removed: int
    warnings: int
    preview_rows: list[CleanResultRow] = field(default_factory=list)


class TokenProtector:
    def tokens(self, text: str) -> list[str]:
        return _TOKEN_PATTERN.findall(text)

    def protect(self, text: str) -> tuple[str, list[str]]:
        tokens: list[str] = []

        def replace(match: re.Match[str]) -> str:
            token_id = len(tokens)
            tokens.append(match.group(0))
            return f"\uFFF0TOKEN{token_id}\uFFF1"

        return _TOKEN_PATTERN.sub(replace, text), tokens

    def restore(self, text: str, tokens: list[str]) -> str:
        restored = text
        for token_id, token in enumerate(tokens):
            restored = restored.replace(f"\uFFF0TOKEN{token_id}\uFFF1", token)
        return restored


class CleanerEngine:
    def __init__(self, options: CleanerOptions, profile: LanguageProfile) -> None:
        self.options = options
        self.profile = profile
        self.token_protector = TokenProtector()

    def clean_row(self, row: PairRow) -> CleanResultRow:
        issues: list[CleanIssue] = []
        rules_applied: list[str] = []

        source_tokens = Counter(self.token_protector.tokens(row.source))
        target_tokens_before = Counter(self.token_protector.tokens(row.target))
        if source_tokens != target_tokens_before:
            issues.append(
                CleanIssue(
                    rule_id="token_mismatch_source_target",
                    severity="warning",
                    message="Source and target contain different placeholder/tag sets.",
                )
            )

        protected_target, target_tokens = self.token_protector.protect(row.target)
        cleaned = protected_target

        if self.options.normalize_whitespace:
            cleaned, changed = _normalize_service_whitespace(cleaned)
            if changed:
                rules_applied.append("normalize_service_whitespace")
            cleaned, changed = _collapse_internal_spaces(cleaned)
            if changed:
                rules_applied.append("collapse_internal_spaces")

        if self.options.trim_edges:
            trimmed = _trim_edges(cleaned)
            if trimmed != cleaned:
                cleaned = trimmed
                rules_applied.append("trim_edges")

        if self.options.normalize_punctuation:
            repeated = _normalize_repeated_punctuation(cleaned, self.profile)
            if repeated != cleaned:
                cleaned = repeated
                rules_applied.append("normalize_repeated_punctuation")
            punct = _normalize_punctuation_spacing(cleaned, self.profile)
            if punct != cleaned:
                cleaned = punct
                rules_applied.append("normalize_punctuation_spacing")

        if self.options.normalize_quotes:
            quoted = _normalize_quotes(cleaned, self.profile)
            if quoted != cleaned:
                cleaned = quoted
                rules_applied.append("normalize_quotes")

        if self.options.normalize_dashes:
            dashed = _normalize_dashes(cleaned, self.profile)
            if dashed != cleaned:
                cleaned = dashed
                rules_applied.append("normalize_dashes")

        cleaned_target = self.token_protector.restore(cleaned, target_tokens)
        target_tokens_after = Counter(self.token_protector.tokens(cleaned_target))
        if target_tokens_after != target_tokens_before:
            issues.append(
                CleanIssue(
                    rule_id="token_integrity",
                    severity="warning",
                    message="Cleaner changed the protected tokens. Row was reverted to original target.",
                )
            )
            cleaned_target = row.target
            rules_applied = []

        if self.options.normalize_final_punctuation:
            aligned = _align_final_punctuation(row.source, cleaned_target, self.profile)
            if aligned != cleaned_target:
                cleaned_target = aligned
                rules_applied.append("align_final_punctuation")

        removed = False
        status = "kept"
        if self.options.remove_empty_target and not cleaned_target.strip():
            removed = True
            status = "removed"
            rules_applied.append("remove_empty_target")
        elif rules_applied:
            status = "changed"
        elif issues:
            status = "warning"

        return CleanResultRow(
            row_index=row.row_index,
            source=row.source,
            target_original=row.target,
            target_cleaned=cleaned_target,
            changed=cleaned_target != row.target,
            removed=removed,
            status=status,
            rules_applied=rules_applied,
            issues=issues,
        )


def preview_pair_file(file_path: Path, options: CleanerOptions | None = None) -> FileCleanResult:
    return _process_pair_file(file_path=file_path, output_dir=None, options=options or CleanerOptions())


def clean_pair_file(file_path: Path, output_dir: Path, options: CleanerOptions | None = None) -> FileCleanResult:
    return _process_pair_file(file_path=file_path, output_dir=output_dir, options=options or CleanerOptions())


def _process_pair_file(file_path: Path, output_dir: Path | None, options: CleanerOptions) -> FileCleanResult:
    info = analyze_pair_file(file_path)
    profile = get_profile(info.target_lang)
    engine = CleanerEngine(options=options, profile=profile)
    preview_rows: list[CleanResultRow] = []
    seen_pairs: set[tuple[str, str]] = set()

    rows_out = 0
    rows_changed = 0
    rows_removed = 0
    duplicates_removed = 0
    warnings = 0
    writer = None
    output_file = build_cleaned_output_path(file_path, output_dir) if output_dir is not None else None

    try:
        if output_file is not None:
            writer = open_pair_writer(output_file, info.source_lang, info.target_lang)

        for row in iter_pair_rows(file_path):
            result_row = engine.clean_row(row)
            warnings += len(result_row.issues)

            if result_row.removed:
                rows_removed += 1
                _append_preview(preview_rows, result_row, options.preview_limit)
                continue

            pair_key = (result_row.source, result_row.target_cleaned)
            if options.dedupe_pairs and pair_key in seen_pairs:
                duplicates_removed += 1
                rows_removed += 1
                duplicate_row = CleanResultRow(
                    row_index=result_row.row_index,
                    source=result_row.source,
                    target_original=result_row.target_original,
                    target_cleaned=result_row.target_cleaned,
                    changed=result_row.changed,
                    removed=True,
                    status="removed",
                    rules_applied=result_row.rules_applied + ["dedupe_pairs"],
                    issues=result_row.issues,
                )
                _append_preview(preview_rows, duplicate_row, options.preview_limit)
                continue

            seen_pairs.add(pair_key)
            rows_out += 1
            if result_row.changed:
                rows_changed += 1
            if result_row.changed or result_row.issues:
                _append_preview(preview_rows, result_row, options.preview_limit)
            if writer is not None:
                writer.write_row(result_row.source, result_row.target_cleaned)
    finally:
        if writer is not None:
            writer.close()

    return FileCleanResult(
        input_file=file_path,
        output_file=output_file,
        source_lang=info.source_lang,
        target_lang=info.target_lang,
        rows_in=info.row_count,
        rows_out=rows_out,
        rows_changed=rows_changed,
        rows_removed=rows_removed,
        duplicates_removed=duplicates_removed,
        warnings=warnings,
        preview_rows=preview_rows,
    )


def _append_preview(preview_rows: list[CleanResultRow], row: CleanResultRow, limit: int) -> None:
    if len(preview_rows) < limit:
        preview_rows.append(row)


def _trim_edges(text: str) -> str:
    return text.strip()


def _normalize_service_whitespace(text: str) -> tuple[str, bool]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    normalized = normalized.replace("\u00a0", " ").replace("\u202f", " ")
    normalized = _ZERO_WIDTH_RE.sub("", normalized)
    normalized = re.sub(r" *\n *", "\n", normalized)
    return normalized, normalized != text


def _collapse_internal_spaces(text: str) -> tuple[str, bool]:
    lines = [_SPACE_RUN_RE.sub(" ", line) for line in text.split("\n")]
    normalized = "\n".join(lines)
    return normalized, normalized != text


def _normalize_repeated_punctuation(text: str, profile: LanguageProfile) -> str:
    text = _ELLIPSIS_RE.sub(profile.ellipsis, text)
    return _REPEAT_PUNCT_RE.sub(r"\1", text)


def _normalize_punctuation_spacing(text: str, profile: LanguageProfile) -> str:
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    for mark in ":;?!":
        if mark in profile.space_before_punct:
            text = re.sub(rf"(?<!^)(?<!\s)\{mark}", f" {mark}", text)
        else:
            text = re.sub(rf"\s+\{mark}", mark, text)
    text = re.sub(r"\s+([,.])", r"\1", text)
    text = re.sub(r'([,;:!?])(?![\s"»)\]}]|$)', r"\1 ", text)
    text = re.sub(r'(?<!\d)\.(?![\s."»)\]}]|$)(?!\d)', ". ", text)
    return text


def _normalize_quotes(text: str, profile: LanguageProfile) -> str:
    toggle_open = True
    result: list[str] = []
    for char in text:
        if _DOUBLE_QUOTE_RE.fullmatch(char):
            replacement = profile.quote_open if toggle_open else profile.quote_close
            result.append(replacement)
            if profile.quote_open != profile.quote_close:
                toggle_open = not toggle_open
            continue
        result.append(char)
    normalized = "".join(result)
    if profile.quote_open == "«" and profile.quote_close == "»":
        normalized = _FRENCH_OPEN_RE.sub("« ", normalized)
        normalized = _FRENCH_CLOSE_RE.sub(" »", normalized)
    return normalized


def _normalize_dashes(text: str, profile: LanguageProfile) -> str:
    text = _SPACED_DASH_RE.sub(profile.dash_char, text)
    return _STANDALONE_DASH_RE.sub(profile.dash_char, text)


def _align_final_punctuation(source: str, target: str, profile: LanguageProfile) -> str:
    if profile.final_punctuation_mode != "preserve":
        return target
    source_match = _FINAL_PUNCT_RE.search(source.rstrip())
    target_match = _FINAL_PUNCT_RE.search(target.rstrip())
    if not source_match or target_match:
        return target
    return f"{target.rstrip()}{source_match.group(1)}"

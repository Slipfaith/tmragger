from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LanguageProfile:
    code: str
    space_before_punct: frozenset[str]
    quote_open: str
    quote_close: str
    dash_char: str
    ellipsis: str = "..."
    final_punctuation_mode: str = "preserve"


_DEFAULT_PROFILE = LanguageProfile(
    code="default",
    space_before_punct=frozenset(),
    quote_open='"',
    quote_close='"',
    dash_char="-",
)

_PROFILES = {
    "en": LanguageProfile(code="en", space_before_punct=frozenset(), quote_open='"', quote_close='"', dash_char="-"),
    "en-us": LanguageProfile(code="en-US", space_before_punct=frozenset(), quote_open='"', quote_close='"', dash_char="-"),
    "fr": LanguageProfile(code="fr", space_before_punct=frozenset({":", ";", "?", "!"}), quote_open="«", quote_close="»", dash_char="-"),
    "tr": LanguageProfile(code="tr", space_before_punct=frozenset(), quote_open='"', quote_close='"', dash_char="-"),
    "tg": LanguageProfile(code="tg", space_before_punct=frozenset(), quote_open='"', quote_close='"', dash_char="-"),
    "ka": LanguageProfile(code="ka", space_before_punct=frozenset(), quote_open='"', quote_close='"', dash_char="-"),
    "ar": LanguageProfile(code="ar", space_before_punct=frozenset(), quote_open='"', quote_close='"', dash_char="-"),
}


def get_profile(lang_code: str) -> LanguageProfile:
    key = lang_code.strip().lower()
    return _PROFILES.get(key, _DEFAULT_PROFILE)

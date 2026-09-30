"""Script-aware text helpers for Indian-language FIRs.

* ``canonical_name`` maps "Ramesh", "Rameś", "रमेश" and "RAMESH" to one Latin key so entity resolution and search do
  not treat spellings and scripts as different people.
* ``extract_legal_sections`` pulls IPC / BNS section numbers out of FIR text and classifies them.

Pure Python: ``indic-transliteration`` is used for Devanagari when installed, and the module degrades to leaving
Devanagari untouched (with no crash) when it is not.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Optional

try:  # required in production (requirements.txt); optional so the unit-test env stays light
    from indic_transliteration import sanscript
except ImportError:  # pragma: no cover - exercised only where the library is absent
    sanscript = None

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_HONORIFICS = {"shri", "shree", "sri", "smt", "shrimati", "mr", "mrs", "ms", "dr", "km", "kumari"}
_LATIN_FOLDS = {"ś": "sh", "ṣ": "sh", "ṭ": "t", "ḍ": "d", "ṇ": "n", "ñ": "n", "ṅ": "n", "ṁ": "n", "ṃ": "n", "ḥ": "h", "ā": "a", "ī": "i", "ū": "u", "ṛ": "ri", "ç": "s"}


def has_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI.search(text))


def detect_script(text: str) -> str:
    """'devanagari', 'latin' or 'mixed' (ignoring digits and punctuation)."""
    dev = len(_DEVANAGARI.findall(text))
    latin = sum(1 for ch in text if ch.isalpha() and ch.isascii() or "LATIN" in unicodedata.name(ch, ""))
    if dev and latin:
        return "mixed"
    return "devanagari" if dev else "latin"


def _devanagari_to_latin(word: str) -> str:
    """Devanagari word -> lowercase Latin key. Hindi drops the inherent final 'a' (रमेश is Ramesh, not Ramesha)."""
    if sanscript is None:
        return word
    itrans = sanscript.transliterate(word, sanscript.DEVANAGARI, sanscript.ITRANS)
    itrans = itrans.replace("RRi", "ri").replace("RRI", "ri").replace("M", "n").replace("~N", "n").replace(".n", "n").replace("N", "n")
    if len(itrans) > 2 and itrans.endswith("a") and not itrans.endswith("aa"):
        itrans = itrans[:-1]
    return itrans


def _fold_latin(word: str) -> str:
    word = "".join(_LATIN_FOLDS.get(ch, _LATIN_FOLDS.get(ch.lower(), ch)) for ch in unicodedata.normalize("NFC", word))
    word = unicodedata.normalize("NFKD", word)
    word = "".join(ch for ch in word if not unicodedata.combining(ch)).lower()
    word = re.sub(r"[^a-z0-9]", "", word)
    word = word.replace("w", "v").replace("ee", "i").replace("oo", "u")
    return re.sub(r"(.)\1+", r"\1", word)  # kumaar -> kumar, ramessh -> ramesh


@lru_cache(maxsize=4096)
def canonical_name(name: str) -> str:
    """Single canonical Latin key for a person/place name written in any script or casing. '' if nothing usable."""
    tokens = []
    for raw in re.split(r"[\s.,;:_\-/]+", unicodedata.normalize("NFC", name or "")):
        if not raw:
            continue
        # Mixed-script tokens (rare) are split at script boundaries so each half is handled on its own.
        for part in re.findall(r"[ऀ-ॿ]+|[^ऀ-ॿ]+", raw):
            folded = _fold_latin(_devanagari_to_latin(part) if has_devanagari(part) else part)
            if folded:
                tokens.append(folded)
    if len(tokens) > 1:
        tokens = [t for t in tokens if t not in _HONORIFICS] or tokens
    return " ".join(tokens)


def name_aliases(name: str) -> list[str]:
    """The original spelling (kept so the source script stays searchable) when it differs from the canonical key."""
    original = " ".join((name or "").split())
    return [original] if original and original.lower() != canonical_name(name) else []


def normalise_text_digits(text: str) -> str:
    return text.translate(_DEVANAGARI_DIGITS)


# ---------------------------------------------------------------------------
# IPC / BNS sections
# ---------------------------------------------------------------------------

# Offence categories. The seven Women-Safety categories are the ones the WSRS (phase 3) scores on.
WS_CATEGORIES = {"HARASSMENT", "STALKING", "ASSAULT", "TRAFFICKING", "DOMESTIC_VIOLENCE", "ACID_ATTACK", "SEXUAL_OFFENCE"}

_IPC_CATEGORY = {
    "354": "ASSAULT", "354A": "HARASSMENT", "354B": "ASSAULT", "354C": "SEXUAL_OFFENCE", "354D": "STALKING",
    "509": "HARASSMENT", "375": "SEXUAL_OFFENCE", "376": "SEXUAL_OFFENCE", "376A": "SEXUAL_OFFENCE",
    "376AB": "SEXUAL_OFFENCE", "376B": "SEXUAL_OFFENCE", "376C": "SEXUAL_OFFENCE", "376D": "SEXUAL_OFFENCE",
    "376DA": "SEXUAL_OFFENCE", "376DB": "SEXUAL_OFFENCE", "376E": "SEXUAL_OFFENCE", "377": "SEXUAL_OFFENCE",
    "326A": "ACID_ATTACK", "326B": "ACID_ATTACK",
    "366": "TRAFFICKING", "366A": "TRAFFICKING", "366B": "TRAFFICKING", "370": "TRAFFICKING", "370A": "TRAFFICKING",
    "372": "TRAFFICKING", "373": "TRAFFICKING",
    "498A": "DOMESTIC_VIOLENCE", "304B": "DOMESTIC_VIOLENCE",
    "302": "HOMICIDE", "304": "HOMICIDE", "307": "HOMICIDE", "324": "HURT", "323": "HURT", "325": "HURT", "326": "HURT",
    "379": "THEFT", "380": "THEFT", "392": "ROBBERY", "395": "ROBBERY", "396": "ROBBERY",
    "406": "BREACH_OF_TRUST", "420": "CHEATING", "467": "FORGERY", "468": "FORGERY", "471": "FORGERY",
    "120B": "CONSPIRACY", "363": "KIDNAPPING", "364": "KIDNAPPING", "365": "KIDNAPPING", "506": "INTIMIDATION",
    "504": "INTIMIDATION", "294": "OBSCENITY", "153A": "COMMUNAL", "201": "EVIDENCE_TAMPERING",
}
_BNS_CATEGORY = {
    "74": "ASSAULT", "75": "HARASSMENT", "76": "ASSAULT", "77": "SEXUAL_OFFENCE", "78": "STALKING", "79": "HARASSMENT",
    "63": "SEXUAL_OFFENCE", "64": "SEXUAL_OFFENCE", "65": "SEXUAL_OFFENCE", "66": "SEXUAL_OFFENCE", "67": "SEXUAL_OFFENCE",
    "68": "SEXUAL_OFFENCE", "69": "SEXUAL_OFFENCE", "70": "SEXUAL_OFFENCE", "71": "SEXUAL_OFFENCE",
    "124": "ACID_ATTACK", "96": "TRAFFICKING", "143": "TRAFFICKING", "144": "TRAFFICKING",
    "80": "DOMESTIC_VIOLENCE", "85": "DOMESTIC_VIOLENCE", "86": "DOMESTIC_VIOLENCE",
    "103": "HOMICIDE", "105": "HOMICIDE", "109": "HOMICIDE", "115": "HURT", "117": "HURT",
    "303": "THEFT", "305": "THEFT", "309": "ROBBERY", "310": "ROBBERY", "316": "BREACH_OF_TRUST", "318": "CHEATING",
    "336": "FORGERY", "338": "FORGERY", "340": "FORGERY", "61": "CONSPIRACY", "137": "KIDNAPPING", "140": "KIDNAPPING",
    "351": "INTIMIDATION", "356": "INTIMIDATION", "196": "COMMUNAL", "238": "EVIDENCE_TAMPERING",
}
# Common-intention / abetment style sections modify another charge rather than being an offence themselves.
_MODIFIER_SECTIONS = {"IPC": {"34", "149", "109", "114"}, "BNS": {"3(5)", "3", "49", "54"}}

_IPC_WORDS = r"\bI\.?\s?P\.?\s?C\b\.?|Indian\s+Penal\s+Code|भा\.?\s?द\.?\s?(?:वि|सं)\.?|भारतीय\s+दण्?ड\s+संहिता"
_BNS_WORDS = r"\bB\.?\s?N\.?\s?S\b\.?|Bharatiya\s+Nyaya\s+Sanhita|भा\.?\s?न्या\.?\s?सं\.?|भारतीय\s+न्याय\s+संहिता|बी\.?\s?एन\.?\s?एस\.?"
_ACT_WORDS = f"(?:{_IPC_WORDS}|{_BNS_WORDS})"
_ACT_RE = re.compile(f"(?P<ipc>{_IPC_WORDS})|(?P<bns>{_BNS_WORDS})", re.IGNORECASE)
_SEC_NUM = r"\d{1,3}(?:-?[A-Za-z]{1,2}(?![A-Za-z]))?(?:\(\d{1,2}\))?(?:\([a-z]\))?"
_SEC_LIST = rf"{_SEC_NUM}(?:\s*(?:,|/|&|and|और|तथा)\s*{_SEC_NUM})*"
_SECTION_RE = re.compile(
    rf"(?:(?:{_ACT_WORDS})\s*[,:\-]?\s*(?:sections?|secs?\.?|u/s|धारा(?:ओं)?)?\s*(?P<pre_list>{_SEC_LIST})"
    rf"|(?:under\s+)?(?:sections?|secs?\.?|u/ss?|धारा(?:ओं)?)\s*(?P<list>{_SEC_LIST})(?:\s*(?:of|के\s+तहत)?\s*(?:the\s+)?(?:{_ACT_WORDS}))?"
    rf"|(?<![\w.])(?P<bare_list>{_SEC_LIST})\s*(?:{_ACT_WORDS}))",
    re.IGNORECASE,
)


def _act_of(match: re.Match) -> Optional[str]:
    # The act may be written before or after the section list, so scan the whole matched span.
    span = match.group(0)
    hit = _ACT_RE.search(span)
    if not hit:
        return None
    return "BNS" if hit.lastgroup == "bns" else "IPC"


def _split_sections(raw: str) -> list[str]:
    out = []
    for piece in re.split(r"\s*(?:,|/|&|\band\b|और|तथा)\s*", raw.strip(), flags=re.IGNORECASE):
        piece = re.sub(r"[\s-]+", "", piece).upper()
        if piece:
            out.append(piece)
    return out


def _base_section(section: str) -> str:
    """'64(2)' -> '64', '376AB' -> '376AB', '354D(1)' -> '354D'."""
    return re.sub(r"\(.*$", "", section)


def extract_legal_sections(text: str) -> list[dict]:
    """IPC / BNS sections mentioned in FIR text -> [{section, act, offense_category, confidence, evidence}].

    ``act`` is 'IPC', 'BNS' or 'UNKNOWN' when the text names a section without its Act (the category is then
    'UNCLASSIFIED' because IPC and BNS numbers overlap). Order of first appearance, de-duplicated.
    """
    text = normalise_text_digits(text or "")
    found: dict[tuple[str, str], dict] = {}
    for match in _SECTION_RE.finditer(text):
        raw_list = match.group("pre_list") or match.group("list") or match.group("bare_list")
        act = _act_of(match)
        for section in _split_sections(raw_list):
            base = _base_section(section)
            if act and (section.lower() in _MODIFIER_SECTIONS.get(act, ()) or base in _MODIFIER_SECTIONS.get(act, ())):
                continue
            if act == "IPC":
                category = _IPC_CATEGORY.get(base, "OTHER")
            elif act == "BNS":
                category = _BNS_CATEGORY.get(base, "OTHER")
            else:
                category = "UNCLASSIFIED"
            key = (act or "UNKNOWN", section)
            if key not in found:
                found[key] = {
                    "section": section,
                    "act": act or "UNKNOWN",
                    "offense_category": category,
                    "womens_safety": category in WS_CATEGORIES,
                    "confidence": 0.95 if act and category not in {"OTHER", "UNCLASSIFIED"} else 0.6,
                    "evidence": " ".join(match.group(0).split())[:200],
                }
    return list(found.values())

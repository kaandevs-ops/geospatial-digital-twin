"""
AI Assistant - Intent Parser
============================

Roadmap Phase 12. Turkce (agirlikli) dogal dil komutlarini `CommandIntent`
listesine cevirir. Iki mod destekler:

  1. Kural tabanli (varsayilan, bagimliliksiz): anahtar-kelime + regex
     eslestirme. Ag baglantisi / API key gerektirmez, deterministiktir,
     test edilebilir.
  2. LLM destekli (opsiyonel): `llm_fn` parametresi ile ana projenin
     coklu-LLM router'i (orn. `brain/` veya `core/` altindaki mevcut LLM
     cagirma katmani) enjekte edilebilir. `llm_fn(text) -> list[dict]`
     imzasinda, her dict `{"action": ..., "target": ..., "parameters": {...}}`
     seklinde JSON-uyumlu olmalidir. Kural tabanli parser hicbir fragmani
     eslestiremediginde ve `llm_fn` verilmisse, kalan fragman LLM'e
     devredilir (hybrid: hizli/ucretsiz kural motoru + zor durumlarda LLM
     fallback).

Cumle, once " ve ", "," , ".", ";" baglaclarindan atomik fragmanlara
bolunur ("bir kat daha ekle ve catiyi duz yap" -> ["bir kat daha ekle",
"catiyi duz yap"]); her fragman bagimsiz olarak eslestirilir. Bu, roadmap
Phase 12'nin ornek cumlesiyle ("Bu binaya bir kat daha ekle ve catiyi duz
yap.") birebir uyumludur.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from .intent import CommandIntent, IntentAction, ParseResult

# -- Sozlukler -------------------------------------------------------------- #

_ROOF_KEYWORDS: dict[str, str] = {
    "duz": "flat",
    "düz": "flat",
    "flat": "flat",
    "hip": "hip",
    "beşik": "gable",
    "besik": "gable",
    "gable": "gable",
    "çapraz beşik": "cross_gable",
    "capraz besik": "cross_gable",
    "mansard": "mansard",
    "piramit": "pyramid",
    "pyramid": "pyramid",
    "testere": "sawtooth",
    "sawtooth": "sawtooth",
    "endüstriyel çatı": "industrial",
    "endustriyel cati": "industrial",
    "modern": "modern",
    "solar": "solar",
    "güneş": "solar",
    "gunes": "solar",
    "yeşil": "green",
    "yesil": "green",
    "green": "green",
}

_FACADE_KEYWORDS: dict[str, str] = {
    "cam": "cam",
    "beton": "beton",
    "tuğla": "tugla",
    "tugla": "tugla",
    "metal": "metal",
    "kompozit": "kompozit",
    "taş": "tas",
    "tas": "tas",
    "ahşap": "ahsap",
    "ahsap": "ahsap",
    "endüstriyel": "endustriyel",
    "endustriyel": "endustriyel",
}

_NUMBER_WORDS: dict[str, int] = {
    "bir": 1, "iki": 2, "üç": 3, "uc": 3, "dört": 4, "dort": 4,
    "beş": 5, "bes": 5, "altı": 6, "alti": 6, "yedi": 7, "sekiz": 8,
    "dokuz": 9, "on": 10,
}

_FLOOR_ADD_RE = re.compile(r"\bkat\w*\b.*\bekle\w*\b|\bekle\w*\b.*\bkat\w*\b", re.IGNORECASE)
_FLOOR_REMOVE_RE = re.compile(
    r"\bkat\w*\b.*\b(sil|kaldır|kaldir|çıkar|cikar)\w*\b|\b(sil|kaldır|kaldir)\w*\b.*\bkat\w*\b",
    re.IGNORECASE,
)
_ROOF_CHANGE_RE = re.compile(r"\bçat\w*\b", re.IGNORECASE)
_FACADE_CHANGE_RE = re.compile(r"\bcephe\w*\b|\bmalzeme\w*\b|\bdış\s*yüzey\w*\b|\bdis\s*yuzey\w*\b", re.IGNORECASE)
_DOOR_ADD_RE = re.compile(r"\bkap[ıi]\w*\b.*\bekle\w*\b|\bekle\w*\b.*\bkap[ıi]\w*\b", re.IGNORECASE)
_DOOR_REMOVE_RE = re.compile(r"\bkap[ıi]\w*\b.*\b(sil|kaldır|kaldir)\w*\b", re.IGNORECASE)
_WINDOW_ADD_RE = re.compile(r"\bpencere\w*\b.*\bekle\w*\b|\bekle\w*\b.*\bpencere\w*\b", re.IGNORECASE)
_WINDOW_REMOVE_RE = re.compile(r"\bpencere\w*\b.*\b(sil|kaldır|kaldir)\w*\b", re.IGNORECASE)
_ANALYZE_RE = re.compile(r"\banaliz\b|\bincele\b|\bdeğerlendir\b|\bdegerlendir\b", re.IGNORECASE)

_SPLIT_RE = re.compile(r"\s+ve\s+|\s+and\s+|[,;.]+", re.IGNORECASE)

# -- ROADMAP_V4 - Faz E16: i18n - Ingilizce kural seti ---------------------- #
# Turkce kural setiyle birebir ayni CommandIntent uzayina cozulur (kabul
# kriteri: "bir kat ekle" ve "add a floor" ayni action'a esler). Basit dil
# algilama `harita.i18n.detect_language` uzerinden yapilabilir, ancak
# `_parse_fragment` performans/saglamlik icin dil ayrimi yapmadan her iki
# kural setini de sirayla dener - boylece karisik dilli cumleler bile
# (nadir ama olasi) dogru cozulur.

_EN_ROOF_KEYWORDS: dict[str, str] = {
    "flat": "flat",
    "hip": "hip",
    "gable": "gable",
    "cross gable": "cross_gable",
    "mansard": "mansard",
    "pyramid": "pyramid",
    "sawtooth": "sawtooth",
    "industrial": "industrial",
    "modern": "modern",
    "solar": "solar",
    "green": "green",
}

_EN_FACADE_KEYWORDS: dict[str, str] = {
    "glass": "cam",
    "concrete": "beton",
    "brick": "tugla",
    "metal": "metal",
    "composite": "kompozit",
    "stone": "tas",
    "wood": "ahsap",
    "wooden": "ahsap",
    "industrial": "endustriyel",
}

_EN_NUMBER_WORDS: dict[str, int] = {
    "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_EN_FLOOR_ADD_RE = re.compile(r"\bfloor\w*\b.*\badd\w*\b|\badd\w*\b.*\bfloor\w*\b", re.IGNORECASE)
_EN_FLOOR_REMOVE_RE = re.compile(
    r"\bfloor\w*\b.*\b(remove|delete)\w*\b|\b(remove|delete)\w*\b.*\bfloor\w*\b",
    re.IGNORECASE,
)
_EN_ROOF_CHANGE_RE = re.compile(r"\broof\w*\b", re.IGNORECASE)
_EN_FACADE_CHANGE_RE = re.compile(r"\bfacade\w*\b|\bmaterial\w*\b|\bexterior\w*\b|\bcladding\w*\b", re.IGNORECASE)
_EN_DOOR_ADD_RE = re.compile(r"\bdoor\w*\b.*\badd\w*\b|\badd\w*\b.*\bdoor\w*\b", re.IGNORECASE)
_EN_DOOR_REMOVE_RE = re.compile(
    r"\bdoor\w*\b.*\b(remove|delete)\w*\b|\b(remove|delete)\w*\b.*\bdoor\w*\b", re.IGNORECASE
)
_EN_WINDOW_ADD_RE = re.compile(r"\bwindow\w*\b.*\badd\w*\b|\badd\w*\b.*\bwindow\w*\b", re.IGNORECASE)
_EN_WINDOW_REMOVE_RE = re.compile(
    r"\bwindow\w*\b.*\b(remove|delete)\w*\b|\b(remove|delete)\w*\b.*\bwindow\w*\b", re.IGNORECASE
)
_EN_ANALYZE_RE = re.compile(r"\banaly[sz]e\b|\bexamine\b|\bevaluate\b|\bassess\b", re.IGNORECASE)


def _find_en_count(fragment: str, default: int = 1) -> int:
    m = re.search(r"(\d+)\s*floors?", fragment, re.IGNORECASE)
    if m:
        return int(m.group(1))
    lowered = fragment.lower()
    for word, value in _EN_NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", lowered):
            return value
    return default


def _find_count(fragment: str, default: int = 1) -> int:
    m = re.search(r"(\d+)\s*kat", fragment, re.IGNORECASE)
    if m:
        return int(m.group(1))
    for word, value in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", fragment, re.IGNORECASE):
            return value
    return default


def _find_keyword(fragment: str, table: dict[str, str]) -> Optional[str]:
    lowered = fragment.lower()
    # en uzun anahtari once dene (orn. "capraz besik" > "besik")
    for key in sorted(table, key=len, reverse=True):
        if key in lowered:
            return table[key]
    return None


class IntentParser:
    """Kural tabanli (+opsiyonel LLM fallback) dogal dil -> CommandIntent
    parser'i."""

    def __init__(self, llm_fn: Optional[Callable[[str], list[dict]]] = None) -> None:
        self._llm_fn = llm_fn

    def parse(self, text: str) -> ParseResult:
        fragments = [f.strip() for f in _SPLIT_RE.split(text) if f.strip()]
        result = ParseResult()
        for fragment in fragments:
            intent = self._parse_fragment(fragment)
            if intent is not None:
                result.intents.append(intent)
                continue
            if self._llm_fn is not None:
                llm_intents = self._llm_intents(fragment)
                if llm_intents:
                    result.intents.extend(llm_intents)
                    continue
            result.unmatched_fragments.append(fragment)
            result.intents.append(
                CommandIntent(action=IntentAction.UNKNOWN, raw_fragment=fragment, confidence=0.0)
            )
        return result

    # -- Kural tabanli eslestirme -------------------------------------- #

    def _parse_fragment(self, fragment: str) -> Optional[CommandIntent]:
        if _ROOF_CHANGE_RE.search(fragment):
            roof_type = _find_keyword(fragment, _ROOF_KEYWORDS)
            if roof_type:
                return CommandIntent(
                    action=IntentAction.CHANGE_ROOF,
                    parameters={"roof_type": roof_type},
                    raw_fragment=fragment,
                )
        if _FACADE_CHANGE_RE.search(fragment):
            material = _find_keyword(fragment, _FACADE_KEYWORDS)
            if material:
                return CommandIntent(
                    action=IntentAction.CHANGE_FACADE,
                    parameters={"material": material},
                    raw_fragment=fragment,
                )
        # cephe anahtar kelimesi olmasa da malzeme kelimesi tek basina gecebilir
        if not _ROOF_CHANGE_RE.search(fragment):
            material = _find_keyword(fragment, _FACADE_KEYWORDS)
            if material and re.search(r"\byap\b|\bdeğiştir\b|\bdegistir\b", fragment, re.IGNORECASE):
                return CommandIntent(
                    action=IntentAction.CHANGE_FACADE,
                    parameters={"material": material},
                    raw_fragment=fragment,
                )
        if _FLOOR_ADD_RE.search(fragment):
            return CommandIntent(
                action=IntentAction.ADD_FLOOR,
                parameters={"count": _find_count(fragment)},
                raw_fragment=fragment,
            )
        if _FLOOR_REMOVE_RE.search(fragment):
            return CommandIntent(
                action=IntentAction.REMOVE_FLOOR,
                parameters={"count": _find_count(fragment)},
                raw_fragment=fragment,
            )
        if _DOOR_ADD_RE.search(fragment):
            return CommandIntent(action=IntentAction.ADD_DOOR, parameters={}, raw_fragment=fragment)
        if _DOOR_REMOVE_RE.search(fragment):
            return CommandIntent(action=IntentAction.REMOVE_DOOR, parameters={}, raw_fragment=fragment)
        if _WINDOW_ADD_RE.search(fragment):
            return CommandIntent(action=IntentAction.ADD_WINDOW, parameters={}, raw_fragment=fragment)
        if _WINDOW_REMOVE_RE.search(fragment):
            return CommandIntent(action=IntentAction.REMOVE_WINDOW, parameters={}, raw_fragment=fragment)
        if _ANALYZE_RE.search(fragment):
            return CommandIntent(action=IntentAction.ANALYZE_BUILDING, parameters={}, raw_fragment=fragment)
        return self._parse_fragment_en(fragment)

    # -- ROADMAP_V4 - Faz E16: Ingilizce kural tabanli eslestirme -------- #

    def _parse_fragment_en(self, fragment: str) -> Optional[CommandIntent]:
        if _EN_ROOF_CHANGE_RE.search(fragment):
            roof_type = _find_keyword(fragment, _EN_ROOF_KEYWORDS)
            if roof_type:
                return CommandIntent(
                    action=IntentAction.CHANGE_ROOF,
                    parameters={"roof_type": roof_type},
                    raw_fragment=fragment,
                )
        if _EN_FACADE_CHANGE_RE.search(fragment):
            material = _find_keyword(fragment, _EN_FACADE_KEYWORDS)
            if material:
                return CommandIntent(
                    action=IntentAction.CHANGE_FACADE,
                    parameters={"material": material},
                    raw_fragment=fragment,
                )
        if not _EN_ROOF_CHANGE_RE.search(fragment):
            material = _find_keyword(fragment, _EN_FACADE_KEYWORDS)
            if material and re.search(r"\bmake\b|\bchange\b", fragment, re.IGNORECASE):
                return CommandIntent(
                    action=IntentAction.CHANGE_FACADE,
                    parameters={"material": material},
                    raw_fragment=fragment,
                )
        if _EN_FLOOR_ADD_RE.search(fragment):
            return CommandIntent(
                action=IntentAction.ADD_FLOOR,
                parameters={"count": _find_en_count(fragment)},
                raw_fragment=fragment,
            )
        if _EN_FLOOR_REMOVE_RE.search(fragment):
            return CommandIntent(
                action=IntentAction.REMOVE_FLOOR,
                parameters={"count": _find_en_count(fragment)},
                raw_fragment=fragment,
            )
        if _EN_DOOR_ADD_RE.search(fragment):
            return CommandIntent(action=IntentAction.ADD_DOOR, parameters={}, raw_fragment=fragment)
        if _EN_DOOR_REMOVE_RE.search(fragment):
            return CommandIntent(action=IntentAction.REMOVE_DOOR, parameters={}, raw_fragment=fragment)
        if _EN_WINDOW_ADD_RE.search(fragment):
            return CommandIntent(action=IntentAction.ADD_WINDOW, parameters={}, raw_fragment=fragment)
        if _EN_WINDOW_REMOVE_RE.search(fragment):
            return CommandIntent(action=IntentAction.REMOVE_WINDOW, parameters={}, raw_fragment=fragment)
        if _EN_ANALYZE_RE.search(fragment):
            return CommandIntent(action=IntentAction.ANALYZE_BUILDING, parameters={}, raw_fragment=fragment)
        return None

    # -- LLM fallback ---------------------------------------------------- #

    def _llm_intents(self, fragment: str) -> list[CommandIntent]:
        assert self._llm_fn is not None
        raw_list = self._llm_fn(fragment) or []
        intents: list[CommandIntent] = []
        for raw in raw_list:
            try:
                action = IntentAction(raw.get("action", "unknown"))
            except ValueError:
                action = IntentAction.UNKNOWN
            intents.append(
                CommandIntent(
                    action=action,
                    target=raw.get("target", "building"),
                    parameters=raw.get("parameters", {}) or {},
                    confidence=float(raw.get("confidence", 0.6)),
                    raw_fragment=fragment,
                )
            )
        return intents

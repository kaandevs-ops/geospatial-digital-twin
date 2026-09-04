"""
i18n - Basit Dil Algilama
==========================

Roadmap V4 - Faz E16. Tam bir dil siniflandirici degil; `ai_assistant`
girdisinin Turkce mi Ingilizce mi oldugunu kaba ama deterministik bir
sekilde tahmin eden stdlib-only bir heuristic. Amac: `IntentParser`
kural setini secmek/loglamak icin yeterli sinyal (parser'in kendisi
her iki dilin kurallarini da her zaman dener, bu yuzden yanlis tahmin
komut coz'meyi bozmaz - sadece raporlama/telemetri amacli).

Yontem: Turkce'ye ozgu karakterler (ç,ğ,ı,ö,ş,ü) + yuksek frekansli
Turkce fonksiyon kelimeleri (bir, ve, ile, kat, ekle, ...) vs Ingilizce
fonksiyon kelimeleri (the, and, add, floor, remove, ...) icin puanlama.
"""
from __future__ import annotations

import re

_TR_CHARS = set("çğıöşüÇĞİÖŞÜ")

_TR_WORDS = {
    "bir", "iki", "ve", "ile", "kat", "ekle", "kaldır", "kaldir", "sil",
    "çatı", "cati", "cephe", "pencere", "kapı", "kapi", "değiştir",
    "degistir", "analiz", "yap", "bu", "binaya", "binayı", "daha",
}
_EN_WORDS = {
    "the", "and", "add", "remove", "floor", "floors", "roof", "facade",
    "window", "door", "change", "make", "analyze", "building", "this",
    "a", "an", "more",
}

_WORD_RE = re.compile(r"[a-zA-ZçğıöşüÇĞİÖŞÜ]+")


def detect_language(text: str) -> str:
    """`text` icin en olasi dili dondurur: "tr" veya "en".

    Bos/anlamsiz girdi ya da eşit puan durumunda varsayilan "tr"'dir
    (projenin birincil dili - bkz. i18n/translations.py DEFAULT_LANGUAGE).
    """
    if any(ch in _TR_CHARS for ch in text):
        return "tr"

    words = {w.lower() for w in _WORD_RE.findall(text)}
    if not words:
        return "tr"

    tr_score = len(words & _TR_WORDS)
    en_score = len(words & _EN_WORDS)

    if en_score > tr_score:
        return "en"
    return "tr"

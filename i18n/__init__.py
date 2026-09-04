"""
i18n - Coklu Dil Destegi
=========================

Roadmap V4 - Faz E16 (Yeni Alt Sistem). Onceki durumda `ai_assistant`
yalnizca Turkce NLU destekliyordu ve `app_shell/web/index.html` arayuz
metinleri sabit Turkce/Ingilizce karisimiydi - merkezi bir ceviri/
yerellestirme katmani yoktu.

Bu paket stdlib-only basit bir anahtar->ceviri sozlugu sistemi (bkz.
`translations.py`) ve kaba bir dil algilama heuristigi (`language_detection.py`)
sunar. `ai_assistant.intent_parser` Ingilizce bir kural seti ile
genisletildi (bkz. o modulun kendi degisiklik notlari); `app_shell`
arayuzu bu sozlukten metin cekebilir (bkz. `web/index.html` dil
degistirme dugmesi).
"""
from __future__ import annotations

from .translations import (
    SUPPORTED_LANGUAGES,
    DEFAULT_LANGUAGE,
    TRANSLATIONS,
    translate,
    Translator,
)
from .language_detection import detect_language

__all__ = [
    "SUPPORTED_LANGUAGES",
    "DEFAULT_LANGUAGE",
    "TRANSLATIONS",
    "translate",
    "Translator",
    "detect_language",
]

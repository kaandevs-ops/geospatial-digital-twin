"""
i18n - Ceviri Sozlugu
======================

Roadmap V4 - Faz E16 (+ Faz 6/3 genisletme). stdlib-only anahtar -> dil ->
metin sozlugu. `gettext` yerine kasıtlı olarak basit bir dict kullanılıyor:
proje küçük/orta ölçekli bir UI metin kümesine sahip, .po/.mo derleme
adımı eklemek (gettext'in gerektirdiği) bu ölçekte gereksiz operasyonel
karmaşıklık ekler. İleride büyürse `gettext.GNUTranslations` bu
sözlüğün üzerine (aynı `translate()` imzasını koruyarak) eklenebilir.

Desteklenen diller: "tr" (varsayılan), "en", "de", "ar".

Not: `ai_assistant.intent_parser`in dogal dil komut ayristirmasi hala
yalnizca tr/en kural setlerine sahip (bkz. `language_detection.py`
docstring'i) - bu dosyadaki de/ar girdileri yalnizca statik UI metinleri
icindir, komut satiri/NLU kapsamini genisletmez.
"""

from __future__ import annotations

SUPPORTED_LANGUAGES: tuple[str, ...] = ("tr", "en", "de", "ar")
DEFAULT_LANGUAGE = "tr"

# key -> {lang: metin}
TRANSLATIONS: dict[str, dict[str, str]] = {
    "app.title": {
        "tr": "Harita Modelleme",
        "en": "Map Modeling",
        "de": "Kartenmodellierung",
        "ar": "نمذجة الخرائط",
    },
    "panel.project_explorer": {
        "tr": "Proje Gezgini",
        "en": "Project Explorer",
        "de": "Projekt-Explorer",
        "ar": "مستكشف المشروع",
    },
    "panel.layers_buildings": {
        "tr": "Katmanlar / Binalar",
        "en": "Layers / Buildings",
        "de": "Ebenen / Gebäude",
        "ar": "الطبقات / المباني",
    },
    "panel.gis_io": {
        "tr": "GIS İçe/Dışa Aktarım",
        "en": "GIS Import/Export",
        "de": "GIS Import/Export",
        "ar": "استيراد/تصدير GIS",
    },
    "panel.inspector": {
        "tr": "Inspector",
        "en": "Inspector",
        "de": "Inspektor",
        "ar": "المفتش",
    },
    "panel.editor": {
        "tr": "Editör",
        "en": "Editor",
        "de": "Editor",
        "ar": "المحرر",
    },
    "panel.terrain_road_editor": {
        "tr": "Arazi / Yol Editörü",
        "en": "Terrain / Road Editor",
        "de": "Gelände-/Straßeneditor",
        "ar": "محرر التضاريس/الطرق",
    },
    "panel.ai_assistant": {
        "tr": "AI Asistan",
        "en": "AI Assistant",
        "de": "KI-Assistent",
        "ar": "المساعد الذكي",
    },
    "action.add_floor": {
        "tr": "Kat ekle",
        "en": "Add floor",
        "de": "Stockwerk hinzufügen",
        "ar": "إضافة طابق",
    },
    "action.remove_floor": {
        "tr": "Kat kaldır",
        "en": "Remove floor",
        "de": "Stockwerk entfernen",
        "ar": "إزالة طابق",
    },
    "action.change_roof": {
        "tr": "Çatıyı değiştir",
        "en": "Change roof",
        "de": "Dach ändern",
        "ar": "تغيير السقف",
    },
    "action.change_facade": {
        "tr": "Cepheyi değiştir",
        "en": "Change facade",
        "de": "Fassade ändern",
        "ar": "تغيير الواجهة",
    },
    "action.add_door": {
        "tr": "Kapı ekle",
        "en": "Add door",
        "de": "Tür hinzufügen",
        "ar": "إضافة باب",
    },
    "action.remove_door": {
        "tr": "Kapı kaldır",
        "en": "Remove door",
        "de": "Tür entfernen",
        "ar": "إزالة باب",
    },
    "action.add_window": {
        "tr": "Pencere ekle",
        "en": "Add window",
        "de": "Fenster hinzufügen",
        "ar": "إضافة نافذة",
    },
    "action.remove_window": {
        "tr": "Pencere kaldır",
        "en": "Remove window",
        "de": "Fenster entfernen",
        "ar": "إزالة نافذة",
    },
    "action.analyze_building": {
        "tr": "Binayı analiz et",
        "en": "Analyze building",
        "de": "Gebäude analysieren",
        "ar": "تحليل المبنى",
    },
    "status.ok": {
        "tr": "Bağlandı",
        "en": "Connected",
        "de": "Verbunden",
        "ar": "متصل",
    },
    "status.err": {
        "tr": "Hata",
        "en": "Error",
        "de": "Fehler",
        "ar": "خطأ",
    },
    "chat.placeholder": {
        "tr": "Bir komut yazın…",
        "en": "Type a command…",
        "de": "Einen Befehl eingeben…",
        "ar": "اكتب أمرًا…",
    },
    "chat.send": {
        "tr": "Gönder",
        "en": "Send",
        "de": "Senden",
        "ar": "إرسال",
    },
    "lang.toggle": {
        "tr": "EN",
        "en": "DE",
        "de": "AR",
        "ar": "TR",
    },
}


def translate(key: str, lang: str = DEFAULT_LANGUAGE) -> str:
    """`key` icin `lang` cevirisini dondurur.

    Bilinmeyen `lang` -> `DEFAULT_LANGUAGE`'a duser. Bilinmeyen `key`
    -> anahtarin kendisini (gorunur bir "eksik ceviri" isareti olarak)
    dondurur, sessizce KeyError firlatmaz (UI kodunu kirmaz).
    """
    entry = TRANSLATIONS.get(key)
    if entry is None:
        return key
    if lang not in entry:
        lang = DEFAULT_LANGUAGE
    return entry.get(lang, key)


class Translator:
    """Sabit bir dile baglanmis kucuk cevirmen (UI/CLI icin ergonomik sarici)."""

    def __init__(self, lang: str = DEFAULT_LANGUAGE) -> None:
        if lang not in SUPPORTED_LANGUAGES:
            lang = DEFAULT_LANGUAGE
        self.lang = lang

    def __call__(self, key: str) -> str:
        return translate(key, self.lang)

    def with_language(self, lang: str) -> Translator:
        return Translator(lang)

"""Roadmap V4 - Track E / Faz E16 (i18n) icin testler.

Kabul kriteri: Ayni komut ("bir kat ekle" / "add a floor") her iki dilde
de ayni `CommandIntent`'e cozulur; UI dil degistirme dugmesiyle en az 2 dil
arasinda gecis yapilabilir (bu test dosyasi Python tarafini dogrular; HTML
tarafi statik olarak `app_shell/web/index.html` icinde `data-i18n` /
`I18N` JS tablosu ile saglanir - bkz. dosyanin kendisi).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.i18n import (
    SUPPORTED_LANGUAGES,
    DEFAULT_LANGUAGE,
    TRANSLATIONS,
    translate,
    Translator,
    detect_language,
)
from harita.ai_assistant import IntentParser, IntentAction


def test_supported_languages_and_default():
    assert SUPPORTED_LANGUAGES == ("tr", "en", "de", "ar")
    assert DEFAULT_LANGUAGE == "tr"


def test_translate_known_key_both_languages():
    assert translate("action.add_floor", "tr") == "Kat ekle"
    assert translate("action.add_floor", "en") == "Add floor"


def test_translate_known_key_de_and_ar():
    assert translate("action.add_floor", "de") == "Stockwerk hinzufügen"
    assert translate("action.add_floor", "ar") == "إضافة طابق"


def test_translate_unknown_language_falls_back_to_default():
    assert translate("action.add_floor", "fr") == translate("action.add_floor", "tr")


def test_translate_unknown_key_returns_key_itself():
    assert translate("nonexistent.key", "en") == "nonexistent.key"


def test_translator_object_is_bound_to_a_language():
    t_en = Translator("en")
    assert t_en("action.analyze_building") == "Analyze building"
    t_tr = t_en.with_language("tr")
    assert t_tr("action.analyze_building") == "Binayı analiz et"


def test_translator_supports_german_and_arabic():
    t_de = Translator("de")
    assert t_de("action.analyze_building") == "Gebäude analysieren"
    t_ar = t_de.with_language("ar")
    assert t_ar("action.analyze_building") == "تحليل المبنى"


def test_every_translation_entry_has_all_supported_languages():
    for key, entry in TRANSLATIONS.items():
        for lang in SUPPORTED_LANGUAGES:
            assert lang in entry, f"{key!r} icin {lang!r} cevirisi eksik"


def test_detect_language_turkish_by_diacritics():
    assert detect_language("çatıyı düz yap") == "tr"


def test_detect_language_turkish_by_function_words():
    assert detect_language("bina ye kat ekle") == "tr"


def test_detect_language_english_by_function_words():
    assert detect_language("add a floor to this building") == "en"


def test_detect_language_defaults_to_turkish_on_empty_or_ambiguous():
    assert detect_language("") == "tr"
    assert detect_language("123 456") == "tr"


# -- Kabul kriteri: iki dilde ayni komut, ayni CommandIntent -------------- #

def test_add_floor_same_intent_both_languages():
    parser = IntentParser()
    tr_result = parser.parse("bir kat ekle")
    en_result = parser.parse("add a floor")
    assert tr_result.intents[0].action == IntentAction.ADD_FLOOR
    assert en_result.intents[0].action == IntentAction.ADD_FLOOR
    assert tr_result.intents[0].parameters["count"] == en_result.intents[0].parameters["count"] == 1


def test_remove_floor_english():
    parser = IntentParser()
    result = parser.parse("remove two floors")
    assert result.intents[0].action == IntentAction.REMOVE_FLOOR
    assert result.intents[0].parameters["count"] == 2


def test_change_roof_english_matches_turkish_semantics():
    parser = IntentParser()
    tr_result = parser.parse("çatıyı düz yap")
    en_result = parser.parse("change the roof to flat")
    assert tr_result.intents[0].action == IntentAction.CHANGE_ROOF
    assert en_result.intents[0].action == IntentAction.CHANGE_ROOF
    assert tr_result.intents[0].parameters["roof_type"] == en_result.intents[0].parameters["roof_type"] == "flat"


def test_change_facade_english():
    parser = IntentParser()
    result = parser.parse("change the facade to glass")
    assert result.intents[0].action == IntentAction.CHANGE_FACADE
    assert result.intents[0].parameters["material"] == "cam"


def test_add_remove_door_english():
    parser = IntentParser()
    add_result = parser.parse("add a door")
    remove_result = parser.parse("remove the door")
    assert add_result.intents[0].action == IntentAction.ADD_DOOR
    assert remove_result.intents[0].action == IntentAction.REMOVE_DOOR


def test_add_remove_window_english():
    parser = IntentParser()
    add_result = parser.parse("add a window")
    remove_result = parser.parse("remove the window")
    assert add_result.intents[0].action == IntentAction.ADD_WINDOW
    assert remove_result.intents[0].action == IntentAction.REMOVE_WINDOW


def test_analyze_building_english():
    parser = IntentParser()
    result = parser.parse("analyze the building")
    assert result.intents[0].action == IntentAction.ANALYZE_BUILDING


def test_multi_fragment_english_sentence():
    parser = IntentParser()
    result = parser.parse("add a floor and change the roof to flat")
    actions = [i.action for i in result.intents]
    assert IntentAction.ADD_FLOOR in actions
    assert IntentAction.CHANGE_ROOF in actions


def test_unmatched_english_fragment_still_reported_unknown():
    parser = IntentParser()
    result = parser.parse("please paint the sky purple")
    assert result.intents[0].action == IntentAction.UNKNOWN
    assert result.unmatched_fragments


def test_turkish_rules_still_take_priority_and_are_unaffected():
    """Regresyon: Ingilizce kural seti eklenmeden once davranan Turkce
    testlerin (test_phase12_ai_assistant.py) hicbiri kirilmamali - burada
    ek bir dogrulama olarak temel Turkce cumleler tekrar kontrol edilir."""
    parser = IntentParser()
    result = parser.parse("Bu binaya bir kat daha ekle ve çatıyı düz yap.")
    actions = [i.action for i in result.intents]
    assert IntentAction.ADD_FLOOR in actions
    assert IntentAction.CHANGE_ROOF in actions


if __name__ == "__main__":
    import inspect
    mod = sys.modules[__name__]
    tests = [obj for name, obj in vars(mod).items() if name.startswith("test_") and inspect.isfunction(obj)]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # pragma: no cover
            failed += 1
            print(f"ERROR {fn.__name__}: {e!r}")
    print(f"\n{passed} passed, {failed} failed out of {len(tests)}")
    sys.exit(1 if failed else 0)

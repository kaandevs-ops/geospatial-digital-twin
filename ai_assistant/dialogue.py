"""
AI Assistant - Dialogue (çok-turlu diyalog + clarification loop)
===================================================================

ROADMAP_V2 A12 — "AI Assistant (Faz 12) derinleştirme":
    - Türkçe NLU kural motoruna ek olarak çok-turlu diyalog (context/history)
      desteği.
    - Belirsiz komutlarda kullanıcıya açıklayıcı soru sorma (clarification
      loop).
    - Kabul kriteri: "bir kat ekle" -> "hangi binaya?" -> "A binası" akışı
      gibi çok adımlı senaryo testleri geçer.

Bu modül, tek-binalı `AssistantOrchestrator`'ı (Phase 12) **değiştirmeden**
üzerine bir `DialogueSession` katmanı ekler: birden fazla adlandırılmış
bina (`BuildingRegistry`) arasında, hedef belirsizse kullanıcıya soru
sorup bir sonraki mesajı yanıt olarak bekleyen basit bir durum makinesi.

Tasarım kararları
-------------------
* **Ambiguity kuralı**: eğer 2+ bina kayıtlıysa VE kullanıcı mesajında
  hiçbir bina adı geçmiyorsa, komut(lar) belirsizdir -> `needs_clarification`.
  1 bina kayıtlıysa hedef zaten açık (tek seçenek) -> doğrudan uygulanır.
* **Bina adı tespiti**: "A binası" / "A binasına" / "B binasını" gibi
  Türkçe çekim ekli kalıpları tolere eden bağımsız bir regex (intent_parser
  ile aynı `\\w*` tolere etme deseni) — registry'deki gerçek adlarla
  karşılaştırılır (büyük/küçük harf duyarsız).
* **Bekleyen (pending) durum tek seferde tek gruptur**: bir mesajdaki
  TÜM intent'ler belirsizse hepsi birlikte kuyruklanır, kullanıcı bina
  adını verince hepsi o binaya uygulanır. Yeni bir belirsiz mesaj,
  bekleyen eski grubun üzerine yazar (basit ama öngörülebilir davranış).
* **Geçmiş (history)**: her `send()` çağrısı, kullanıcı mesajını ve
  asistanın yanıtını `DialogueTurn` olarak `history`'e ekler — ana
  projenin bir sohbet UI'ında doğrudan gösterilebilir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..building_reconstruction.procedural_generator import Building
from ..editor.commands import UndoRedoStack
from .intent import CommandIntent, IntentAction
from .intent_parser import IntentParser
from .llm_providers import LLMCallError, LLMProvider, ProviderUnavailableError
from .orchestrator import AssistantOrchestrator, AssistantResult, IntentExecution


class UnknownBuildingError(Exception):
    """`BuildingRegistry` içinde bulunmayan bir bina adına erişim denemesi."""


@dataclass(slots=True)
class DialogueTurn:
    """Diyalog geçmişindeki tek bir tur (kullanıcı mesajı + asistan yanıtı)."""

    user_text: str
    assistant_reply: str
    needs_clarification: bool
    building_name: Optional[str] = None


@dataclass(slots=True)
class PendingClarification:
    """Hedef bina belirsiz olduğunda beklemeye alınan intent grubu."""

    intents: list[CommandIntent]
    original_text: str


@dataclass(slots=True)
class DialogueResult:
    """`DialogueSession.send()` çıktısı."""

    reply: str
    needs_clarification: bool
    building_name: Optional[str] = None
    assistant_result: Optional[AssistantResult] = None


class BuildingRegistry:
    """Ad -> `Building` eşlemesi; her bina için ayrı bir `UndoRedoStack`
    (ve dolayısıyla ayrı bir `AssistantOrchestrator`) tutar, böylece bir
    binadaki undo/redo diğerini etkilemez."""

    def __init__(self) -> None:
        self._buildings: dict[str, Building] = {}
        self._orchestrators: dict[str, AssistantOrchestrator] = {}

    def register(self, name: str, building: Building, parser: IntentParser | None = None) -> None:
        self._buildings[name] = building
        self._orchestrators[name] = AssistantOrchestrator(
            building, parser=parser, undo_stack=UndoRedoStack()
        )

    def names(self) -> list[str]:
        return list(self._buildings.keys())

    def get_building(self, name: str) -> Building:
        try:
            return self._buildings[name]
        except KeyError:
            raise UnknownBuildingError(name) from None

    def get_orchestrator(self, name: str) -> AssistantOrchestrator:
        try:
            return self._orchestrators[name]
        except KeyError:
            raise UnknownBuildingError(name) from None

    def __len__(self) -> int:
        return len(self._buildings)

    def __contains__(self, name: str) -> bool:
        return name in self._buildings


_QUESTION_RE = re.compile(
    r"\b(neden|niçin|nicin|niye|nasıl|nasil|kaç|kac)\b.*\?\s*$|\?\s*$",
    re.IGNORECASE,
)

_EXPLAIN_SYSTEM_PROMPT = (
    "Sen bir bina modelleme aracinin asistanisin. Kullaniciya, elindeki "
    "bina verisine (kat sayisi, cati tipi, cephe malzemesi, oda sayisi "
    "gibi) dayanarak KISA (2-4 cumle), Turkce ve dogal bir aciklama "
    "yap. Yalnizca sana verilen veriye dayan, uydurma sayi/ozellik "
    "ekleme; veride olmayan bir sey soruluyorsa bunu acikca soyle."
)


def _building_context_summary(building: Building) -> str:
    """LLM'e verilecek kısa, yapılandırılmış bina özeti (prompt bağlamı).
    Kullanıcıya gösterilmez - yalnızca `answer_question`'ın iç prompt'unda
    kullanılır."""
    fp = building.footprint
    parts = [
        f"bina_tipi={fp.building_type or 'bilinmiyor'}",
        f"kat_sayisi={len(building.floors)}",
        f"cati_tipi={getattr(building.roof, 'roof_type', 'bilinmiyor')}",
        f"cephe_malzemesi={getattr(building.facade, 'material', 'bilinmiyor')}",
    ]
    total_rooms = sum(len(f.rooms) for f in building.floors)
    parts.append(f"toplam_oda_sayisi={total_rooms}")
    return ", ".join(parts)


_COREFERENCE_RE = re.compile(
    r"\bonu\b|\bona\b(?!\s*bina)|\baynı\w*\b|\bben\s*zer\w*\b|\bbenzer\w*\b",
    re.IGNORECASE,
)


def _find_all_building_mentions(text: str, candidate_names: list[str]) -> list[tuple[int, str]]:
    """Metindeki TUM bina adi gecislerini (konum, ad) olarak, metindeki
    sirayla dondurur. Ayni ada birden fazla deginilirse (cekim ekleriyle)
    her gecis ayri bir giris olur. En uzun aday ad once denenir (or.
    "A Blok" > "A"), ve bir bolgede zaten eslesen daha kisa bir isim
    tekrar eslestirilmez (cakisma onleme)."""
    lowered = text.lower()
    claimed: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < c_end and end > c_start for c_start, c_end in claimed)

    mentions: list[tuple[int, str]] = []
    for name in sorted(candidate_names, key=len, reverse=True):
        pattern = rf"\b{re.escape(name.lower())}\b\w*"
        for m in re.finditer(pattern, lowered):
            if overlaps(m.start(), m.end()):
                continue
            claimed.append((m.start(), m.end()))
            mentions.append((m.start(), name))
    mentions.sort(key=lambda item: item[0])
    return mentions


def _find_building_name(text: str, candidate_names: list[str]) -> Optional[str]:
    """Metinde geçen bir bina adını bulur. Önce Türkçe çekim ekli
    "<ad> binası/binasına/binasını" kalıbını, sonra düz ad geçişini dener.
    En uzun eşleşen aday adı tercih edilir (örn. "A Blok" > "A")."""
    lowered = text.lower().strip()

    for name in sorted(candidate_names, key=len, reverse=True):
        pattern = rf"\b{re.escape(name.lower())}\b\s*bina\w*"
        if re.search(pattern, lowered):
            return name

    for name in sorted(candidate_names, key=len, reverse=True):
        if name.lower().strip() == lowered:
            return name
        if re.search(rf"\b{re.escape(name.lower())}\b", lowered):
            return name

    return None


class DialogueSession:
    """Çok-turlu, çoklu-bina destekli diyalog oturumu.

    Tek binalı kullanım (Phase 12 orijinal davranışı) tamamen geriye
    uyumludur: registry'de tek bina varken hiçbir belirsizlik oluşmaz,
    her mesaj doğrudan uygulanır.
    """

    def __init__(
        self,
        registry: BuildingRegistry,
        parser: IntentParser | None = None,
        llm_provider: Optional[LLMProvider] = None,
    ) -> None:
        self.registry = registry
        self.parser = parser or IntentParser()
        self.llm_provider = llm_provider
        self.history: list[DialogueTurn] = []
        self._pending: Optional[PendingClarification] = None

    @property
    def awaiting_clarification(self) -> bool:
        return self._pending is not None

    def send(self, text: str) -> DialogueResult:
        if self._pending is not None:
            result = self._resolve_pending(text)
        else:
            result = self._handle_new_message(text)

        self.history.append(
            DialogueTurn(
                user_text=text,
                assistant_reply=result.reply,
                needs_clarification=result.needs_clarification,
                building_name=result.building_name,
            )
        )
        return result

    # ------------------------------------------------------------------ #

    def _handle_new_message(self, text: str) -> DialogueResult:
        names = self.registry.names()

        # Faz AI-2: aciklayici soru tespiti ("neden bu binanin catisi hip
        # oldu?" gibi) - bir KOMUT degil, mevcut bina verisine dayali bir
        # aciklama istegidir. Kural motoru bunu bir intent'e cozemeyecegi
        # icin, once acikca soru-kalibinda olup olmadigina bakariz; oyleyse
        # dogrudan answer_question'a yonlendiririz (intent parse edilmez).
        if names and _QUESTION_RE.search(text):
            explicit_name = _find_building_name(text, names)
            target = explicit_name or (names[0] if len(names) == 1 else None)
            reply = self.answer_question(text, building_name=target)
            return DialogueResult(reply=reply, needs_clarification=False, building_name=target)

        # Faz D17: coklu-hedef tespiti - metinde 2+ FARKLI bina adina
        # (ayni cumlede) deginiliyorsa, her hedefi kendi segmentiyle
        # bagimsiz olarak yorumla ("A binasina bir kat ekle, B'ye de
        # aynisini yap" -> A ve B ayri ayri, coreference ile).
        if names:
            mentions = _find_all_building_mentions(text, names)
            distinct_names = {name for _, name in mentions}
            if len(distinct_names) >= 2:
                return self._handle_multi_target(text, mentions)

        parse_result = self.parser.parse(text)
        intents = parse_result.intents

        if not intents:
            return DialogueResult(reply="Anlaşılamadı, tekrar dener misin?", needs_clarification=False)

        explicit_name = _find_building_name(text, names) if names else None

        if explicit_name is not None:
            return self._execute_on(explicit_name, intents)

        if len(self.registry) <= 1:
            if len(self.registry) == 0:
                return DialogueResult(
                    reply="Henüz kayıtlı bir bina yok.", needs_clarification=False
                )
            only_name = names[0]
            return self._execute_on(only_name, intents)

        # 2+ bina var ve mesajda hiçbiri geçmiyor -> belirsiz, soru sor
        self._pending = PendingClarification(intents=intents, original_text=text)
        options = ", ".join(names)
        return DialogueResult(
            reply=f"Hangi binaya işlem uygulansın? Seçenekler: {options}",
            needs_clarification=True,
        )

    def _resolve_pending(self, text: str) -> DialogueResult:
        assert self._pending is not None
        names = self.registry.names()
        resolved = _find_building_name(text, names)
        if resolved is None:
            options = ", ".join(names)
            return DialogueResult(
                reply=(
                    f"Anlayamadım — lütfen şu isimlerden birini söyle: {options}"
                ),
                needs_clarification=True,
            )
        pending = self._pending
        self._pending = None
        return self._execute_on(resolved, pending.intents)

    def _execute_on(self, building_name: str, intents: list[CommandIntent]) -> DialogueResult:
        orchestrator = self.registry.get_orchestrator(building_name)
        assistant_result = orchestrator.execute_intents(intents)
        reply = " ".join(assistant_result.messages) or "İşlem tamamlandı."
        return DialogueResult(
            reply=reply,
            needs_clarification=False,
            building_name=building_name,
            assistant_result=assistant_result,
        )

    def _handle_multi_target(
        self, text: str, mentions: list[tuple[int, str]]
    ) -> DialogueResult:
        """Ayni mesajda 2+ farkli binaya yonlendirilen komutlari, her
        bina icin ayri bir metin segmentine bolerek isler.

        Segmentleme: her bina-adi gecisinden bir sonraki gecise kadar
        olan metin parcasi, o binaya ait komut(lar)i icerir (\"A binasina
        bir kat ekle, B'ye de aynisini yap\" -> [\"A binasina bir kat
        ekle, \", \"B'ye de aynisini yap\"]). Bir segment somut bir komut
        eylemi icermiyor ama coreference ifadesi (\"onu da\", \"aynisini\")
        iceriyorsa, bir onceki segmentte basariyla eslesen intent'ler
        aynen tekrar uygulanir.
        """
        starts = [m[0] for m in mentions] + [len(text)]
        segments: list[tuple[str, str]] = []
        for i, (start, name) in enumerate(mentions):
            end = starts[i + 1]
            segments.append((name, text[start:end]))

        last_intents: list[CommandIntent] | None = None
        replies: list[str] = []
        last_result: Optional[DialogueResult] = None
        touched_names: list[str] = []

        for name, segment in segments:
            parse_result = self.parser.parse(segment)
            actionable = [
                intent for intent in parse_result.intents if intent.action is not IntentAction.UNKNOWN
            ]

            if not actionable and _COREFERENCE_RE.search(segment) and last_intents is not None:
                # coreference: bir onceki basarili komut grubunu tekrarla
                intents = last_intents
            elif actionable:
                intents = actionable
            else:
                intents = parse_result.intents  # UNKNOWN dahil, kullaniciya bildirilir

            if any(intent.action is not IntentAction.UNKNOWN for intent in intents):
                last_intents = intents

            result = self._execute_on(name, intents)
            replies.append(f"{name}: {result.reply}")
            last_result = result
            touched_names.append(name)

        combined_reply = " ".join(replies) if replies else "Anlaşılamadı, tekrar dener misin?"
        return DialogueResult(
            reply=combined_reply,
            needs_clarification=False,
            building_name=", ".join(touched_names),
            assistant_result=last_result.assistant_result if last_result else None,
        )

    def answer_question(self, text: str, building_name: Optional[str] = None) -> str:
        """`docs/AI_INTEGRATION_MAP.md` fırsat #2: komut değil, açıklayıcı
        bir soruyu ("neden bu binanın çatısı hip oldu?") mevcut bina
        verisine dayanarak yanıtlar. `IntentParser`/`AssistantOrchestrator`
        akışına hiç girmez - hiçbir komut çalıştırmaz, hiçbir mesh/veri
        değiştirmez, salt-okunur bir açıklama katmanıdır.

        Sağlayıcı yoksa veya çağrı başarısız olursa (aynı `report_narrator`/
        `result_narrator` ilkesi), sessizce genel bir şablon yanıta düşer -
        hiçbir zaman istisna fırlatmaz.
        """
        building_name = building_name or (
            self.registry.names()[0] if len(self.registry) == 1 else None
        )
        if building_name is None or building_name not in self.registry:
            return (
                "Hangi binayı kastettiğini anlayamadım - lütfen bina adını "
                "belirt."
            )
        building = self.registry.get_building(building_name)
        fallback = (
            f"{building_name}: bu soruya şu an yalnızca bina verisiyle "
            f"({_building_context_summary(building)}) genel bir yanıt "
            "verebiliyorum; daha ayrıntılı açıklama için bir LLM sağlayıcı "
            "bağlaman gerekir."
        )
        if self.llm_provider is None:
            return fallback
        prompt = (
            f"Bina verisi: {_building_context_summary(building)}\n\n"
            f"Kullanıcı sorusu: {text}"
        )
        try:
            reply = self.llm_provider.complete(prompt, system=_EXPLAIN_SYSTEM_PROMPT).strip()
        except (ProviderUnavailableError, LLMCallError):
            return fallback
        return reply or fallback

    def cancel_pending(self) -> None:
        """Bekleyen açıklama isteğini iptal eder (kullanıcı vazgeçerse)."""
        self._pending = None


__all__ = [
    "UnknownBuildingError",
    "DialogueTurn",
    "PendingClarification",
    "DialogueResult",
    "BuildingRegistry",
    "DialogueSession",
]

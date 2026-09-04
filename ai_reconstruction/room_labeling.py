"""AI Reconstruction - Oda Etiketleme (Room Labeling)
=======================================================

`docs/AI_INTEGRATION_MAP.md` fırsat #1: "bir LLM'e 'bu bina bir aile
evi, 4 kişilik' gibi bağlam verilip oda *isimlendirme*/kullanım amacı
önerisi (örn. 'üst kat 2. oda -> çalışma odası, güney cepheye bakıyor')
istenebilir - sayısal geometriyi değiştirmez, yalnızca etiketleme/
açıklama katmanı ekler."

Önemli tasarım kararı: bu modül `Room.polygon`/`Room.room_type`
geometrisine DOKUNMAZ (roadmap ilkesi: mesh/geometri üretimi
deterministik kalmalı, bkz. `AI_INTEGRATION_MAP.md` "kasıtlı olarak AI
eklenmeyen yerler"). Sadece var olan `Room` listesine, kullanıcı
bağlamına göre insan-okunur bir `label` + kısa bir `rationale` (gerekçe)
ekler - bu tamamen ek/opsiyonel bir metaveri katmanıdır.

Sağlayıcı yoksa/başarısız olursa (aynı `report_narrator`/
`result_narrator` ilkesi), her oda için `room_type` + basit yön bilgisine
dayanan kural-tabanlı bir şablon etikete sessizce düşülür.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from ..building_reconstruction.room_generator import Room
from ..ai_assistant.llm_providers import LLMCallError, LLMProvider, ProviderUnavailableError

__all__ = ["RoomLabel", "suggest_room_labels"]


@dataclass(slots=True)
class RoomLabel:
    """Tek bir oda için önerilen kullanım etiketi + kısa gerekçe.
    `room.room_type` (geometri/uygunluk denetiminde kullanılan gerçek tip)
    değişmez - bu yalnızca ek bir görüntüleme/etiketleme katmanıdır."""

    room_id: int
    room_type: str
    label: str
    rationale: str


_LABELING_SYSTEM_PROMPT = (
    "Sen bir ic mimarsin. Sana bir binanin oda listesi (tip ve metrekare) "
    "ve kisa bir kullanici baglami verilecek. Her oda icin, teknik oda "
    "tipini DEGISTIRMEDEN, o odaya uygun kisa bir kullanim etiketi ve "
    "1 cumlelik gerekce oner. YALNIZCA gecerli JSON dizisi dondur, baska "
    "hicbir metin ekleme. Format: "
    '[{"room_id": 1, "label": "...", "rationale": "..."}, ...]'
)


def _fallback_label(room: Room) -> RoomLabel:
    """Sağlayıcı yokken/başarısız olduğunda kullanılan basit, tamamen
    kural tabanlı etiket (room_type'ın insan-okunur hali + alan bilgisi)."""
    readable = room.room_type.replace("_", " ").capitalize()
    return RoomLabel(
        room_id=room.room_id,
        room_type=room.room_type,
        label=readable,
        rationale=f"{room.area_m2:.1f} m² alanlı standart {readable.lower()}.",
    )


def suggest_room_labels(
    rooms: list[Room],
    context: str = "",
    provider: Optional[LLMProvider] = None,
) -> list[RoomLabel]:
    """Verilen oda listesi + serbest metin bağlam (örn. "4 kişilik aile
    evi, çalışan bir çift") için oda başına kullanım etiketi önerir.

    `provider=None` (varsayılan) veya çağrı başarısız/geçersiz JSON
    dönerse, HER oda için sessizce `_fallback_label`'e düşülür - hiçbir
    zaman istisna fırlatmaz, hiçbir zaman eksik liste döndürmez (girdi
    kadar çıktı garantisi).
    """
    fallback = [_fallback_label(room) for room in rooms]
    if provider is None or not rooms:
        return fallback

    room_summaries = [
        {"room_id": r.room_id, "room_type": r.room_type, "area_m2": round(r.area_m2, 1)}
        for r in rooms
    ]
    prompt = (
        f"Kullanıcı bağlamı: {context or '(belirtilmedi)'}\n\n"
        f"Odalar: {json.dumps(room_summaries, ensure_ascii=False)}"
    )
    try:
        raw = provider.complete(prompt, system=_LABELING_SYSTEM_PROMPT).strip()
    except (ProviderUnavailableError, LLMCallError):
        return fallback

    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return fallback
    except json.JSONDecodeError:
        return fallback

    by_id = {r.room_id: r for r in rooms}
    result: list[RoomLabel] = []
    seen_ids: set[int] = set()
    for item in parsed:
        if not isinstance(item, dict):
            continue
        rid = item.get("room_id")
        room = by_id.get(rid)
        if room is None:
            continue
        label = item.get("label")
        rationale = item.get("rationale")
        if not isinstance(label, str) or not label.strip():
            continue
        result.append(RoomLabel(
            room_id=rid, room_type=room.room_type,
            label=label.strip(),
            rationale=rationale.strip() if isinstance(rationale, str) else "",
        ))
        seen_ids.add(rid)

    # LLM bazı odaları atladıysa/uydurma id döndürdüyse, eksik kalanları
    # şablon etiketle tamamla - "girdi kadar çıktı" garantisi hiçbir
    # zaman bozulmaz.
    for room in rooms:
        if room.room_id not in seen_ids:
            result.append(_fallback_label(room))

    result.sort(key=lambda rl: rl.room_id)
    return result

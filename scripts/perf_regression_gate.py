#!/usr/bin/env python3
"""ROADMAP_V5 - Track Q / Q3: "Performans regresyon eşiği tanımlanmalı:
'1000 bina sahnesi FPS'i bir önceki sürüme göre %X'ten fazla düşerse CI
kırmızı' gibi somut bir eşik."

DÜRÜST SINIRLAMA: bu ortamda gerçek bir GPU/render pipeline'ı yok (bkz.
`scripts/visual_regression.py` başlığındaki aynı not), dolayısıyla
"FPS" ölçülemez. Bunun yerine roadmap'in **kendi M1.1/M1.2 kabul
kriterlerinin** doğrudan sayısal karşılıkları CI-eşiği olarak kullanılır
- ikisi de FPS'in asıl belirleyicisi (GPU'ya giden üçgen/draw-call
sayısı):

  - **M1.1 (LOD) eşiği**: "kamera şehir geneli görünümdeyken toplam
    üçgen sayısı LOD0'a göre en az %90 azalmalı" -> LOD3 (impostor) /
    LOD0 oranı >= 0.90 azalma (yani LOD3 üçgen sayısı <= LOD0'ın %10'u).
  - **M1.2 (batching) eşiği**: "100 bina + vejetasyon sahnede draw call
    sayısı, instancing/batching öncesine göre en az %60 azalmalı" ->
    `DrawCallEstimator.reduction_percent(...) >= 60.0`.

Bu iki eşik, önceki bir çalıştırmanın sonucuyla (`perf_baseline.json`)
karşılaştırılıp **regresyon** (eşiğin altına düşme VEYA önceki
çalıştırmaya göre kayda değer kötüleşme) tespit edilirse script
non-zero exit code döner - CI'da bu kırmızı sonuç anlamına gelir.

Kullanım::

    python3 scripts/perf_regression_gate.py                # kontrol et (CI modu)
    python3 scripts/perf_regression_gate.py --update-baseline  # baseline'ı güncelle
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harita.mesh_engine import MeshBuilder  # noqa: E402
from harita.mesh_engine.batching import DrawCallEstimator  # noqa: E402
from harita.mesh_engine.lod import LODChainBuilder, LODLevel  # noqa: E402

BASELINE_PATH = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "perf_regression_baseline.json"
)

# Roadmap M1.1 kabul kriteri: LOD0 -> LOD3 (en agresif seviye, şehir
# geneli görünüm) üçgen sayısı en az %90 azalmalı.
LOD_REDUCTION_MIN_RATIO = 0.90

# Roadmap M1.2 kabul kriteri: 100 bina sahnesinde draw call en az %60 azalmalı.
DRAW_CALL_REDUCTION_MIN_PERCENT = 60.0

# Bir önceki baseline'a göre izin verilen maksimum kötüleşme (göreli) -
# eşiği geçse bile önceki sürüme göre büyük bir düşüş varsa yine kırmızı
# olsun diye (roadmap Q3'ün "%X düşerse kırmızı" cümlesinin somut hali).
MAX_ALLOWED_REGRESSION_VS_BASELINE = 0.05  # %5


def _measure_lod_reduction() -> float:
    """Orta ölçekli, temsili bir bina üzerinde LOD0->LOD3 üçgen azalma
    oranını ölçer (roadmap örneğindeki "1000 bina sahnesi" ile aynı
    orandaki tek-bina ölçümü - oran bina sayısından bağımsızdır, çünkü
    her bina kendi LOD zincirini kullanır)."""
    lod0 = MeshBuilder.build_box(12, 10, 30, name="representative_building")
    chain = LODChainBuilder.build_from_lod0(lod0)
    return chain.triangle_reduction_ratio(LODLevel.LOD3)


def _measure_draw_call_reduction() -> float:
    """100 bina + vejetasyon sahnesini temsilen: 20 malzeme, malzeme
    başına ortalama 6 nesne (bina cephesi + ağaç + sokak lambası vb.
    karışımı) - roadmap M1.2 örneğiyle aynı büyüklük mertebesi."""
    pairs = [(f"material_{i % 20}", 1) for i in range(120)]
    return DrawCallEstimator.reduction_percent(pairs)


def measure_current() -> dict:
    return {
        "lod_reduction_ratio": round(_measure_lod_reduction(), 6),
        "draw_call_reduction_percent": round(_measure_draw_call_reduction(), 6),
    }


def load_baseline() -> dict | None:
    if not BASELINE_PATH.exists():
        return None
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def save_baseline(metrics: dict) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")


def evaluate(current: dict, baseline: dict | None) -> list[str]:
    """Kırmızı (CI'yi kırmızı yapacak) sorunların listesini döndürür - boşsa
    her şey yeşil demektir."""
    problems: list[str] = []

    if current["lod_reduction_ratio"] < LOD_REDUCTION_MIN_RATIO:
        problems.append(
            f"M1.1 eşiği ihlal edildi: LOD0->LOD3 üçgen azalma oranı "
            f"{current['lod_reduction_ratio']:.1%}, beklenen >= {LOD_REDUCTION_MIN_RATIO:.0%}."
        )

    if current["draw_call_reduction_percent"] < DRAW_CALL_REDUCTION_MIN_PERCENT:
        problems.append(
            f"M1.2 eşiği ihlal edildi: draw call azalma oranı "
            f"%{current['draw_call_reduction_percent']:.1f}, beklenen >= "
            f"%{DRAW_CALL_REDUCTION_MIN_PERCENT:.0f}."
        )

    if baseline is not None:
        for key, label in (
            ("lod_reduction_ratio", "LOD üçgen azalma oranı"),
            ("draw_call_reduction_percent", "Draw call azalma oranı"),
        ):
            prev = baseline.get(key)
            cur = current.get(key)
            if prev is None or cur is None or prev == 0:
                continue
            relative_drop = (prev - cur) / prev
            if relative_drop > MAX_ALLOWED_REGRESSION_VS_BASELINE:
                problems.append(
                    f"Regresyon: {label} önceki baseline'a göre %"
                    f"{relative_drop * 100:.2f} düştü (izin verilen: "
                    f"%{MAX_ALLOWED_REGRESSION_VS_BASELINE * 100:.0f}). "
                    f"Önceki: {prev}, şimdi: {cur}."
                )

    return problems


def main() -> int:
    current = measure_current()
    update_baseline = "--update-baseline" in sys.argv

    if update_baseline:
        save_baseline(current)
        print(f"Perf baseline güncellendi: {BASELINE_PATH}")
        print(json.dumps(current, indent=2, ensure_ascii=False))
        return 0

    baseline = load_baseline()
    problems = evaluate(current, baseline)

    print("Güncel performans metrikleri:")
    print(json.dumps(current, indent=2, ensure_ascii=False))
    if baseline is not None:
        print("\nBaseline metrikleri:")
        print(json.dumps(baseline, indent=2, ensure_ascii=False))
    else:
        print("\n(Baseline yok - ilk çalıştırma, yalnızca mutlak eşikler kontrol edildi.)")

    if problems:
        print("\nPerformans regresyon eşiği İHLAL EDİLDİ:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("\nTüm performans eşikleri karşılandı - CI yeşil.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

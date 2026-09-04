#!/usr/bin/env python3
"""Roadmap V4 - Track C / C1: Roadmap <-> kod senkronizasyon doğrulayıcısı.

`ROADMAP.md`, `ROADMAP_V2.md`, `ROADMAP_V3.md`, `ROADMAP_V4.md` içindeki her
``tests/test_*.py`` referansının:
  1. Diskte gerçekten var olduğunu,
  2. (varsayılan olarak, `--skip-pytest` verilmezse) `pytest` ile gerçekten
     YEŞİL geçtiğini,
doğrudan doğrular. Böylece bir roadmap dosyasının "tamamlandı" dediği bir
fazın test dosyası silinmiş/bozulmuşsa CI kırmızı olur; roadmap'in kendisi
"sıradaki iş" olarak gösterdiği ama aslında testi zaten var+yeşil olan
fazlar için de bir UYARI (hata değil - bu doküman-metni sorunu, kod
sorunu değil) basar.

Kullanım::

    python3 scripts/verify_roadmap_sync.py             # tam kontrol (testleri çalıştırır)
    python3 scripts/verify_roadmap_sync.py --skip-pytest  # yalnızca dosya varlığı
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROADMAP_FILES = [
    ROOT / "ROADMAP.md",
    ROOT / "ROADMAP_V2.md",
    ROOT / "ROADMAP_V3.md",
    ROOT / "ROADMAP_V4.md",
    # Faz 5.6 (dokümantasyon birleştirme): yeni_roadmap.md artık güncel,
    # nihai yol haritası — ROADMAP*.md dosyaları arşiv notuyla işaretlendi
    # ama silinmedi (onlarca dosyada yol referansı var). Bu script'in
    # senkron denetimi artık ikisini de kapsıyor.
    ROOT / "yeni_roadmap.md",
]

TEST_REF_RE = re.compile(r"tests/test_[A-Za-z0-9_]+\.py")

# Bir roadmap dosyasında bu kelimelerden biri bir test referansına yakın
# (aynı satırda veya bir önceki satırda) geçiyorsa ve dosya "sıradaki iş" /
# "açık" gibi bir bağlamda anılıyorsa ama test aslında var+yeşilse, bu
# doküman-senkron uyarısıdır (V4 denetim maddesi #1).
OPEN_MARKERS = ("sırada", "kalan", "açık", "Sıradaki Adım", "⏳")
DONE_MARKERS = ("✅", "Tamamlandı", "tamamlandı")


def _find_test_refs(text: str) -> set[str]:
    return set(TEST_REF_RE.findall(text))


def _line_context(text: str, ref: str) -> str:
    idx = text.find(ref)
    if idx == -1:
        return ""
    start = text.rfind("\n", 0, idx)
    end = text.find("\n", idx)
    # bir önceki satırı da dahil et (bağlam için)
    prev_start = text.rfind("\n", 0, start) if start != -1 else -1
    return text[max(prev_start, 0):end if end != -1 else len(text)]


def main() -> int:
    skip_pytest = "--skip-pytest" in sys.argv
    errors: list[str] = []
    warnings: list[str] = []
    all_refs: set[str] = set()

    for rf in ROADMAP_FILES:
        if not rf.exists():
            continue
        text = rf.read_text(encoding="utf-8")
        refs = _find_test_refs(text)
        all_refs |= refs
        for ref in refs:
            path = ROOT / ref
            ctx = _line_context(text, ref)
            if not path.exists():
                # "yeni `tests/...`" ifadesi roadmap V4'te henüz yazılmamış,
                # PLANLANAN bir test dosyasını işaret eder (V4 bir planlama
                # dokümanıdır) - bu bir hata değildir, henüz koda dökülmemiş
                # bir Track E/R maddesidir. Diğer tüm durumlarda (ROADMAP,
                # ROADMAP_V2, ROADMAP_V3 - geriye dönük/tamamlanmış işi
                # anlatan dokümanlar) eksik dosya gerçek bir senkron hatasıdır.
                normalized = ctx.replace("\n", " ").lower()
                if f"yeni `{ref}".lower() in normalized:
                    warnings.append(
                        f"{rf.name}: {ref} henüz oluşturulmamış planlanan bir "
                        "test dosyası (Track E/R - henüz koda dökülmedi)"
                    )
                else:
                    errors.append(f"{rf.name}: {ref} referans veriyor ama dosya yok")
                continue
            looks_open = any(m in ctx for m in OPEN_MARKERS)
            looks_done = any(m in ctx for m in DONE_MARKERS)
            if looks_open and not looks_done:
                warnings.append(
                    f"{rf.name}: {ref} dosyası VAR ama roadmap metni bunu "
                    "'açık/sırada' gibi gösteriyor olabilir (doküman-senkron "
                    "kontrolü önerilir)"
                )

    if not skip_pytest and all_refs:
        rel_paths = sorted(p for p in all_refs if (ROOT / p).exists())
        print(f"pytest ile {len(rel_paths)} referanslı test dosyası çalıştırılıyor...")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *rel_paths],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        print(result.stdout[-4000:])
        if result.returncode != 0:
            errors.append(
                "Roadmap'te referans verilen test dosyalarından en az biri "
                f"pytest'te KIRMIZI (returncode={result.returncode})"
            )

    if warnings:
        print("\n--- UYARILAR (doküman-senkron, hata değil) ---")
        for w in warnings:
            print(f"  ⚠ {w}")

    if errors:
        print("\n--- HATALAR ---")
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    print(f"\nRoadmap senkron kontrolü tamam: {len(all_refs)} test referansı doğrulandı. ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())

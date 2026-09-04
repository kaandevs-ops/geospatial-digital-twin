"""
Predictor Protocol
===================

Roadmap Phase 4 - "AI Reconstruction" ortak arayüzü.

Tüm AI Reconstruction bileşenleri iki katmanlı düşünülür:
    (a) kural/istatistik tabanlı tahminciler (bağımlılıksız, hemen çalışır)
    (b) opsiyonel ML modeli entegre noktaları (`Predictor` Protocol'ü ile -
        kullanıcı kendi eğitilmiş modelini takabilir).

Varsayılan olarak her bileşen kendi `HeuristicPredictor`'ını kullanır;
`predictor=` parametresiyle bu Protocol'ü uygulayan herhangi bir nesne
(örn. bir ONNX/sklearn model sarmalayıcısı) enjekte edilebilir.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Predictor(Protocol):
    """Harici / eğitilmiş bir ML modelinin uyması gereken minimal arayüz."""

    def predict(self, features: dict) -> dict:
        """`features` sözlüğünden bir tahmin sözlüğü döner.
        Dönüş anahtarları bileşene özgüdür (bkz. ilgili modülün docstring'i)."""
        ...

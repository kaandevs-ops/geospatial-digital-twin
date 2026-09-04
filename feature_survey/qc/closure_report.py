"""FAZ S5 — Poligon (traverse) kapatma raporu.

`geodetic_engine.traverse` içindeki gerçek `AngularClosure`/`LinearClosure`
hesaplarının üzerine, gerçek tolerans karşılaştırması ekler — kapanma hatası
her zaman "kabul edilebilir" olarak raporlanmaz, tolerans aşılırsa açıkça
`False`/uyarı döner (roadmap S5 kabul kriteri: negatif test senaryosu).

Tolerans kaynağı (`standard_reference`) zorunlu belgeleme alanıdır — bu
modül hiçbir sabit sayıyı "yönetmelik toleransı" diye uydurmaz; çağıran kod
projeye uygulanan gerçek standardı (örn. TUJJB/HKMO Büyük Ölçekli Harita ve
Harita Bilgileri Üretim Yönetmeliği'nin ilgili poligon tolerans formülü) bu
alanla birlikte iletir.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..geodetic_engine.traverse import AngularClosure, InsufficientDataError, LinearClosure


@dataclass(slots=True)
class ClosureQcReport:
    angular_closure: AngularClosure
    linear_closure: LinearClosure
    angular_tolerance_gon: float
    max_relative_precision: float  # örn. 1/5000 = 0.0002 (kapanma_mesafesi/çevre için ÜST sınır)
    standard_reference: str
    angular_pass: bool
    linear_pass: bool

    @property
    def all_passed(self) -> bool:
        """Sabit `True` DEĞİLDİR — tolerans aşıldığında `False` döner
        (roadmap S5 kabul kriteri: negatif test senaryosu zorunlu)."""
        return self.angular_pass and self.linear_pass


def generate_closure_report(
    angular_closure: AngularClosure,
    linear_closure: LinearClosure,
    angular_tolerance_gon: float,
    max_relative_precision: float,
    standard_reference: str,
) -> ClosureQcReport:
    """Açısal ve doğrusal kapanma hatalarını, verilen (uydurulmamış, gerçek
    standarttan gelen) tolerans değerleriyle karşılaştırır.

    `max_relative_precision`: bağıl hata için üst sınır, örn. 1/5000 için
    `0.0002` (yani `linear_closure.relative_precision <= max_relative_precision`
    olmalı — daha KÜÇÜK bağıl hata daha iyi hassasiyet demektir).
    """

    if angular_tolerance_gon <= 0:
        raise InsufficientDataError(
            "Açısal tolerans pozitif olmalı — gerçek uygulanan standarttan gelen bir değer bekler."
        )
    if max_relative_precision <= 0:
        raise InsufficientDataError(
            "Bağıl hassasiyet üst sınırı pozitif olmalı (örn. 1/5000 için 0.0002)."
        )
    if not standard_reference or not standard_reference.strip():
        raise InsufficientDataError(
            "`standard_reference` boş olamaz — tolerans kaynağı denetlenebilirlik için belgelenmelidir."
        )

    angular_pass = abs(angular_closure.closure_error_gon) <= angular_tolerance_gon
    linear_pass = linear_closure.relative_precision <= max_relative_precision

    return ClosureQcReport(
        angular_closure=angular_closure,
        linear_closure=linear_closure,
        angular_tolerance_gon=angular_tolerance_gon,
        max_relative_precision=max_relative_precision,
        standard_reference=standard_reference,
        angular_pass=angular_pass,
        linear_pass=linear_pass,
    )

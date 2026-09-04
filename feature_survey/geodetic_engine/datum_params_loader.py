"""FAZ S2.4 — resmi datum dönüşüm parametre setlerinin (HGK/TUSAGA-Aktif,
EGM2008/TG-03 jeoit ondülasyonu) **dış kaynaktan** yüklenmesi.

Roadmap ROADMAP_V6.md S2.4: "gerçek sayılar kod içine gömülmeyecek, kaynak
dosyadan/README'de referanslı şekilde yüklenecek ki güncellenebilsin."

Bu modül, `datum_transform.DatumTransformParameters`'ı **icat edilmiş**
sayılarla doldurmaz -- resmi kaynaktan (HGK'nın "Türkiye Ulusal Temel GPS
Ağı" / TUSAGA-Aktif yayınları) elde edilen parametreleri, kullanıcının
yerleştirdiği bir JSON dosyasından okur, şema/eksiklik doğrulaması yapar ve
yalnızca gerçekten dolu, kaynağı belgelenmiş bir parametre setini kabul
eder. Dosya yoksa veya alanlar eksikse/placeholder ise **açıkça hata**
fırlatılır (`InsufficientDataError`) -- roadmap'in "sessizce varsayılan/
uydurma değer üretilmez" ilkesiyle birebir tutarlı.

Kullanım:
    from .datum_params_loader import load_datum_transform_params
    params = load_datum_transform_params(Path("datum_params/itrf96_to_ed50_turkey.json"))
    # params artık gerçek, kaynağı belgelenmiş bir DatumTransformParameters

JSON şeması (bkz. `datum_params/README.md` ve `datum_params/*.example.json`):
    {
      "tx_m": <float>, "ty_m": <float>, "tz_m": <float>,
      "rx_arcsec": <float>, "ry_arcsec": <float>, "rz_arcsec": <float>,
      "scale_ppm": <float>,
      "source": "<resmi kaynak adı + yayın/erişim tarihi>"
    }
"""

from __future__ import annotations

import json
from pathlib import Path

from .datum_transform import DatumTransformParameters, InsufficientDataError

_REQUIRED_NUMERIC_FIELDS = (
    "tx_m", "ty_m", "tz_m", "rx_arcsec", "ry_arcsec", "rz_arcsec", "scale_ppm",
)

#: Şablon dosyalarında kullanılan, "gerçek değil" işaretleyici. Bu değer
#: `source` alanında bulunursa yükleme reddedilir -- kullanıcı örnek/şablon
#: dosyayı düzenlemeden fiilen kullanmaya çalışıyor demektir.
_PLACEHOLDER_MARKER = "REPLACE_ME"


def load_datum_transform_params(path: str | Path) -> DatumTransformParameters:
    """Diskten bir JSON dosyası okuyup doğrulanmış bir
    `DatumTransformParameters` döndürür.

    Doğrulama adımları (hepsi zorunlu, hiçbiri sessizce atlanmaz):
    1. Dosya var mı ve geçerli JSON mu?
    2. Tüm sayısal alanlar (`tx_m`...`scale_ppm`) mevcut mu ve gerçekten
       sayısal mı (bool/None kabul edilmez -- Python'da `bool` `int`
       alt sınıfı olduğundan özellikle kontrol edilir)?
    3. `source` alanı dolu mu ve `_PLACEHOLDER_MARKER` içermiyor mu (şablon
       dosyanın düzenlenmeden kullanılmasını engeller)?
    """
    p = Path(path)
    if not p.is_file():
        raise InsufficientDataError(
            f"Datum dönüşüm parametre dosyası bulunamadı: {p}. "
            "Resmi HGK/TUSAGA-Aktif parametre setini `datum_params/README.md`'deki "
            "şemaya göre bir JSON dosyasına yerleştirip yolunu vermelisiniz -- "
            "bu platform resmi parametreleri kendiliğinden uydurmaz."
        )
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InsufficientDataError(f"Geçersiz JSON ({p}): {exc}") from exc
    if not isinstance(raw, dict):
        raise InsufficientDataError(f"Datum parametre dosyası bir JSON nesnesi olmalı: {p}")

    missing = [f for f in _REQUIRED_NUMERIC_FIELDS if f not in raw]
    if missing:
        raise InsufficientDataError(
            f"Datum parametre dosyasında eksik alan(lar): {', '.join(missing)} ({p})."
        )

    numeric: dict[str, float] = {}
    for field_name in _REQUIRED_NUMERIC_FIELDS:
        value = raw[field_name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InsufficientDataError(
                f"'{field_name}' sayısal olmalı, alınan tür: {type(value).__name__} ({p})."
            )
        numeric[field_name] = float(value)

    source = raw.get("source")
    if not isinstance(source, str) or not source.strip():
        raise InsufficientDataError(
            f"'source' alanı zorunlu ve boş olamaz -- parametrelerin hangi resmi "
            f"kaynaktan geldiği belgelenmelidir ({p})."
        )
    if _PLACEHOLDER_MARKER in source:
        raise InsufficientDataError(
            f"'{p}' hâlâ bir şablon dosyası ('source' alanı {_PLACEHOLDER_MARKER!r} "
            "içeriyor) -- gerçek resmi parametrelerle değiştirilmeden kullanılamaz."
        )

    return DatumTransformParameters(
        tx_m=numeric["tx_m"],
        ty_m=numeric["ty_m"],
        tz_m=numeric["tz_m"],
        rx_arcsec=numeric["rx_arcsec"],
        ry_arcsec=numeric["ry_arcsec"],
        rz_arcsec=numeric["rz_arcsec"],
        scale_ppm=numeric["scale_ppm"],
        source=source.strip(),
    )

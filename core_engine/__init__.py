"""Faz 1 — Core Engine: GIS parser'lar, geometri motoru, koordinat
sistemleri, tile kaynakları, coğrafi referans (CRS/EPSG) katmanı.

Not (Faz 22 doğrulaması sırasında bulundu ve düzeltildi): bu dosya
öncesinde eksikti — `core_engine/` diğer tüm kardeş modüllerin (persistence,
export, app_shell, ...) aksine kendi `__init__.py`'ı olmadan yalnızca bir
PEP 420 örtük ad alanı (implicit namespace) paketiydi. Bu, `python -c
"import core_engine"` gibi doğrudan importlarda sessizce çalışsa da,
`setuptools.find_packages()` (namespace-farkında olmayan klasik biçimiyle)
bu dizini ve TÜM alt modüllerini (`geometry_engine`, `gis_core`, ...)
paketleme sırasında tamamen atlamasına yol açıyordu — yani `pip install`
ile kurulan pakette `core_engine` ve alt modülleri hiç yer almıyordu. Bu
dosyanın eklenmesiyle `core_engine` artık diğerleriyle tutarlı, standart
bir düzenli (regular) pakettir.
"""

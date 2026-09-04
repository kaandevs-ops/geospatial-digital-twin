"""
vegetation.types - Temel veri tipleri
=====================================

Roadmap V4 - Faz E18.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TreeSpecies(str, Enum):
    """Basit tur ayrimi - her tur `tree_generator.TreeGenerator` icin
    farkli oran/renk-ipucu parametreleri secer (gercek botanik tur
    katalogu degil, gorsel cesitlilik icin kaba bir siniflandirma).

    Roadmap V8 Faz 5.2: ilk 4 sinif (GENERIC/CONIFER/DECIDUOUS/SHRUB)
    Turkiye/Akdeniz iklimi icin gorsel olarak cok farkli siluetleri
    (palmiye, servi, zeytin...) ayni kovaya sikistiriyordu - bu, 8
    gorsel olarak ayirt edilebilir alt tipe genisletildi."""

    GENERIC = "generic"
    CONIFER = "conifer"  # ince, sivri, kozalakli (genel igne yaprakli)
    DECIDUOUS = "deciduous"  # genis, yuvarlak tac (genel yaprakli)
    SHRUB = "shrub"  # kisa, calilik
    PALM = "palm"  # palmiye - cizgili ince govde + tepe yelpaze yapraklar
    CYPRESS = "cypress"  # servi - dar, dikey, sutun-siluet
    OLIVE = "olive"  # zeytin - dusuk govde, genis/duzensiz basik tac
    PINE = "pine"  # cam - conifer'dan ayristirilmis, sisman katmanli tac
    OAK = "oak"  # mese - genis, yuvarlak, kalin govdeli
    PLANE = "plane"  # cinar - cok genis yayilan tac, yuksek govde


@dataclass(slots=True)
class VegetationInstance:
    """Sahnede tek bir bitki orgusu ornegi (agac/calili) icin yerlesim
    bilgisi. Mesh'in kendisini tasimaz - `species`/`height`/`canopy_radius`
    + `seed` ile `TreeGenerator.generate()` cagrilarak deterministik olarak
    yeniden uretilebilir (bellek/depolama tasarrufu - RenderScene her
    instance icin ayni mesh'i N kez cogaltmak yerine tek bir base-mesh'i
    referans alabilir, bkz. `performance.InstancingBatch`)."""

    species: TreeSpecies
    x: float
    y: float
    z: float
    height: float
    canopy_radius: float
    rotation_deg: float
    seed: int

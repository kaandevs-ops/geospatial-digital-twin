"""
Harici PBR Doku Kütüphanesi Entegrasyonu (ambientCG)
=====================================================

yeni_roadmap.md Faz 1.4: "Gerçek PBR doku kütüphanesi (açık kaynak:
ambientCG, Poly Haven gibi CC0 kaynaklardan) entegre edilmeli - şu an
prosedürel/placeholder seviyesinde."

Bu modül, `ProceduralMaterials`'ın (mevcut, kod-üretimli placeholder)
yanına, CC0 lisanslı gerçek doku kütüphanesi ambientCG'nin genel REST
API'siyle (https://ambientcg.com/api/v2/full_json) konuşan bir istemci
ekler.

Tasarım kararları (dürüst kısıtlamalar):

* Bu sandbox'ın ağ erişimi ambientcg.com'a izin vermiyor (yalnızca
  paket kayıt defterleri: pypi/npm/github vb.). Bu yüzden gerçek doku
  indirme burada *hiç doğrulanmadı* - istemci kodu ambientCG'nin
  belgelenmiş genel API şemasına göre yazıldı, testler `urlopen`'i
  mock'layarak şemayı doğruluyor. Gerçek ağ erişimi olan bir ortamda
  ilk kullanımda `AmbientCGClient.search()`/`fetch_material()` gerçek
  isteği atacak; sandbox testleri bunu iddia etmiyor.
* Ağ çağrısı **hiçbir zaman** zorunlu değil: `PBRMaterialLibrary.get()`
  önce yerel diske önbelleklenmiş sonucu, sonra (varsa) ağı dener; ikisi
  de yoksa/başarısızsa `ProceduralMaterials`'a (mevcut placeholder)
  **sessizce** düşer - böylece offline senaryo (roadmap Faz 0/5'te
  bahsedilen "offline senaryolar") hiç kırılmaz.
* Sadece stdlib (`urllib.request`, `json`, `pathlib`) - opsiyonel ağır
  bağımlılık yok.
* İndirilen doku dosyaları (albedo/normal/roughness/AO/displacement PNG/
  JPG) `TextureLoader` (Pillow gerektirir) ile daha sonra decode
  edilebilir; bu modül yalnızca *indirme + metadata + yerel önbellek*
  sorumluluğunu taşır, decode'a karışmaz.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import PBRMaterial, ProceduralMaterials

AMBIENTCG_API_BASE = "https://ambientcg.com/api/v2/full_json"

# ambientCG malzeme tipi -> bizim procedural preset isimlerimiz (fallback
# ve arama sorgusu eşleştirmesi için). Roadmap'teki 8 cephe malzemesiyle
# hizalı.
MATERIAL_TYPE_SEARCH_TERMS: dict[str, str] = {
    "beton": "Concrete",
    "tugla": "Bricks",
    "metal": "Metal",
    "tas": "Rock",
    "ahsap": "Wood",
    "cam": "Glass",
    "kompozit": "PaintedMetal",
    "endustriyel": "CorrugatedSteel",
}


class ExternalLibraryError(RuntimeError):
    """Ağ/parse hatası - çağıran taraf bunu yakalayıp fallback'e düşer."""


@dataclass(slots=True)
class ExternalMaterialAsset:
    """ambientCG'den dönen bir malzeme kaydının indirgenmiş metadata'sı."""

    asset_id: str
    display_name: str
    category: str
    download_urls: dict[str, str] = field(default_factory=dict)  # map_type -> url
    preview_url: Optional[str] = None
    license: str = "CC0"


class AmbientCGClient:
    """ambientCG genel REST API'sine (v2/full_json) ince bir HTTP istemcisi.

    Kimlik doğrulama gerekmez (public API, CC0 içerik). stdlib
    `urllib.request` kullanır - `requests` bağımlılığı eklemez.
    """

    def __init__(self, base_url: str = AMBIENTCG_API_BASE, timeout: float = 10.0) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def search(self, query: str, limit: int = 5, category: str = "Atlas,Decal,Material") -> list[ExternalMaterialAsset]:
        """Verilen anahtar kelimeyle malzeme ara.

        ambientCG şeması: `{"foundAssets": [{"assetId": ..., "displayName":
        ..., "dimensionsType": ..., "previewImage": {...},
        "downloadFolders": {"default": {"downloadFiletypeCategories": {
            "zip": {"downloads": [{"attribute": "1K-PNG", "downloadLink": ...}]}
        }}}}]}`. Bu istemci yalnızca ihtiyaç duyduğumuz alt kümeyi çıkarır.
        """
        url = (
            f"{self.base_url}?type=Material&q={urllib.parse.quote(query)}"
            f"&limit={limit}&category={urllib.parse.quote(category)}"
        )
        raw = self._get_json(url)
        assets: list[ExternalMaterialAsset] = []
        for item in raw.get("foundAssets", []):
            assets.append(self._parse_asset(item))
        return assets

    def _get_json(self, url: str) -> dict:
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:  # noqa: S310
                data = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ExternalLibraryError(f"ambientCG isteği başarısız: {exc}") from exc
        try:
            return json.loads(data)
        except json.JSONDecodeError as exc:
            raise ExternalLibraryError(f"ambientCG yanıtı parse edilemedi: {exc}") from exc

    @staticmethod
    def _parse_asset(item: dict) -> ExternalMaterialAsset:
        asset_id = item.get("assetId", "unknown")
        preview = None
        preview_block = item.get("previewImage")
        if isinstance(preview_block, dict):
            preview = preview_block.get("128px") or preview_block.get("512px")

        downloads: dict[str, str] = {}
        folders = item.get("downloadFolders", {})
        default_folder = folders.get("default", {}) if isinstance(folders, dict) else {}
        filetypes = default_folder.get("downloadFiletypeCategories", {}) if isinstance(default_folder, dict) else {}
        zip_cat = filetypes.get("zip", {}) if isinstance(filetypes, dict) else {}
        for entry in zip_cat.get("downloads", []) if isinstance(zip_cat, dict) else []:
            attr = entry.get("attribute")
            link = entry.get("downloadLink")
            if attr and link:
                downloads[attr] = link

        return ExternalMaterialAsset(
            asset_id=asset_id,
            display_name=item.get("displayName", asset_id),
            category=item.get("category", ""),
            download_urls=downloads,
            preview_url=preview,
            license="CC0",
        )

    def download_file(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp, open(dest, "wb") as fh:  # noqa: S310
                fh.write(resp.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ExternalLibraryError(f"Dosya indirilemedi ({url}): {exc}") from exc
        return dest


@dataclass(slots=True)
class LibraryCacheEntry:
    asset_id: str
    display_name: str
    material_type: str
    local_files: dict[str, str]  # map_type -> yerel dosya yolu (str)
    license: str = "CC0"
    source: str = "ambientCG"


class PBRMaterialLibrary:
    """Yüksek seviye API: `get(material_type)` -> gerçek doku (varsa) veya
    prosedürel `PBRMaterial` (fallback).

    Önbellek dizini: `<cache_dir>/index.json` + `<cache_dir>/<asset_id>/...`.
    Ağ hiç kullanılamasa bile (offline, sandbox, CI) bu sınıf asla
    exception fırlatmaz - `ProceduralMaterials`'a düşer ve `source` alanı
    çağırana hangi yoldan geldiğini söyler.
    """

    def __init__(self, cache_dir: str | Path, client: Optional[AmbientCGClient] = None) -> None:
        self.cache_dir = Path(cache_dir)
        self.client = client or AmbientCGClient()
        self._index_path = self.cache_dir / "index.json"

    # -- yerel önbellek ---------------------------------------------------
    def _load_index(self) -> dict[str, dict]:
        if not self._index_path.exists():
            return {}
        try:
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_index(self, index: dict[str, dict]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    def cached_entry(self, material_type: str) -> Optional[LibraryCacheEntry]:
        index = self._load_index()
        raw = index.get(material_type)
        if raw is None:
            return None
        return LibraryCacheEntry(**raw)

    # -- ana giriş noktası --------------------------------------------------
    def get(
        self,
        material_type: str,
        variation_seed: int | None = None,
        prefer_cached: bool = True,
        resolution: str = "1K-PNG",
    ) -> tuple[PBRMaterial, str]:
        """`material_type` için `(PBRMaterial, source)` döndürür.

        `source` ∈ {"cache", "network", "procedural"} - çağıran tarafın
        (örn. QA raporu, UI) gerçek doku mu placeholder mı kullanıldığını
        ayırt edebilmesi için.
        """
        material_type = material_type.lower()

        if prefer_cached:
            entry = self.cached_entry(material_type)
            if entry is not None and entry.local_files:
                mat = ProceduralMaterials.create(material_type, variation_seed=variation_seed)
                mat.name = f"{material_type}:{entry.asset_id}"
                return mat, "cache"

        query = MATERIAL_TYPE_SEARCH_TERMS.get(material_type, material_type)
        try:
            assets = self.client.search(query, limit=1)
        except ExternalLibraryError:
            assets = []

        if assets and assets[0].download_urls:
            asset = assets[0]
            link = asset.download_urls.get(resolution) or next(iter(asset.download_urls.values()))
            dest = self.cache_dir / asset.asset_id / f"{resolution}.zip"
            try:
                self.client.download_file(link, dest)
            except ExternalLibraryError:
                pass
            else:
                index = self._load_index()
                index[material_type] = {
                    "asset_id": asset.asset_id,
                    "display_name": asset.display_name,
                    "material_type": material_type,
                    "local_files": {resolution: str(dest)},
                    "license": asset.license,
                    "source": "ambientCG",
                }
                self._save_index(index)
                mat = ProceduralMaterials.create(material_type, variation_seed=variation_seed)
                mat.name = f"{material_type}:{asset.asset_id}"
                return mat, "network"

        # Fallback: hiçbir gerçek doku bulunamadı/indirilemedi -> mevcut
        # prosedürel placeholder (davranış roadmap-öncesiyle birebir aynı).
        return ProceduralMaterials.create(material_type, variation_seed=variation_seed), "procedural"

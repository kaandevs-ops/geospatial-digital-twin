"""yeni_roadmap.md Faz 1.4 - Gerçek PBR doku kütüphanesi entegrasyonu.

Sandbox ağ erişimi ambientcg.com'a izin vermediği için testler
`urllib.request.urlopen`'i mock'lar; asıl şema-parse + cache + fallback
mantığını doğrular. Gerçek ağ olmadan da (procedural fallback) sınıfın
hiçbir zaman exception fırlatmadığı ayrıca test edilir.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from harita.material_engine import PBRMaterial
from harita.material_engine.external_library import (
    AmbientCGClient,
    ExternalLibraryError,
    PBRMaterialLibrary,
)

FAKE_SEARCH_RESPONSE = {
    "foundAssets": [
        {
            "assetId": "Bricks090",
            "displayName": "Bricks 090",
            "category": "Material",
            "previewImage": {"128px": "https://ambientcg.com/prev.png"},
            "downloadFolders": {
                "default": {
                    "downloadFiletypeCategories": {
                        "zip": {
                            "downloads": [
                                {"attribute": "1K-PNG", "downloadLink": "https://ambientcg.com/Bricks090_1K.zip"},
                            ]
                        }
                    }
                }
            },
        }
    ]
}


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_client_search_parses_schema():
    client = AmbientCGClient()
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps(FAKE_SEARCH_RESPONSE).encode())):
        assets = client.search("Bricks")
    assert len(assets) == 1
    asset = assets[0]
    assert asset.asset_id == "Bricks090"
    assert asset.download_urls["1K-PNG"] == "https://ambientcg.com/Bricks090_1K.zip"
    assert asset.license == "CC0"


def test_client_search_network_error_raises_external_library_error():
    import urllib.error

    client = AmbientCGClient()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
        with pytest.raises(ExternalLibraryError):
            client.search("Bricks")


def test_library_falls_back_to_procedural_when_network_unavailable(tmp_path: Path):
    import urllib.error

    lib = PBRMaterialLibrary(cache_dir=tmp_path)
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        mat, source = lib.get("beton")
    assert source == "procedural"
    assert isinstance(mat, PBRMaterial)
    assert mat.name == "beton"


def test_library_downloads_and_caches_on_success(tmp_path: Path):
    lib = PBRMaterialLibrary(cache_dir=tmp_path)

    search_resp = _FakeResponse(json.dumps(FAKE_SEARCH_RESPONSE).encode())
    download_resp = _FakeResponse(b"FAKE-ZIP-BYTES")

    with patch("urllib.request.urlopen", side_effect=[search_resp, download_resp]):
        mat, source = lib.get("tugla")

    assert source == "network"
    assert mat.name == "tugla:Bricks090"
    cached_file = tmp_path / "Bricks090" / "1K-PNG.zip"
    assert cached_file.exists()
    assert cached_file.read_bytes() == b"FAKE-ZIP-BYTES"

    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index["tugla"]["asset_id"] == "Bricks090"
    assert index["tugla"]["license"] == "CC0"


def test_library_uses_cache_on_second_call_without_network(tmp_path: Path):
    lib = PBRMaterialLibrary(cache_dir=tmp_path)
    search_resp = _FakeResponse(json.dumps(FAKE_SEARCH_RESPONSE).encode())
    download_resp = _FakeResponse(b"FAKE-ZIP-BYTES")
    with patch("urllib.request.urlopen", side_effect=[search_resp, download_resp]):
        lib.get("metal")

    # İkinci çağrıda ağ hiç kullanılmamalı (cache'ten dönmeli) - urlopen
    # çağrılırsa test patlar.
    with patch("urllib.request.urlopen", side_effect=AssertionError("ağ çağrılmamalıydı")):
        mat, source = lib.get("metal")
    assert source == "cache"
    assert mat.name.startswith("metal:")


def test_library_asset_search_no_downloads_falls_back(tmp_path: Path):
    empty_asset_response = {
        "foundAssets": [
            {"assetId": "Weird1", "displayName": "Weird", "category": "Material", "downloadFolders": {}}
        ]
    }
    lib = PBRMaterialLibrary(cache_dir=tmp_path)
    with patch("urllib.request.urlopen", return_value=_FakeResponse(json.dumps(empty_asset_response).encode())):
        mat, source = lib.get("ahsap")
    assert source == "procedural"
    assert mat.name == "ahsap"

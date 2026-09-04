#!/usr/bin/env python3
"""Manifest üretim betiği (stdlib-only).

Kullanıcı talebi (madde 3): "somut manifest scripti ekle projeye".

Var olan bir `.harita` proje dosyasını açar, istenen export formatlarını
(`export_scene` üzerinden) gerçekten diske yazar, ardından hepsini tek bir
`manifest.json`'da kataloglar — her dosya için biçim/yol/boyut/SHA-256
checksum, her bina için kimlik/tip/kat sayısı/yükseklik/bbox.

Kullanım::

    python scripts/generate_manifest.py PROJE.harita --format obj --format gltf
    python scripts/generate_manifest.py PROJE.harita --format obj --out-dir /tmp/export --crs EPSG:4326
    python scripts/generate_manifest.py PROJE.harita --verify manifest.json   # yalnızca checksum doğrulama

Çıkış kodu: başarıda 0, herhangi bir hata durumunda 1 (stderr'e mesaj).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from harita.app_shell.session import AppSession, AppSessionError  # noqa: E402
from harita.export.manifest import ManifestValidationError, verify_manifest_checksums  # noqa: E402

_VALID_FORMATS = {"obj", "stl", "ply", "gltf", "dxf", "3dtiles", "ifc"}


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bir harita projesini export edip somut bir manifest.json üretir.",
    )
    parser.add_argument(
        "project_path",
        type=str,
        nargs="?",
        default=None,
        help="Açılacak .harita proje dosyasının yolu (--verify modunda gerekli değil)",
    )
    parser.add_argument(
        "--format",
        "-f",
        dest="formats",
        action="append",
        default=[],
        choices=sorted(_VALID_FORMATS),
        help="Export edilecek format (birden fazla kez verilebilir). Varsayılan: obj, gltf.",
    )
    parser.add_argument(
        "--out-dir", type=str, default=None, help="Export hedef dizini (proje köküne göreli)"
    )
    parser.add_argument(
        "--crs",
        type=str,
        default=None,
        help="Manifest'e yazılacak koordinat referans sistemi etiketi (örn. EPSG:4326)",
    )
    parser.add_argument(
        "--no-checksums",
        action="store_true",
        help="SHA-256 checksum hesaplamayı atla (büyük 3D Tiles dizinlerinde hızlandırmak için)",
    )
    parser.add_argument(
        "--verify",
        type=str,
        default=None,
        metavar="MANIFEST.json",
        help="Export yapmadan, var olan bir manifest.json'un checksum'larını diskle karşılaştırıp doğrula",
    )
    return parser


def _cmd_verify(manifest_path: str) -> int:
    try:
        mismatches = verify_manifest_checksums(manifest_path)
    except (ManifestValidationError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"HATA: manifest doğrulanamadı: {exc}", file=sys.stderr)
        return 1
    if mismatches:
        print(f"UYUMSUZ ({len(mismatches)} dosya):")
        for m in mismatches:
            print(f"  - {m}")
        return 1
    print("Tüm dosya checksum'ları tutarlı.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.verify:
        return _cmd_verify(args.verify)

    formats = args.formats or ["obj", "gltf"]
    if not args.project_path:
        print("HATA: project_path gerekli (--verify modu hariç).", file=sys.stderr)
        return 1
    project_path = Path(args.project_path)
    if not project_path.exists():
        print(f"HATA: proje dosyası bulunamadı: {project_path}", file=sys.stderr)
        return 1

    registry_path = project_path.parent / ".harita_registry.json"
    session = AppSession(registry_path)
    try:
        try:
            info = session.open_project(path=project_path)
        except AppSessionError as exc:
            print(f"HATA: proje açılamadı: {exc}", file=sys.stderr)
            return 1
        project_id = info["project_id"]

        try:
            result = session.export_manifest(
                project_id,
                formats,
                out_dir=args.out_dir,
                crs=args.crs,
                compute_checksums=not args.no_checksums,
            )
        except AppSessionError as exc:
            print(f"HATA: manifest üretilemedi: {exc}", file=sys.stderr)
            return 1

        print(f"Manifest yazıldı: {result['manifest_path']}")
        print(f"  bina sayısı : {result['manifest']['building_count']}")
        print(f"  dosya sayısı: {len(result['manifest']['files'])}")
        for f in result["manifest"]["files"]:
            checksum = f.get("sha256", "")[:12] + "…" if f.get("sha256") else "(checksum yok)"
            print(f"  - [{f['format']:>7}] {f['path']}  ({f['bytes_written']} bayt, {checksum})")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())

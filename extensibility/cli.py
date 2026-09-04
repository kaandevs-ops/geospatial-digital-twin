"""
CLI Arayüzü
============

Roadmap Phase 14 - "CLI arayüzü".

`argparse` tabanlı, alt-komutlu bir komut satırı arayüzü. Gerçek `sys.argv`
okumaz - `CLI.run(argv)` ile test edilebilir (Phase 14'ün diğer modülleri
gibi soket/stdin'e bağımlı değildir). Alt komutlar `ProjectFile`
(`project_format.py`), `PluginManager` ve `RestRouter` gibi diğer Phase 14
bileşenlerini saran ince bir kabuk (thin wrapper) görevi görür.

Komutlar:
    * ``project new <path> --name <isim>``      - yeni `.harita` projesi oluşturur
    * ``project info <path>``                   - proje meta verisini yazdırır
    * ``project migrate <path>``                - en güncel şema sürümüne taşır
    * ``plugin list --dir <klasör>``             - klasördeki eklentileri keşfeder
    * ``plugin enable/disable <isim>``           - eklenti durumunu değiştirir
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TextIO

from .plugin_system import PluginManager
from .project_format import ProjectFile, migrate_project_file


@dataclass
class CLIResult:
    """`CLI.run()` çıktısını temsil eder (test edilebilirlik için exit-code fırlatmak yerine)."""

    exit_code: int
    output: str


class CLI:
    """
    Harita Modelleme Platformu komut satırı arayüzü.

    ``cli = CLI(); result = cli.run(["project", "info", "scene.harita"])``
    """

    def __init__(self, plugin_manager: PluginManager | None = None) -> None:
        self.plugin_manager = plugin_manager or PluginManager()
        self._parser = self._build_parser()

    # ------------------------------------------------------------------ #
    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="harita", description="Harita Modelleme Platformu CLI"
        )
        sub = parser.add_subparsers(dest="command", required=True)

        project = sub.add_parser("project", help="Proje dosyası (.harita) işlemleri")
        project_sub = project.add_subparsers(dest="project_command", required=True)

        p_new = project_sub.add_parser("new", help="Yeni proje oluştur")
        p_new.add_argument("path")
        p_new.add_argument("--name", default="Adsız Proje")

        p_info = project_sub.add_parser("info", help="Proje meta verisini göster")
        p_info.add_argument("path")

        p_migrate = project_sub.add_parser("migrate", help="Projeyi güncel şemaya taşı")
        p_migrate.add_argument("path")

        plugin = sub.add_parser("plugin", help="Eklenti işlemleri")
        plugin_sub = plugin.add_subparsers(dest="plugin_command", required=True)

        pl_list = plugin_sub.add_parser("list", help="Eklentileri listele")
        pl_list.add_argument("--dir", default=None)

        pl_enable = plugin_sub.add_parser("enable", help="Eklentiyi etkinleştir")
        pl_enable.add_argument("name")

        pl_disable = plugin_sub.add_parser("disable", help="Eklentiyi devre dışı bırak")
        pl_disable.add_argument("name")

        return parser

    # ------------------------------------------------------------------ #
    def run(self, argv: Sequence[str], stream: TextIO | None = None) -> CLIResult:
        stream = stream if stream is not None else sys.stdout
        try:
            args = self._parser.parse_args(list(argv))
        except SystemExit as exc:
            # argparse hatada sys.exit çağırır - test edilebilirlik için yakalanır.
            return CLIResult(exit_code=exc.code or 2, output="")

        try:
            output = self._dispatch(args)
            return CLIResult(exit_code=0, output=output)
        except Exception as exc:  # noqa: BLE001 - CLI hata mesajını kullanıcıya iletir
            return CLIResult(exit_code=1, output=f"hata: {exc}")

    # ------------------------------------------------------------------ #
    def _dispatch(self, args: argparse.Namespace) -> str:
        if args.command == "project":
            return self._dispatch_project(args)
        if args.command == "plugin":
            return self._dispatch_plugin(args)
        raise ValueError(f"bilinmeyen komut: {args.command}")

    def _dispatch_project(self, args: argparse.Namespace) -> str:
        if args.project_command == "new":
            pf = ProjectFile.create(name=args.name)
            pf.save(args.path)
            return f"proje oluşturuldu: {args.path} (schema v{pf.schema_version})"
        if args.project_command == "info":
            pf = ProjectFile.load(args.path)
            return (
                f"isim: {pf.name}\n"
                f"schema: v{pf.schema_version}\n"
                f"oluşturulma: {pf.created_at}\n"
                f"nesne sayısı: {len(pf.twins)}"
            )
        if args.project_command == "migrate":
            pf = ProjectFile.load(args.path)
            migrated, from_version, to_version = migrate_project_file(pf)
            migrated.save(args.path)
            if from_version == to_version:
                return f"zaten güncel (v{to_version})"
            return f"v{from_version} -> v{to_version} taşındı: {args.path}"
        raise ValueError(f"bilinmeyen alt komut: {args.project_command}")

    def _dispatch_plugin(self, args: argparse.Namespace) -> str:
        if args.plugin_command == "list":
            if args.dir:
                self.plugin_manager.discover_directory(args.dir)
            names = self.plugin_manager.list_plugins()
            return "\n".join(names) if names else "(eklenti yok)"
        if args.plugin_command == "enable":
            self.plugin_manager.enable(args.name)
            return f"etkinleştirildi: {args.name}"
        if args.plugin_command == "disable":
            self.plugin_manager.disable(args.name)
            return f"devre dışı bırakıldı: {args.name}"
        raise ValueError(f"bilinmeyen alt komut: {args.plugin_command}")

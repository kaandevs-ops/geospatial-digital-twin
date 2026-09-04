"""
Export — Reports
=================

Roadmap Phase 11 - "Reports" export bölümü: JSON, CSV, XML, Markdown, PDF.

Bu modül, platformun herhangi bir katmanından (Digital Twin, Analysis
Engine, AI Reconstruction ...) gelen sözlük/liste tabanlı rapor verisini
disk üzerinde standart formatlara yazar. PDF raporu için bkz.
`vector_2d.PDFExporter`. Bağımlılık: yalnızca stdlib.
"""

from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from xml.sax.saxutils import escape as _xml_escape

from .geometry_3d import ExportResult


class JSONReportExporter:
    """Herhangi bir JSON-serileştirilebilir yapıyı (dict/list/dataclass'lar
    zaten dict'e çevrilmiş olmalı) biçimlendirilmiş JSON olarak yazar."""

    @staticmethod
    def export(data: Any, path: str, indent: int = 2) -> ExportResult:
        content = json.dumps(data, indent=indent, ensure_ascii=False, default=str)
        Path(path).write_text(content, encoding="utf-8")
        return ExportResult(path, "json", len(content.encode("utf-8")), 0, 0)


class CSVReportExporter:
    """Bir kayıt listesini (list[dict]) CSV'ye yazar. Sütun başlıkları,
    tüm kayıtlardaki anahtarların birleşiminden (ilk görülme sırasına göre)
    otomatik çıkarılır."""

    @staticmethod
    def export(rows: Sequence[Mapping[str, Any]], path: str,
               fieldnames: Sequence[str] | None = None) -> ExportResult:
        if fieldnames is None:
            fieldnames = []
            seen = set()
            for row in rows:
                for key in row.keys():
                    if key not in seen:
                        seen.add(key)
                        fieldnames.append(key)

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        content = buf.getvalue()
        Path(path).write_text(content, encoding="utf-8", newline="")
        return ExportResult(path, "csv", len(content.encode("utf-8")), 0, 0)


class XMLReportExporter:
    """Bir dict/list yapısını basit, öngörülebilir bir XML ağacına serileştirir.
    Şema: dict anahtarları eleman adı olur; list elemanları `<item>` altında
    tekrarlanır; ilkel değerler metin içeriği olarak yazılır."""

    @staticmethod
    def export(data: Any, path: str, root_tag: str = "report") -> ExportResult:
        body = XMLReportExporter._to_xml(data, root_tag)
        content = f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'
        Path(path).write_text(content, encoding="utf-8")
        return ExportResult(path, "xml", len(content.encode("utf-8")), 0, 0)

    @staticmethod
    def _to_xml(data: Any, tag: str) -> str:
        safe_tag = tag if tag and tag[0].isalpha() else f"n_{tag}"
        if isinstance(data, Mapping):
            inner = "".join(XMLReportExporter._to_xml(v, str(k)) for k, v in data.items())
            return f"<{safe_tag}>{inner}</{safe_tag}>"
        if isinstance(data, (list, tuple, set)):
            inner = "".join(XMLReportExporter._to_xml(v, "item") for v in data)
            return f"<{safe_tag}>{inner}</{safe_tag}>"
        if data is None:
            return f"<{safe_tag} />"
        return f"<{safe_tag}>{_xml_escape(str(data))}</{safe_tag}>"


class MarkdownReportExporter:
    """Başlık + anahtar-değer özet + (opsiyonel) tablo bölümlerinden oluşan
    bir Markdown raporu üretir. Roadmap'in "Tek tıkla tam PDF rapor"
    ihtiyacına giden en pratik ilk adım: Markdown -> (harici araçla) PDF."""

    @staticmethod
    def export(title: str, sections: Sequence["ReportSection"], path: str,
               generated_at: float | None = None) -> ExportResult:
        ts = generated_at if generated_at is not None else time.time()
        lines = [f"# {title}", "", f"_Oluşturulma zamanı: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))}_", ""]
        for section in sections:
            lines.append(f"## {section.heading}")
            lines.append("")
            if section.summary:
                for key, value in section.summary.items():
                    lines.append(f"- **{key}**: {value}")
                lines.append("")
            if section.table_rows:
                headers = section.table_headers or list(section.table_rows[0].keys())
                lines.append("| " + " | ".join(headers) + " |")
                lines.append("| " + " | ".join("---" for _ in headers) + " |")
                for row in section.table_rows:
                    lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")
                lines.append("")
            if section.notes:
                lines.append(section.notes)
                lines.append("")
        content = "\n".join(lines).rstrip() + "\n"
        Path(path).write_text(content, encoding="utf-8")
        return ExportResult(path, "markdown", len(content.encode("utf-8")), 0, 0)


class ReportSection:
    """Markdown/PDF raporlarının paylaştığı ortak bölüm veri modeli."""

    __slots__ = ("heading", "summary", "table_headers", "table_rows", "notes")

    def __init__(self, heading: str, summary: Mapping[str, Any] | None = None,
                 table_headers: Sequence[str] | None = None,
                 table_rows: Sequence[Mapping[str, Any]] | None = None,
                 notes: str = ""):
        self.heading = heading
        self.summary = dict(summary) if summary else {}
        self.table_headers = list(table_headers) if table_headers else None
        self.table_rows = list(table_rows) if table_rows else []
        self.notes = notes

    def to_plain_lines(self) -> list[str]:
        """`PDFExporter.export_text_report` gibi düz-metin tüketicileri için
        bu bölümü satır listesine çevirir."""
        lines = [f"[{self.heading}]"]
        for key, value in self.summary.items():
            lines.append(f"  {key}: {value}")
        if self.table_rows:
            headers = self.table_headers or list(self.table_rows[0].keys())
            lines.append("  " + " | ".join(headers))
            for row in self.table_rows:
                lines.append("  " + " | ".join(str(row.get(h, "")) for h in headers))
        if self.notes:
            lines.append(f"  {self.notes}")
        return lines


class ReportBuilder:
    """Tüm rapor formatlarını (JSON/CSV/XML/Markdown/PDF) tek bir
    `ReportSection` listesinden üretmek için kolaylık sınıfı — Digital Twin
    veya Analysis Engine çıktısını tek yerden çok formata dağıtır."""

    def __init__(self, title: str, sections: Sequence[ReportSection]):
        self.title = title
        self.sections = list(sections)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "generated_at": time.time(),
            "sections": [
                {
                    "heading": s.heading,
                    "summary": s.summary,
                    "table": s.table_rows,
                    "notes": s.notes,
                }
                for s in self.sections
            ],
        }

    def export_json(self, path: str) -> ExportResult:
        return JSONReportExporter.export(self.to_dict(), path)

    def export_xml(self, path: str) -> ExportResult:
        return XMLReportExporter.export(self.to_dict(), path, root_tag="report")

    def export_markdown(self, path: str) -> ExportResult:
        return MarkdownReportExporter.export(self.title, self.sections, path)

    def export_csv(self, path: str, section_index: int = 0) -> ExportResult:
        if not self.sections or not self.sections[section_index].table_rows:
            raise ValueError("Seçilen bölümde CSV'ye çevrilecek tablo verisi yok.")
        return CSVReportExporter.export(self.sections[section_index].table_rows, path)

    def export_pdf(self, path: str):
        from .vector_2d import PDFExporter
        lines: list[str] = []
        for section in self.sections:
            lines.extend(section.to_plain_lines())
            lines.append("")
        return PDFExporter.export_text_report(self.title, lines, path)

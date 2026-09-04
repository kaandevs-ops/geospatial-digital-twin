"""
Export — Vector 2D
===================

Roadmap Phase 11 - "2D" export bölümü: PNG, JPEG, SVG, PDF, DXF, DWG.

DXF/DWG için bkz. `geometry_3d.py` (3D mesh temsili). Bu modül **2D**
kat planı / blueprint / kesit görünümü gibi çıktılar için SVG (tamamen
bağımlılıksız, XML) exporter'ı sağlar — roadmap'teki "2D Kat Planları:
PDF export edilebilir blueprint'ler" ve "Section View" ihtiyacına karşılık
gelir. PNG/JPEG rasterizasyonu (Pillow) ve PDF (reportlab veya ana projede
zaten var olan bir PDF motoru) opsiyonel bağımlılık gerektirir; mevcutsa
kullanılır, değilse açık bir `UnsupportedFormatError` fırlatılır.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .geometry_3d import ExportResult, UnsupportedFormatError
from ..mesh_engine import Mesh3D


@dataclass(slots=True)
class SVGStyle:
    stroke: str = "#1a1a1a"
    stroke_width: float = 1.0
    fill: str = "none"
    font_size: float = 10.0
    font_family: str = "monospace"


@dataclass(slots=True)
class SVGCanvas:
    """Basit, bağımlılıksız bir SVG oluşturucu (kat planı / blueprint / kesit
    çizimleri için). Koordinat sistemi: SVG'de Y aşağı doğru büyür; bu sınıf
    girdi Y eksenini ters çevirmez, çağıran taraf (rapor katmanı) gerekli
    dönüşümü yapar.
    """

    width: float
    height: float
    background: Optional[str] = "#ffffff"
    _elements: list[str] = field(default_factory=list)

    def line(self, x1: float, y1: float, x2: float, y2: float,
             style: SVGStyle = SVGStyle()) -> None:
        self._elements.append(
            f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" '
            f'stroke="{style.stroke}" stroke-width="{style.stroke_width}" />'
        )

    def polygon(self, points: list[tuple[float, float]],
                style: SVGStyle = SVGStyle()) -> None:
        pts = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
        self._elements.append(
            f'<polygon points="{pts}" fill="{style.fill}" '
            f'stroke="{style.stroke}" stroke-width="{style.stroke_width}" />'
        )

    def polyline(self, points: list[tuple[float, float]],
                 style: SVGStyle = SVGStyle()) -> None:
        pts = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
        self._elements.append(
            f'<polyline points="{pts}" fill="none" '
            f'stroke="{style.stroke}" stroke-width="{style.stroke_width}" />'
        )

    def circle(self, cx: float, cy: float, r: float,
               style: SVGStyle = SVGStyle()) -> None:
        self._elements.append(
            f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r:.3f}" fill="{style.fill}" '
            f'stroke="{style.stroke}" stroke-width="{style.stroke_width}" />'
        )

    def text(self, x: float, y: float, content: str,
              style: SVGStyle = SVGStyle()) -> None:
        safe = (content.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))
        self._elements.append(
            f'<text x="{x:.3f}" y="{y:.3f}" font-family="{style.font_family}" '
            f'font-size="{style.font_size}" fill="{style.stroke}">{safe}</text>'
        )

    def to_string(self) -> str:
        bg = (f'<rect x="0" y="0" width="{self.width}" height="{self.height}" '
              f'fill="{self.background}" />') if self.background else ""
        body = "\n  ".join(self._elements)
        return (
            f'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{self.width}" height="{self.height}" '
            f'viewBox="0 0 {self.width} {self.height}">\n'
            f'  {bg}\n  {body}\n</svg>\n'
        )

    def save(self, path: str) -> ExportResult:
        content = self.to_string()
        Path(path).write_text(content, encoding="utf-8")
        return ExportResult(path, "svg", len(content.encode("utf-8")), 0, 0)


class FloorPlanSVGExporter:
    """Bir kat/oda listesinden (Phase 3 `Room`/`Floor` benzeri nesnelerden,
    ördekbilim ile: her elemanda `.polygon` -> `list[(x, y)]` ve `.name`
    beklenir) 2D kat planı SVG'si üretir. Roadmap: "2D Kat Planları: PDF
    export edilebilir blueprint'ler" — SVG, PDF'e giden en pratik ara
    formattır (herhangi bir tarayıcı/vektör aracıyla PDF'e çevrilebilir).
    """

    @staticmethod
    def export(rooms: list, path: str, margin: float = 20.0,
                scale: float = 1.0) -> ExportResult:
        all_points: list[tuple[float, float]] = []
        for room in rooms:
            all_points.extend(getattr(room, "polygon", []))
        if not all_points:
            all_points = [(0.0, 0.0), (1.0, 1.0)]

        xs = [p[0] for p in all_points]
        ys = [p[1] for p in all_points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = (max_x - min_x) * scale + margin * 2
        height = (max_y - min_y) * scale + margin * 2

        canvas = SVGCanvas(width=max(width, 1.0), height=max(height, 1.0))

        def _to_svg(pt: tuple[float, float]) -> tuple[float, float]:
            x, y = pt
            return ((x - min_x) * scale + margin, (y - min_y) * scale + margin)

        style = SVGStyle(stroke="#222831", stroke_width=1.5, fill="#e9edf2")
        text_style = SVGStyle(stroke="#111", font_size=11.0)

        for room in rooms:
            poly = [_to_svg(p) for p in getattr(room, "polygon", [])]
            if len(poly) >= 3:
                canvas.polygon(poly, style)
                cx = sum(p[0] for p in poly) / len(poly)
                cy = sum(p[1] for p in poly) / len(poly)
                canvas.text(cx - 10, cy, str(getattr(room, "name", "")), text_style)

        return canvas.save(path)


class PNGExporter:
    """Rasterizasyon (SVG/Mesh -> PNG) yalnızca Pillow yüklüyse mümkündür.
    Bu, procedural/vektör verinin piksel tabanlı yeniden örneklenmesi
    olduğundan (anti-aliasing, rasterization) stdlib ile makul kalitede
    yapılamaz. Pillow mevcutsa basit bir üstten-görünüm (top-down) footprint
    rasterizasyonu sağlanır; değilse `UnsupportedFormatError` fırlatılır.
    """

    @staticmethod
    def export_footprint(mesh: Mesh3D, path: str, width: int = 1024,
                          height: int = 1024) -> ExportResult:
        try:
            from PIL import Image, ImageDraw
        except ImportError as exc:
            raise UnsupportedFormatError(
                "PNG rasterizasyonu için Pillow gerekir: pip install pillow"
            ) from exc

        if not mesh.vertices:
            raise ValueError("Boş mesh rasterize edilemez.")

        xs = [v.x for v in mesh.vertices]
        ys = [v.y for v in mesh.vertices]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max(max_x - min_x, 1e-6)
        span_y = max(max_y - min_y, 1e-6)
        margin = 0.05

        img = Image.new("RGB", (width, height), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)

        def _to_px(x: float, y: float) -> tuple[float, float]:
            nx = (x - min_x) / span_x
            ny = (y - min_y) / span_y
            px = margin * width + nx * width * (1 - 2 * margin)
            py = height - (margin * height + ny * height * (1 - 2 * margin))
            return px, py

        for (i, j, k) in mesh.triangles:
            a, b, c = mesh.vertices[i], mesh.vertices[j], mesh.vertices[k]
            poly = [_to_px(a.x, a.y), _to_px(b.x, b.y), _to_px(c.x, c.y)]
            draw.polygon(poly, outline=(30, 30, 30), fill=(220, 225, 232))

        img.save(path, format="PNG")
        return ExportResult(path, "png", Path(path).stat().st_size,
                             mesh.vertex_count(), mesh.triangle_count())


class PDFExporter:
    """Tam PDF raporu (roadmap: "Raporlama: Tek tıkla tam PDF rapor").
    `reportlab` mevcutsa zengin biçimli PDF üretir; değilse stdlib-only bir
    minimal PDF yazıcısı (tek sayfa, metin satırları) devreye girer — bu,
    harici bağımlılık olmadan da temel bir rapor dosyası garanti eder.
    """

    @staticmethod
    def export_text_report(title: str, lines: list[str], path: str) -> ExportResult:
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas as rl_canvas

            c = rl_canvas.Canvas(path, pagesize=A4)
            width, height = A4
            y = height - 60
            c.setFont("Helvetica-Bold", 16)
            c.drawString(40, y, title)
            y -= 30
            c.setFont("Helvetica", 10)
            for line in lines:
                if y < 40:
                    c.showPage()
                    c.setFont("Helvetica", 10)
                    y = height - 60
                c.drawString(40, y, line)
                y -= 14
            c.save()
            size = Path(path).stat().st_size
            return ExportResult(path, "pdf", size, 0, 0)
        except ImportError:
            return PDFExporter._minimal_pdf(title, lines, path)

    @staticmethod
    def _minimal_pdf(title: str, lines: list[str], path: str) -> ExportResult:
        """Bağımlılıksız minimal PDF yazıcı: tek sayfa, sabit genişlikli font,
        temel bir PDF nesne grafiği (xref tablosu dahil) elle üretilir."""
        all_lines = [title, ""] + lines
        content_lines = []
        y = 780
        for line in all_lines:
            escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            content_lines.append(f"BT /F1 11 Tf 40 {y} Td ({escaped}) Tj ET")
            y -= 14
            if y < 40:
                break  # minimal yazıcı çok-sayfalı akışı desteklemez
        stream = "\n".join(content_lines)
        stream_bytes = stream.encode("latin-1", errors="replace")

        objects = [
            "<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
            "/MediaBox [0 0 595 842] /Contents 5 0 R >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
            None,  # stream nesnesi ayrı işlenir
        ]

        buf = bytearray()
        buf += b"%PDF-1.4\n"
        offsets = [0]
        for idx, obj in enumerate(objects, start=1):
            offsets.append(len(buf))
            if obj is None:
                buf += f"{idx} 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("latin-1")
                buf += stream_bytes
                buf += b"\nendstream\nendobj\n"
            else:
                buf += f"{idx} 0 obj\n{obj}\nendobj\n".encode("latin-1")

        xref_offset = len(buf)
        buf += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
        buf += b"0000000000 65535 f \n"
        for off in offsets[1:]:
            buf += f"{off:010d} 00000 n \n".encode("latin-1")
        buf += (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF"
        ).encode("latin-1")

        Path(path).write_bytes(bytes(buf))
        return ExportResult(path, "pdf-minimal", len(buf), 0, 0)

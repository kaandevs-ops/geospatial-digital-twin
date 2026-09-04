"""FAZ S1.5 (Roadmap V7 - bilinçli kapsam-dışı madde 1'in kapanışı) —
Tarayıcıdan sunucuya gerçek `multipart/form-data` dosya yükleme.

Önceki durum (yeni_roadmap.md Faz 6.4'te dürüstçe işaretlenmişti): proje
JSON-tabanlı REST mimarisi kullanıyordu, hiçbir endpoint ikili dosya kabul
etmiyordu; Feature Survey fotoğrafları "sunucu diskinde zaten bir klasörde
olduğu" varsayımıyla çalışıyordu.

Bu modül, harici bağımlılık olmadan (stdlib-only, projenin genel ilkesiyle
tutarlı) RFC 7578 `multipart/form-data` gövdesini ayrıştırır. `cgi` modülü
Python 3.13'te kaldırıldığı için (PEP 594) kasıtlı olarak kullanılmıyor —
bu, kendi implementasyonumuzun ayrıca bir gerekçesi.

Kapsam: tek seviyeli `multipart/form-data` (iç içe `multipart/mixed` YOK —
tarayıcıların `<input type="file" multiple>` ile ürettiği gövdeler bu
kapsamda). Her `Content-Disposition: form-data` parçası bir alan adı
(`name`) ve opsiyonel bir dosya adı (`filename`) taşır.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class MultipartParseError(ValueError):
    """Gövde RFC 7578'e uymuyorsa (sınır bulunamadı, eksik başlık, vb.)."""


@dataclass(slots=True)
class MultipartFile:
    field_name: str
    filename: str
    content_type: str
    data: bytes


@dataclass(slots=True)
class MultipartForm:
    fields: dict[str, str] = field(default_factory=dict)
    files: list[MultipartFile] = field(default_factory=list)


def parse_content_type(header_value: str) -> tuple[str, dict[str, str]]:
    """`Content-Type: multipart/form-data; boundary=----X` başlığını
    ana tip + parametre sözlüğüne ayırır."""
    parts = header_value.split(";")
    main = parts[0].strip().lower()
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        value = value.strip().strip('"')
        params[key.strip().lower()] = value
    return main, params


def _split_header_block(block: bytes) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in block.split(b"\r\n"):
        if not line:
            continue
        if b":" not in line:
            continue
        key, _, value = line.partition(b":")
        headers[key.decode("ascii", "ignore").strip().lower()] = value.decode(
            "utf-8", "replace"
        ).strip()
    return headers


def _parse_content_disposition(value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    parts = value.split(";")
    out["_type"] = parts[0].strip()
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, _, val = part.partition("=")
        out[key.strip().lower()] = val.strip().strip('"')
    return out


def parse_multipart(body: bytes, content_type_header: str) -> MultipartForm:
    """`multipart/form-data` gövdesini ayrıştırır.

    Standart sınırlayıcı tabanlı algoritma (RFC 7578 §4 / RFC 2046 §5.1):
    gövde `--<boundary>` ile ayrılmış parçalara bölünür, her parçanın
    başında `\\r\\n\\r\\n` ile ayrılan bir başlık bloğu, ardından ham içerik
    gelir; son parça `--<boundary>--` ile biter.
    """
    main_type, params = parse_content_type(content_type_header)
    if main_type != "multipart/form-data":
        raise MultipartParseError(f"Content-Type multipart/form-data değil: {main_type!r}")
    boundary = params.get("boundary")
    if not boundary:
        raise MultipartParseError("multipart/form-data gövdesinde 'boundary' parametresi eksik.")

    delimiter = b"--" + boundary.encode("utf-8")
    # Gövdeyi delimiter'lara göre böl. İlk eleman genelde boş/prolog'dur,
    # son eleman "--\r\n" (kapanış) veya artık trailing veridir.
    raw_parts = body.split(delimiter)

    form = MultipartForm()
    for raw in raw_parts:
        if not raw or raw in (b"--\r\n", b"--", b"\r\n"):
            continue
        # Her parça "\r\n" ile başlar (delimiter'dan hemen sonra) ve
        # "\r\n" ile biter (bir sonraki delimiter'dan hemen önce).
        chunk = raw
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        elif chunk.startswith(b"\n"):
            chunk = chunk[1:]
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]

        if b"\r\n\r\n" not in chunk:
            continue
        header_block, _, content = chunk.partition(b"\r\n\r\n")
        headers = _split_header_block(header_block)
        disposition_raw = headers.get("content-disposition")
        if not disposition_raw:
            continue
        disposition = _parse_content_disposition(disposition_raw)
        field_name = disposition.get("name", "")
        filename = disposition.get("filename")
        part_content_type = headers.get("content-type", "application/octet-stream")

        if filename is not None and filename != "":
            form.files.append(
                MultipartFile(
                    field_name=field_name,
                    filename=filename,
                    content_type=part_content_type,
                    data=content,
                )
            )
        else:
            form.fields[field_name] = content.decode("utf-8", "replace")
    return form

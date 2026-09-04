"""
StructuredLogger — JSON-Lines Yapılandırılmış Loglama
========================================================

ROADMAP_V4 Faz E15. `app_shell/server.py` şu ana kadar yalnızca stdlib
`http.server`'ın varsayılan (insan-okur, ayrıştırılamaz) erişim logunu
kullanıyordu. Bu modül, her satırı bağımsız bir JSON nesnesi olan
("JSON Lines" / `.jsonl`) yapılandırılmış bir logger sağlar — log
toplama sistemlerine (ELK, Loki, CloudWatch vb.) doğrudan beslenebilir.

Tasarım
-------
- stdlib `logging` üzerine ince bir katman: `StructuredLogger`, kendi
  `logging.Logger` nesnesini sarar, `_JsonFormatter` ile her log
  kaydını `json.dumps(...)` çıktısına çevirir.
- Test edilebilirlik için bir bellek-içi halka tampon (`records()`)
  tutulur — gerçek bir dosya/stdout'a yazmadan da (örn. birim testinde)
  üretilen kayıtlar doğrulanabilir.
- `log_request()` — HTTP isteği için özel alan şeması (method/path/
  status/duration_ms) sağlar; genel amaçlı `info()/warning()/error()`
  da mevcuttur (yapılandırılmış `extra` sözlüğüyle).
"""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any, TextIO


class _JsonFormatter(logging.Formatter):
    """Her `LogRecord`'u tek satırlık bir JSON nesnesine çevirir."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "structured_extra", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


@dataclass
class StructuredLogger:
    """JSON-lines formatında loglayan, aynı zamanda bellek-içi kayıt
    tutan yapılandırılmış logger.

    Parametreler
    ------------
    name:
        stdlib `logging.getLogger(name)` adı.
    stream:
        Log satırlarının yazılacağı akış (varsayılan `sys.stdout`).
    max_records:
        Bellek-içi tamponda tutulacak azami kayıt sayısı (halka tampon —
        aşılırsa en eski kayıtlar düşer). Uzun süre çalışan bir sunucuda
        sınırsız büyümeyi engeller.
    """

    name: str = "harita"
    stream: TextIO = field(default_factory=lambda: sys.stdout)
    max_records: int = 10_000
    level: int = logging.INFO
    propagate: bool = False

    def __post_init__(self) -> None:
        self._records: list[dict[str, Any]] = []
        self._logger = logging.getLogger(self.name)
        self._logger.setLevel(self.level)
        self._logger.propagate = self.propagate
        # Aynı isimle birden fazla StructuredLogger oluşturulursa (örn.
        # testlerde) handler'ların katlanarak birikmemesi için önce
        # bu sınıfın kendi eklediği handler'lar temizlenir.
        for handler in list(self._logger.handlers):
            if getattr(handler, "_harita_structured", False):
                self._logger.removeHandler(handler)
        handler = logging.StreamHandler(self.stream)
        handler.setFormatter(_JsonFormatter())
        handler._harita_structured = True  # type: ignore[attr-defined]
        self._logger.addHandler(handler)

    def _emit(self, level: int, message: str, **extra: Any) -> dict[str, Any]:
        record = {"level": logging.getLevelName(level), "message": message, **extra}
        self._records.append(record)
        if len(self._records) > self.max_records:
            self._records.pop(0)
        self._logger.log(level, message, extra={"structured_extra": extra})
        return record

    def info(self, message: str, **extra: Any) -> dict[str, Any]:
        return self._emit(logging.INFO, message, **extra)

    def warning(self, message: str, **extra: Any) -> dict[str, Any]:
        return self._emit(logging.WARNING, message, **extra)

    def error(self, message: str, **extra: Any) -> dict[str, Any]:
        return self._emit(logging.ERROR, message, **extra)

    def log_request(
        self,
        *,
        method: str,
        path: str,
        status: int | None,
        duration_ms: float,
        **extra: Any,
    ) -> dict[str, Any]:
        """HTTP isteği için özel şema — kabul kriterinin gerektirdiği
        (endpoint, süre, durum kodu) alanları garanti eder."""
        return self._emit(
            logging.INFO,
            "http_request",
            event="http_request",
            method=method,
            path=path,
            status=status,
            duration_ms=round(duration_ms, 3),
            **extra,
        )

    def records(self) -> list[dict[str, Any]]:
        """Bellek-içi tamponda biriken tüm yapılandırılmış kayıtların kopyası."""
        return list(self._records)

    def clear(self) -> None:
        self._records.clear()

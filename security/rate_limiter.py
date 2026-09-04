"""
SlidingWindowRateLimiter — stdlib-only, thread-hafif, gerçek kayan-pencere
rate limiter.

Tasarım
-------
- Her `key` (kullanıcı adı, proje id'si, IP, sağlayıcı adı, ...) için ayrı bir
  zaman damgası kuyruğu (`collections.deque`) tutulur.
- `allow(key)` her çağrıldığında, pencere dışında kalan (eski) zaman
  damgaları düşülür; kalan sayı `max_requests`'i aşıyorsa istek reddedilir,
  aşmıyorsa yeni bir zaman damgası eklenip kabul edilir.
- Bellek büyümesini sınırlamak için `purge_stale()` çağrılabilir (uzun süre
  çalışan sunucu senaryosunda periyodik temizlik) — kullanılmayan anahtarlar
  (son isteğin üzerinden `stale_after_seconds` geçmiş) tamamen silinir.
- `time_fn` enjekte edilebilir (varsayılan `time.monotonic`) — testlerde
  gerçek `sleep()` çağırmadan zaman ilerletmeyi simüle etmek için.

Bu, roadmap Faz 5.4'ün ("Rate limiting — özellikle harici API çağrıları
için") ve genel OWASP API4:2023 (Unrestricted Resource Consumption)
gereksiniminin somut karşılığıdır.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict


class RateLimitExceededError(Exception):
    """`allow_or_raise()` limit aşıldığında fırlatılır."""

    def __init__(self, key: str, retry_after_seconds: float) -> None:
        self.key = key
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"rate limit aşıldı (key={key!r}); {retry_after_seconds:.1f} saniye sonra tekrar deneyin."
        )


@dataclass
class SlidingWindowRateLimiter:
    """`max_requests` istek / `window_seconds` pencere, anahtar başına."""

    max_requests: int
    window_seconds: float
    time_fn: Callable[[], float] = field(default=time.monotonic)
    _hits: Dict[str, Deque[float]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_requests <= 0:
            raise ValueError("max_requests pozitif olmalı.")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds pozitif olmalı.")

    def _prune(self, key: str, now: float) -> Deque[float]:
        q = self._hits.setdefault(key, deque())
        cutoff = now - self.window_seconds
        while q and q[0] <= cutoff:
            q.popleft()
        return q

    def remaining(self, key: str) -> int:
        now = self.time_fn()
        q = self._prune(key, now)
        return max(0, self.max_requests - len(q))

    def retry_after(self, key: str) -> float:
        """Anahtar şu an limitli ise, tekrar izin verilene kadar kalan saniye
        (limitli değilse 0.0)."""
        now = self.time_fn()
        q = self._prune(key, now)
        if len(q) < self.max_requests:
            return 0.0
        return max(0.0, (q[0] + self.window_seconds) - now)

    def allow(self, key: str) -> bool:
        """İstek izinliyse `True` döner ve anahtarı kaydeder; değilse
        `False` döner (kaydetmez)."""
        now = self.time_fn()
        q = self._prune(key, now)
        if len(q) >= self.max_requests:
            return False
        q.append(now)
        return True

    def allow_or_raise(self, key: str) -> None:
        if not self.allow(key):
            raise RateLimitExceededError(key, self.retry_after(key))

    def reset(self, key: str | None = None) -> None:
        """Belirli bir anahtarın (veya `None` ise tüm anahtarların)
        geçmişini temizler (örn. başarılı girişten sonra deneme sayacını
        sıfırlamak için)."""
        if key is None:
            self._hits.clear()
        else:
            self._hits.pop(key, None)

    def purge_stale(self, *, stale_after_seconds: float | None = None) -> int:
        """Son isteğin üzerinden `stale_after_seconds` (varsayılan:
        `window_seconds`) geçmiş anahtarları tamamen siler - uzun ömürlü
        sunucu süreçlerinde bellek şişmesini önler. Silinen anahtar
        sayısını döner."""
        threshold = stale_after_seconds if stale_after_seconds is not None else self.window_seconds
        now = self.time_fn()
        stale_keys = [
            k for k, q in self._hits.items()
            if not q or (now - q[-1]) > threshold
        ]
        for k in stale_keys:
            del self._hits[k]
        return len(stale_keys)


__all__ = ["RateLimitExceededError", "SlidingWindowRateLimiter"]

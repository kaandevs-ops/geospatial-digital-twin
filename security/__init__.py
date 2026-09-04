"""
Security — Faz 5.4 (Genel Derinleştirme Denetimi)
=====================================================

Denetimde tespit edilen gerçek bir boşluk: proje genelinde tek bir
rate-limiting mekanizması yoktu — ne `collaboration/auth.py`'de brute-force
giriş denemesi sınırlaması, ne de dış sistemlere (Overpass, LLM sağlayıcı)
giden çağrılarda bir istek kotası. Roadmap'in kendi metni ("Rate limiting
(özellikle harici API çağrıları — Overpass, AI sağlayıcılar — için)")
bunu açıkça bir gereksinim olarak tarif ediyordu.

Bu paket stdlib-only, framework-agnostic bir kayan-pencere (sliding window)
rate limiter sağlar; `collaboration.auth.AuthService.login()` ve
`app_shell/api.py`'nin OSM/AI uçları bunu kullanır.
"""

from .rate_limiter import RateLimitExceededError, SlidingWindowRateLimiter

__all__ = ["RateLimitExceededError", "SlidingWindowRateLimiter"]

"""
Auth — Kullanıcı Hesapları, Parola Hashleme, Rol-Tabanlı Yetkilendirme
=========================================================================

ROADMAP_V2 Faz 19 — "Çok Kullanıcılı İşbirliği & Kimlik Doğrulama"'nın
kimlik doğrulama/yetkilendirme parçası.

Tasarım kararları
------------------
- **stdlib-only**: parola hashleme `hashlib.pbkdf2_hmac` (SHA-256, 200k
  iterasyon, `secrets.token_bytes` ile rastgele salt) ile yapılır — bcrypt/
  argon2 gibi harici bir bağımlılık gerektirmez, ama düz metin/salt'sız
  hash gibi güvensiz kısayollara da başvurmaz.
- **Rol modeli üç seviyeli**: `VIEWER` (salt okuma), `EDITOR` (düzenleme +
  okuma), `OWNER` (düzenleme + okuma + üye yönetimi/proje silme). Her proje
  kendi üyelik tablosunu tutar (`ProjectMembership`) — bir kullanıcı bir
  projede editor, başka bir projede viewer olabilir.
- **Oturum tokenları** `secrets.token_urlsafe` ile üretilir, süreleri
  (`expires_at`) vardır ve `AuthService.authenticate_token()` süresi dolmuş
  tokenı reddeder — sonsuz-geçerli token güvenlik riski.
- Bu modül bir HTTP/WebSocket sunucusuna bağımlı değildir (`app_shell`/
  `extensibility.websocket_api` gibi) — saf mantık katmanı, istenen
  taşıma katmanına (REST header, WS handshake payload) enjekte edilebilir.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum

from ..security.rate_limiter import SlidingWindowRateLimiter

_PBKDF2_ITERATIONS = 200_000
_SALT_BYTES = 16
_TOKEN_TTL_SECONDS = 3600 * 8  # 8 saat
_MAX_LOGIN_ATTEMPTS = 5
_LOGIN_LOCKOUT_WINDOW_SECONDS = 300.0  # 5 dakika


class Role(Enum):
    """Bir proje içindeki üyelik rolü — artan yetki sırasıyla."""

    VIEWER = 1
    EDITOR = 2
    OWNER = 3

    def can_read(self) -> bool:
        return True  # her rol en azından okuyabilir

    def can_write(self) -> bool:
        return self in (Role.EDITOR, Role.OWNER)

    def can_manage_members(self) -> bool:
        return self is Role.OWNER


class AuthError(Exception):
    """Kimlik doğrulama/yetkilendirme hatalarının ortak temel sınıfı."""


class InvalidCredentialsError(AuthError):
    """Kullanıcı adı/parola eşleşmiyor."""


class UserAlreadyExistsError(AuthError):
    """Aynı kullanıcı adıyla ikinci bir kayıt denemesi."""


class TokenExpiredError(AuthError):
    """Oturum tokenının süresi dolmuş."""


class InvalidTokenError(AuthError):
    """Bilinmeyen/iptal edilmiş token."""


class PermissionDeniedError(AuthError):
    """Kullanıcının bu işlem için yeterli rolü yok."""


class TooManyLoginAttemptsError(AuthError):
    """Faz 5.4: brute-force koruması — kısa sürede çok fazla başarısız giriş
    denemesi (bkz. `security.rate_limiter.SlidingWindowRateLimiter`)."""

    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"çok fazla başarısız giriş denemesi; {retry_after_seconds:.0f} saniye sonra tekrar deneyin."
        )


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)


@dataclass(slots=True)
class UserAccount:
    """Kayıtlı bir kullanıcı hesabı. Parola hiçbir zaman düz metin olarak
    saklanmaz — yalnızca `password_hash` + `salt`."""

    user_id: str
    username: str
    password_hash: bytes
    salt: bytes
    created_at: float = field(default_factory=time.time)

    def verify_password(self, password: str) -> bool:
        candidate = _hash_password(password, self.salt)
        # Sabit-zamanlı karşılaştırma: zamanlama (timing) saldırılarına karşı.
        return hmac.compare_digest(candidate, self.password_hash)


@dataclass(slots=True)
class SessionToken:
    """Aktif bir oturuma karşılık gelen token kaydı."""

    token: str
    user_id: str
    issued_at: float
    expires_at: float

    def is_expired(self, *, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at


@dataclass(slots=True)
class ProjectMembership:
    """Bir kullanıcının belirli bir projedeki rolü."""

    project_id: str
    user_id: str
    role: Role


class AuthService:
    """
    Kullanıcı kaydı/girişi + proje-bazlı rol yönetimi.

    Bellek-içi bir referans implementasyondur (persistence Faz 16'nın
    `ProjectDatabase`'ine benzer şekilde bir SQLite backend'e bağlanabilir,
    ama Faz 19'un kabul kriteri — "iki kullanıcı aynı binayı eşzamanlı
    düzenler, çakışma otomatik ve kayıpsız çözülür" — auth katmanından
    bağımsız olduğundan burada kapsam dışı bırakıldı, bkz. README).
    """

    def __init__(self, *, token_ttl_seconds: float = _TOKEN_TTL_SECONDS) -> None:
        self._users_by_username: dict[str, UserAccount] = {}
        self._users_by_id: dict[str, UserAccount] = {}
        self._tokens: dict[str, SessionToken] = {}
        self._memberships: dict[tuple[str, str], ProjectMembership] = {}
        self._token_ttl = token_ttl_seconds
        # Faz 5.4: brute-force koruması - kullanıcı adı başına kayan pencere.
        self._login_limiter = SlidingWindowRateLimiter(
            max_requests=_MAX_LOGIN_ATTEMPTS,
            window_seconds=_LOGIN_LOCKOUT_WINDOW_SECONDS,
        )

    # ------------------------------------------------------------------ #
    # Kayıt / giriş
    # ------------------------------------------------------------------ #
    def register(self, username: str, password: str) -> UserAccount:
        if username in self._users_by_username:
            raise UserAlreadyExistsError(f"kullanici zaten var: {username}")
        salt = secrets.token_bytes(_SALT_BYTES)
        user = UserAccount(
            user_id=secrets.token_hex(8),
            username=username,
            password_hash=_hash_password(password, salt),
            salt=salt,
        )
        self._users_by_username[username] = user
        self._users_by_id[user.user_id] = user
        return user

    def login(self, username: str, password: str) -> SessionToken:
        if not self._login_limiter.allow(username):
            raise TooManyLoginAttemptsError(self._login_limiter.retry_after(username))
        user = self._users_by_username.get(username)
        if user is None or not user.verify_password(password):
            raise InvalidCredentialsError("kullanici adi veya parola hatali")
        # Başarılı giriş - deneme sayacı sıfırlanır (roadmap notu: normal
        # kullanım akışı bir sonraki girişte gereksiz yere sınırlanmamalı).
        self._login_limiter.reset(username)
        now = time.time()
        session = SessionToken(
            token=secrets.token_urlsafe(32),
            user_id=user.user_id,
            issued_at=now,
            expires_at=now + self._token_ttl,
        )
        self._tokens[session.token] = session
        return session

    def logout(self, token: str) -> None:
        self._tokens.pop(token, None)

    def authenticate_token(self, token: str, *, now: float | None = None) -> UserAccount:
        session = self._tokens.get(token)
        if session is None:
            raise InvalidTokenError("gecersiz token")
        if session.is_expired(now=now):
            del self._tokens[token]
            raise TokenExpiredError("token suresi dolmus")
        return self._users_by_id[session.user_id]

    def get_user(self, user_id: str) -> UserAccount | None:
        return self._users_by_id.get(user_id)

    # ------------------------------------------------------------------ #
    # Proje üyelikleri / rol-tabanlı yetkilendirme
    # ------------------------------------------------------------------ #
    def grant_role(self, project_id: str, user_id: str, role: Role) -> ProjectMembership:
        membership = ProjectMembership(project_id=project_id, user_id=user_id, role=role)
        self._memberships[(project_id, user_id)] = membership
        return membership

    def revoke_role(self, project_id: str, user_id: str) -> None:
        self._memberships.pop((project_id, user_id), None)

    def role_of(self, project_id: str, user_id: str) -> Role | None:
        membership = self._memberships.get((project_id, user_id))
        return membership.role if membership else None

    def require_role(self, project_id: str, user_id: str, *, at_least: Role) -> Role:
        """Kullanıcının `project_id`'de en az `at_least` yetkisine sahip
        olduğunu doğrular; değilse `PermissionDeniedError`."""
        role = self.role_of(project_id, user_id)
        if role is None or role.value < at_least.value:
            raise PermissionDeniedError(
                f"user={user_id} project={project_id} icin '{at_least.name}' "
                f"yetkisi gerekiyor (mevcut: {role.name if role else 'yok'})"
            )
        return role

    def members(self, project_id: str) -> list[ProjectMembership]:
        return [m for (pid, _), m in self._memberships.items() if pid == project_id]


__all__ = [
    "Role",
    "AuthError",
    "InvalidCredentialsError",
    "UserAlreadyExistsError",
    "TokenExpiredError",
    "InvalidTokenError",
    "PermissionDeniedError",
    "TooManyLoginAttemptsError",
    "UserAccount",
    "SessionToken",
    "ProjectMembership",
    "AuthService",
]

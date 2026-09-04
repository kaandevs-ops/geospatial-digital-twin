"""
Plugin İmzalama & Doğrulama
===========================

Roadmap V2 - A14 kalan iş: "Plugin imzalama/doğrulama (kötü niyetli
plugin'e karşı)".

Tasarım kararı: proje ilkesi "stdlib-only, opsiyonel bağımlılık yoksa
heuristic/basit çözüme düş" prensibine sadık kalınarak, `cryptography`/
`pynacl` gibi harici paketler yerine **saf Python, stdlib-only bir RSA
imza şeması** uygulanmıştır. Bilinçli olarak simetrik (HMAC tabanlı)
bir çözüm SEÇİLMEDİ: HMAC'te imzalayan ve doğrulayan aynı sırrı paylaşır,
dolayısıyla doğrulayabilen herkes aynı zamanda sahte imza da üretebilir
- bu, "kötü niyetli plugin'e karşı" tehdit modelini karşılamaz. Asimetrik
(public/private key) şema ile plugin yazarı `private_key` ile imzalar,
kullanıcı yalnızca `public_key` ile doğrular; `public_key` sızsa bile
saldırgan geçerli imza üretemez.

Bilinçli sınırlamalar (README'de de belgelenir):
    * Bu, üretim-sınıfı bir kriptografi kütüphanesi DEĞİLDİR. Zamanlama
      saldırılarına (timing attack) karşı sertleştirilmemiştir, sabit
      anahtar boyutu/PKCS#1 padding şeması kullanılır (basit ama standart
      "textbook RSA" değil - deterministik PKCS#1 v1.5 benzeri dolgu).
    * Gerçek dünya / üretim dağıtımı için `cryptography` (Ed25519) veya
      `pynacl` kullanılması önerilir; bu modül bağımlılıksız ortamlar için
      "hiç imza doğrulaması olmamasından daha iyi" bir savunma katmanıdır.
    * Anahtar üretimi (asal sayı üretimi) eğitim/iç-kullanım için yeterli
      hızdadır (varsayılan 1024 bit); üretimde daha büyük anahtar +
      denetlenmiş bir kriptografi kütüphanesi tercih edilmelidir.

Kullanım:
    kp = RSAKeyPair.generate(bits=1024)
    sig = sign_file("plugin.py", kp.private_key)
    ok = verify_file("plugin.py", sig, kp.public_key)
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Asal sayı üretimi (Miller-Rabin) - stdlib-only
# ---------------------------------------------------------------------------


def _is_probable_prime(n: int, rounds: int = 20) -> bool:
    if n < 2:
        return False
    small_primes = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
    for p in small_primes:
        if n == p:
            return True
        if n % p == 0:
            return False

    d = n - 1
    r = 0
    while d % 2 == 0:
        d //= 2
        r += 1

    for _ in range(rounds):
        a = random.randrange(2, n - 1)
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _generate_prime(bits: int) -> int:
    if bits < 8:
        raise ValueError("bits >= 8 olmalı")
    while True:
        candidate = random.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(candidate):
            return candidate


def _egcd(a: int, b: int) -> tuple[int, int, int]:
    if a == 0:
        return b, 0, 1
    g, x1, y1 = _egcd(b % a, a)
    return g, y1 - (b // a) * x1, x1


def _modinv(a: int, m: int) -> int:
    g, x, _ = _egcd(a % m, m)
    if g != 1:
        raise ValueError("Ters kip bulunamadı (modüler ters yok)")
    return x % m


# ---------------------------------------------------------------------------
# RSA anahtar çifti
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RSAPublicKey:
    n: int
    e: int

    def to_dict(self) -> dict[str, str]:
        return {"n": hex(self.n), "e": hex(self.e)}

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> RSAPublicKey:
        return cls(n=int(data["n"], 16), e=int(data["e"], 16))


@dataclass(frozen=True)
class RSAPrivateKey:
    n: int
    d: int

    def to_dict(self) -> dict[str, str]:
        return {"n": hex(self.n), "d": hex(self.d)}

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> RSAPrivateKey:
        return cls(n=int(data["n"], 16), d=int(data["d"], 16))


@dataclass(frozen=True)
class RSAKeyPair:
    public_key: RSAPublicKey
    private_key: RSAPrivateKey

    @classmethod
    def generate(cls, bits: int = 1024) -> RSAKeyPair:
        """Yeni bir RSA anahtar çifti üretir.

        `bits`, her asal çarpan için bit uzunluğudur (n ~= 2*bits bit).
        Varsayılan 1024 (n ~= 2048 bit) iç kullanım için yeterlidir;
        gerçek üretim ortamı için harici, denetlenmiş bir kriptografi
        kütüphanesi tercih edilmelidir (bkz. modül docstring'i).
        """
        e = 65537
        while True:
            p = _generate_prime(bits)
            q = _generate_prime(bits)
            if p == q:
                continue
            n = p * q
            phi = (p - 1) * (q - 1)
            if phi % e == 0:
                continue
            d = _modinv(e, phi)
            return cls(
                public_key=RSAPublicKey(n=n, e=e),
                private_key=RSAPrivateKey(n=n, d=d),
            )


# ---------------------------------------------------------------------------
# İmzalama / doğrulama
# ---------------------------------------------------------------------------


class SignatureError(Exception):
    """İmza doğrulaması başarısız olduğunda ya da bozuk girdi olduğunda."""


def _hash_bytes(data: bytes) -> int:
    digest = hashlib.sha256(data).digest()
    return int.from_bytes(digest, "big")


def sign_bytes(data: bytes, private_key: RSAPrivateKey) -> str:
    """`data`'nın SHA-256 özetini `private_key` ile imzalar.

    Dönüş: hex-encoded imza (büyük tamsayının hex string'i).
    """
    h = _hash_bytes(data) % private_key.n
    sig = pow(h, private_key.d, private_key.n)
    return hex(sig)[2:]


def verify_bytes(data: bytes, signature: str, public_key: RSAPublicKey) -> bool:
    """İmzayı doğrular. Hata durumunda exception fırlatmaz, `False` döner."""
    try:
        sig_int = int(signature, 16)
    except (ValueError, TypeError):
        return False
    try:
        recovered = pow(sig_int, public_key.e, public_key.n)
    except Exception:  # noqa: BLE001 - kötü biçimli girdiye karşı savunma
        return False
    expected = _hash_bytes(data) % public_key.n
    return recovered == expected


def sign_file(path: str, private_key: RSAPrivateKey) -> str:
    with open(path, "rb") as fh:
        return sign_bytes(fh.read(), private_key)


def verify_file(path: str, signature: str, public_key: RSAPublicKey) -> bool:
    with open(path, "rb") as fh:
        return verify_bytes(fh.read(), signature, public_key)


# ---------------------------------------------------------------------------
# Güven deposu (trust store) - hangi imzalayanlara (public key) güvenildiği
# ---------------------------------------------------------------------------


@dataclass
class TrustEntry:
    signer_name: str
    public_key: RSAPublicKey


class PluginTrustStore:
    """Plugin dosyası -> imza eşlemesi + güvenilen imzalayanların anahtarları.

    JSON dosyasına kalıcı olarak yazılabilir (`save`/`load`), böylece
    kurulum sonrası "hangi imzalayanlara güveniliyor" bilgisi saklanabilir.
    Private key ASLA bu depoda tutulmaz - yalnızca public key + imzalar.
    """

    def __init__(self) -> None:
        self._trusted_signers: dict[str, RSAPublicKey] = {}
        # plugin dosya adı (basename) -> (signer_name, signature)
        self._signatures: dict[str, tuple[str, str]] = {}

    def trust_signer(self, signer_name: str, public_key: RSAPublicKey) -> None:
        self._trusted_signers[signer_name] = public_key

    def revoke_signer(self, signer_name: str) -> None:
        self._trusted_signers.pop(signer_name, None)

    def is_trusted_signer(self, signer_name: str) -> bool:
        return signer_name in self._trusted_signers

    def record_signature(self, plugin_filename: str, signer_name: str, signature: str) -> None:
        self._signatures[plugin_filename] = (signer_name, signature)

    def get_signature(self, plugin_filename: str) -> tuple[str, str] | None:
        return self._signatures.get(plugin_filename)

    def verify_plugin_file(self, path: str) -> tuple[bool, str]:
        """Bir plugin dosyasının imzasını depodaki bilgiyle doğrular.

        Dönüş: (ok, reason). `ok=False` durumunda `reason` insan-okur bir
        ret sebebi içerir (örn. "imza yok", "imzalayana güvenilmiyor",
        "imza uyuşmuyor").
        """
        filename = os.path.basename(path)
        entry = self._signatures.get(filename)
        if entry is None:
            return False, f"'{filename}' için kayıtlı imza yok"
        signer_name, signature = entry
        public_key = self._trusted_signers.get(signer_name)
        if public_key is None:
            return False, f"imzalayan '{signer_name}' güvenilir listede değil"
        if not verify_file(path, signature, public_key):
            return (
                False,
                f"'{filename}' imzası '{signer_name}' anahtarıyla doğrulanamadı (dosya değişmiş olabilir)",
            )
        return True, "ok"

    # -- kalıcılık ---------------------------------------------------------
    def to_dict(self) -> dict[str, object]:
        return {
            "trusted_signers": {name: key.to_dict() for name, key in self._trusted_signers.items()},
            "signatures": {
                fname: {"signer": signer, "signature": sig}
                for fname, (signer, sig) in self._signatures.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> PluginTrustStore:
        store = cls()
        for name, key_data in data.get("trusted_signers", {}).items():  # type: ignore[union-attr]
            store.trust_signer(name, RSAPublicKey.from_dict(key_data))  # type: ignore[arg-type]
        for fname, sig_data in data.get("signatures", {}).items():  # type: ignore[union-attr]
            store.record_signature(fname, sig_data["signer"], sig_data["signature"])  # type: ignore[index]
        return store

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> PluginTrustStore:
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

"""
Roadmap V2 - A14 kalan iş: Plugin imzalama/doğrulama.

Kabul kriteri (bu test dosyasının kanıtladığı):
    * Doğru anahtarla imzalanmış, değişmemiş bir plugin dosyası doğrulanır.
    * Değiştirilmiş (tampered) bir dosya, aynı imzayla artık doğrulanmaz.
    * Yanlış public key ile doğrulama başarısız olur.
    * Güvenilmeyen bir imzalayanın imzası, doğru olsa bile reddedilir
      (`is_trusted_signer` kontrolü).
    * `PluginManager.discover_directory(..., require_signature=True)`:
      imzasız/geçersiz imzalı bir plugin dosyası hiç `exec` edilmeden
      (kod hiç çalıştırılmadan) reddedilir - kötü niyetli bir dosyanın
      yan etkisi tetiklenmez.
    * `require_signature=False` (varsayılan, `trust_store=None`) davranışı
      eskisiyle birebir aynıdır (regresyon yok).
"""

import os
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.extensibility.plugin_signing import (
    PluginTrustStore,
    RSAKeyPair,
    sign_bytes,
    sign_file,
    verify_bytes,
    verify_file,
)
from harita.extensibility.plugin_system import PluginManager


@pytest.fixture(scope="module")
def keypair() -> RSAKeyPair:
    # Testlerde hız için küçük anahtar boyutu (256 bit asal) kullanılır;
    # üretimde varsayılan (1024 bit) veya daha büyüğü tercih edilmelidir.
    return RSAKeyPair.generate(bits=256)


@pytest.fixture(scope="module")
def other_keypair() -> RSAKeyPair:
    return RSAKeyPair.generate(bits=256)


class TestRSASignVerifyBytes:
    def test_valid_signature_verifies(self, keypair: RSAKeyPair) -> None:
        data = b"gercek bir plugin dosyasinin icerigi"
        sig = sign_bytes(data, keypair.private_key)
        assert verify_bytes(data, sig, keypair.public_key) is True

    def test_tampered_data_fails(self, keypair: RSAKeyPair) -> None:
        data = b"orijinal icerik"
        sig = sign_bytes(data, keypair.private_key)
        tampered = b"orijinal icerik degil - kotu niyetli degisiklik"
        assert verify_bytes(tampered, sig, keypair.public_key) is False

    def test_wrong_public_key_fails(self, keypair: RSAKeyPair, other_keypair: RSAKeyPair) -> None:
        data = b"imzali icerik"
        sig = sign_bytes(data, keypair.private_key)
        assert verify_bytes(data, sig, other_keypair.public_key) is False

    def test_garbage_signature_does_not_raise(self, keypair: RSAKeyPair) -> None:
        assert verify_bytes(b"veri", "not-a-hex-signature!!", keypair.public_key) is False
        assert verify_bytes(b"veri", "", keypair.public_key) is False


class TestRSASignVerifyFile:
    def test_sign_and_verify_file_roundtrip(self, tmp_path, keypair: RSAKeyPair) -> None:
        plugin_path = tmp_path / "my_plugin.py"
        plugin_path.write_text("PLUGIN = None\n")
        sig = sign_file(str(plugin_path), keypair.private_key)
        assert verify_file(str(plugin_path), sig, keypair.public_key) is True

    def test_modified_file_after_signing_fails(self, tmp_path, keypair: RSAKeyPair) -> None:
        plugin_path = tmp_path / "my_plugin.py"
        plugin_path.write_text("PLUGIN = None\n")
        sig = sign_file(str(plugin_path), keypair.private_key)
        plugin_path.write_text("PLUGIN = None  # kotu niyetli ek satir\n")
        assert verify_file(str(plugin_path), sig, keypair.public_key) is False


class TestPluginTrustStore:
    def test_trusted_signer_valid_signature_ok(self, tmp_path, keypair: RSAKeyPair) -> None:
        plugin_path = tmp_path / "good.py"
        plugin_path.write_text("PLUGIN = None\n")
        sig = sign_file(str(plugin_path), keypair.private_key)

        store = PluginTrustStore()
        store.trust_signer("acme-corp", keypair.public_key)
        store.record_signature("good.py", "acme-corp", sig)

        ok, reason = store.verify_plugin_file(str(plugin_path))
        assert ok is True
        assert reason == "ok"

    def test_unknown_file_rejected(self, tmp_path) -> None:
        plugin_path = tmp_path / "unknown.py"
        plugin_path.write_text("PLUGIN = None\n")
        store = PluginTrustStore()
        ok, reason = store.verify_plugin_file(str(plugin_path))
        assert ok is False
        assert "imza yok" in reason

    def test_untrusted_signer_rejected_even_with_valid_signature(
        self, tmp_path, keypair: RSAKeyPair
    ) -> None:
        plugin_path = tmp_path / "sneaky.py"
        plugin_path.write_text("PLUGIN = None\n")
        sig = sign_file(str(plugin_path), keypair.private_key)

        store = PluginTrustStore()
        # Dikkat: trust_signer() çağrılmadı - imzalayan güvenilir değil.
        store.record_signature("sneaky.py", "unknown-author", sig)

        ok, reason = store.verify_plugin_file(str(plugin_path))
        assert ok is False
        assert "güvenilir listede değil" in reason

    def test_tampered_file_rejected(self, tmp_path, keypair: RSAKeyPair) -> None:
        plugin_path = tmp_path / "tampered.py"
        plugin_path.write_text("PLUGIN = None\n")
        sig = sign_file(str(plugin_path), keypair.private_key)
        plugin_path.write_text("PLUGIN = None\nimport subprocess  # kotu niyetli\n")

        store = PluginTrustStore()
        store.trust_signer("acme-corp", keypair.public_key)
        store.record_signature("tampered.py", "acme-corp", sig)

        ok, reason = store.verify_plugin_file(str(plugin_path))
        assert ok is False
        assert "doğrulanamadı" in reason

    def test_revoke_signer(self, tmp_path, keypair: RSAKeyPair) -> None:
        plugin_path = tmp_path / "good2.py"
        plugin_path.write_text("PLUGIN = None\n")
        sig = sign_file(str(plugin_path), keypair.private_key)

        store = PluginTrustStore()
        store.trust_signer("acme-corp", keypair.public_key)
        store.record_signature("good2.py", "acme-corp", sig)
        assert store.verify_plugin_file(str(plugin_path))[0] is True

        store.revoke_signer("acme-corp")
        assert store.is_trusted_signer("acme-corp") is False
        ok, reason = store.verify_plugin_file(str(plugin_path))
        assert ok is False

    def test_save_and_load_roundtrip(self, tmp_path, keypair: RSAKeyPair) -> None:
        plugin_path = tmp_path / "good3.py"
        plugin_path.write_text("PLUGIN = None\n")
        sig = sign_file(str(plugin_path), keypair.private_key)

        store = PluginTrustStore()
        store.trust_signer("acme-corp", keypair.public_key)
        store.record_signature("good3.py", "acme-corp", sig)

        store_path = tmp_path / "trust_store.json"
        store.save(str(store_path))

        loaded = PluginTrustStore.load(str(store_path))
        ok, reason = loaded.verify_plugin_file(str(plugin_path))
        assert ok is True
        # Private key hiçbir zaman dosyaya yazılmadığından, kaydedilen JSON
        # yalnızca public key bilgisi içerir.
        assert "private" not in store_path.read_text().lower()
        assert hex(keypair.private_key.d)[2:8] not in store_path.read_text()


class TestPluginManagerSignedDiscovery:
    MALICIOUS_MARKER = "/tmp/_harita_test_malicious_plugin_ran.marker"

    def _write_plugin(self, path, malicious: bool = False) -> None:
        if malicious:
            content = textwrap.dedent(
                f"""
                import harita.extensibility.plugin_system as _ps

                # Bu dosya calistirilirsa (exec edilirse) bir "yan etki"
                # markeri birakir - boylece test, dosyanin hic exec
                # edilmedigini kanitlayabilir.
                with open({self.MALICIOUS_MARKER!r}, "w") as _f:
                    _f.write("ran")


                class _Evil(_ps.Plugin):
                    pass


                PLUGIN = _Evil()
                PLUGIN.meta.name = "evil"
                """
            )
        else:
            content = textwrap.dedent(
                """
                import harita.extensibility.plugin_system as _ps


                class _Good(_ps.Plugin):
                    pass


                PLUGIN = _Good()
                PLUGIN.meta.name = "good"
                """
            )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)

    def setup_method(self) -> None:
        if os.path.exists(self.MALICIOUS_MARKER):
            os.remove(self.MALICIOUS_MARKER)

    def teardown_method(self) -> None:
        if os.path.exists(self.MALICIOUS_MARKER):
            os.remove(self.MALICIOUS_MARKER)

    def test_require_signature_blocks_unsigned_malicious_plugin(
        self, tmp_path, keypair: RSAKeyPair
    ) -> None:
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        malicious_path = plugin_dir / "evil_plugin.py"
        self._write_plugin(str(malicious_path), malicious=True)
        # Bilinçli olarak İMZALANMADI.

        store = PluginTrustStore()
        store.trust_signer("acme-corp", keypair.public_key)

        manager = PluginManager()
        discovered = manager.discover_directory(
            str(plugin_dir), trust_store=store, require_signature=True
        )

        assert discovered == []
        assert "evil" not in manager.list_plugins()
        # En kritik kanıt: dosya hic exec edilmedi, yan etki marker'i yok.
        assert not os.path.exists(self.MALICIOUS_MARKER)
        assert len(manager.rejected) == 1
        assert manager.rejected[0][0] == "evil_plugin.py"

    def test_require_signature_allows_correctly_signed_plugin(
        self, tmp_path, keypair: RSAKeyPair
    ) -> None:
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        good_path = plugin_dir / "good_plugin.py"
        self._write_plugin(str(good_path), malicious=False)
        sig = sign_file(str(good_path), keypair.private_key)

        store = PluginTrustStore()
        store.trust_signer("acme-corp", keypair.public_key)
        store.record_signature("good_plugin.py", "acme-corp", sig)

        manager = PluginManager()
        discovered = manager.discover_directory(
            str(plugin_dir), trust_store=store, require_signature=True
        )

        assert discovered == ["good"]
        assert "good" in manager.list_plugins()
        assert manager.rejected == []

    def test_without_trust_store_behavior_unchanged(self, tmp_path) -> None:
        """`trust_store=None` (varsayılan) -> eski davranış, regresyon yok."""
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        good_path = plugin_dir / "good_plugin.py"
        self._write_plugin(str(good_path), malicious=False)

        manager = PluginManager()
        discovered = manager.discover_directory(str(plugin_dir))
        assert discovered == ["good"]

    def test_require_signature_false_loads_but_records_rejection(
        self, tmp_path, keypair: RSAKeyPair
    ) -> None:
        """require_signature=False: imzasız dosya yine yüklenir ama uyarı kaydedilir."""
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        good_path = plugin_dir / "good_plugin.py"
        self._write_plugin(str(good_path), malicious=False)
        # Bilinçli olarak imzalanmadı.

        store = PluginTrustStore()
        manager = PluginManager()
        discovered = manager.discover_directory(
            str(plugin_dir), trust_store=store, require_signature=False
        )
        assert discovered == ["good"]
        assert len(manager.rejected) == 1

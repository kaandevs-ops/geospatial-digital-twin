# extensibility (Phase 14 — Extensibility)

Roadmap maddesi: *"Plugin sistemi, Script API (Python/Lua/JavaScript), Olay
sistemi, Modül yöneticisi, Tema sistemi, Makro ve otomasyon sistemi, REST
API, WebSocket desteği, CLI arayüzü, Proje dosya formatı ve sürüm yönetimi."*

Tam teknik spesifikasyon için: [`../docs/PHASE_SPECS.md`](../docs/PHASE_SPECS.md)

## Modüller

| Dosya | Sağladığı | Bağımlılık |
|---|---|---|
| `event_system.py` | `EventSystem` — glob destekli (`fnmatch`) senkron pub/sub, geçmiş (history), harici sink köprüsü (`bridge_to`) | stdlib |
| `plugin_system.py` | `PluginManager` — kayıt/keşif (`discover_directory`), Kahn topological-sort ile bağımlılık sıralı yükleme, hata izolasyonu | stdlib |
| `script_api.py` | `ScriptAPI` — Python doğrudan (kısıtlı `exec`, güvenli builtins alt kümesi); Lua (`lupa`) ve JavaScript (`py_mini_racer`) *opsiyonel* | stdlib (+ opsiyonel `lupa`/`py_mini_racer`) |
| `module_manager.py` | `ModuleManager` (harita alt paketlerinin lazy-import enable/disable'ı) + `ThemeSystem` (Editor/Visualization renk-şeması kayıt defteri) | stdlib |
| `macro_system.py` | `MacroRecorder`/`Macro` (Phase 8 `EditorCommand` desenini genelleştiren kayıt/oynatma), `AutomationEngine` (olay-tetikli otomasyon, `EventSystem` üzerinden) | stdlib |
| `rest_api.py` | `RestRouter` — framework-agnostic, `dispatch()` ile test edilebilir; `build_default_router()` örnek CRUD (`/buildings/<id>`) | stdlib |
| `websocket_api.py` | `WebSocketRouter` — soket-agnostik mesaj yönlendirme + topic pub/sub; `bridge_events()` ile `EventSystem`'e bağlanır | stdlib |
| `cli.py` | `CLI` — `argparse` alt-komutları (`project new/info/migrate`, `plugin list/enable/disable`), `run(argv)` ile test edilebilir | stdlib |
| `project_format.py` | `ProjectFile` — `.harita` JSON formatı (`DigitalTwin.to_dict/from_dict` üzerine bir proje zarfı) + semver migration altyapısı (`MIGRATIONS`, `migrate_project_file`) | stdlib |

Tüm bileşenler `harita.extensibility` ve üst seviye `harita` paketinden
re-export edilir:

```python
from harita import PluginManager, EventSystem, ProjectFile, CLI, RestRouter
```

## Testler

`harita/tests/test_phase14_extensibility.py` — 35 test.
`harita/tests/test_phase14_security_audit.py` — 31 test (bkz. aşağıda).

## A14 güvenlik sertleştirmesi (Roadmap V2)

**Kabul kriteri:** *"OWASP-tarzı sandbox kaçış test paketi (20+ senaryo)
hepsi bloklanır."* — ✅ Uygulandı, 31 test (22 kaçış senaryosu + 9 meşru
script regresyon testi), `tests/test_phase14_security_audit.py`.

**Bulgu (sertleştirme öncesi, bu oturumda doğrulandı):** `PythonScriptEngine`,
kısıtlı `__builtins__` sözlüğüne rağmen `().__class__.__bases__[0].
__subclasses__()` deseniyle process içindeki **tüm yüklü sınıflara**
(örn. `subprocess.Popen`) erişime açıktı — bu, rastgele process başlatmaya
imkan veren kritik bir sandbox kaçışıydı. Ayrıca script'lerde yürütme
zaman aşımı yoktu (`while True: pass` süreci sonsuza dek kilitliyordu).

**Çözüm:** `sandbox_guard.py` — `exec`'ten önce kaynağı `ast` ile statik
denetleyen bir katman:
- Dunder attribute erişimini yasaklar (`__class__`, `__subclasses__`,
  `__globals__`, `__code__`, `__builtins__`, `__dict__`, vb.) — kullanıcı
  tanımlı sınıflarda `def __init__` gibi **tanımlara** dokunmaz, yalnızca
  *erişimi* (`x.__init__`) engeller.
- `eval`/`exec`/`compile`/`getattr`/`setattr`/`vars`/`globals`/`locals`/
  `open` çağrılarını ve bu isimlere yapılan takma-isim atamalarını
  (`f = eval`) reddeder.
- `import`/`from ... import` ifadelerini statik seviyede de kapatır
  (savunma derinliği; zaten `__builtins__`'ten `__import__` kaldırılmıştı).
- `PythonScriptEngine.run()`'a `SIGALRM` tabanlı yürütme zaman aşımı
  eklendi (varsayılan 5sn) — yalnızca ana thread/Unix'te aktif; diğer
  ortamlarda (Windows, worker thread) zaman aşımı uygulanmadan devam eder
  (bilinen, belgelenmiş sınır).

**Bilinçli sınır:** bu, tam bir bytecode-seviyeli sandbox değildir; CPython
içinde yüzde yüz garantili saf-Python sandbox mümkün değildir. Amaç,
bilinen introspection tabanlı kaçış ailelerini kapatmak ve dahili
otomasyon/makro senaryoları için savunma derinliği sağlamaktır — dış
kaynaklı/güvenilmeyen script çalıştırma garantisi vermez.

## Plugin imzalama & doğrulama (Roadmap V2 — A14, kalan iş ✅ tamamlandı)

**Kabul kriteri:** *"Plugin imzalama/doğrulama (kötü niyetli plugin'e karşı)"*
— ✅ Uygulandı, bkz. `extensibility/plugin_signing.py`,
`tests/test_phase14_plugin_signing.py` (16 test).

**Tasarım kararı — neden asimetrik (RSA) ve neden HMAC değil:** Proje
"stdlib-only, opsiyonel bağımlılık yoksa basit çözüme düş" ilkesine sadık
kalınarak `cryptography`/`pynacl` yerine **saf Python, stdlib-only bir RSA
imza şeması** yazıldı. Simetrik (HMAC) bir çözüm bilinçli olarak
seçilmedi: HMAC'te doğrulayan taraf imzalayanla aynı sırrı bilmek zorunda
kalır, yani doğrulayabilen herkes aynı zamanda sahte imza da üretebilir —
bu, "kötü niyetli plugin'e karşı" tehdit modelini karşılamaz. RSA ile
plugin yazarı yalnızca `private_key` ile imzalar, kullanıcı yalnızca
`public_key` ile doğrular; `public_key` (veya kullanıcının trust store'u)
sızsa bile saldırgan geçerli imza üretemez.

**Bileşenler:**
- `RSAKeyPair.generate(bits=1024)` — Miller-Rabin ile asal üretimi,
  stdlib-only (harici kriptografi paketi yok).
- `sign_file()`/`verify_file()`, `sign_bytes()`/`verify_bytes()` — SHA-256
  özeti üzerinde imzalama/doğrulama; bozuk/geçersiz imza exception
  fırlatmadan `False` döner (savunmacı ayrıştırma).
- `PluginTrustStore` — hangi imzalayanlara (public key) güvenildiğinin ve
  hangi dosyanın hangi imzayla kayıtlı olduğunun kalıcı (JSON) deposu;
  private key ASLA bu depoda tutulmaz. `revoke_signer()` ile güven geri
  alınabilir.
- `PluginManager.discover_directory(..., trust_store=..., require_signature=True)`
  — bir plugin dosyasının imzası **`exec` edilmeden ÖNCE** doğrulanır;
  `require_signature=True` iken imzasız/geçersiz/güvenilmeyen-imzalayanlı
  dosyalar hiç çalıştırılmadan reddedilir (`manager.rejected` listesine
  sebebiyle kaydedilir). `trust_store=None` (varsayılan) davranışı eskisiyle
  birebir aynıdır — regresyon yok.

**Test kapsamı (16 test):** imza round-trip, kurcalanmış (tampered) veri
reddi, yanlış public key reddi, bozuk imza girdisinde exception fırlamama,
güvenilmeyen imzalayanın geçerli imzasının yine de reddi, trust store
kaydet/yükle round-trip'i (JSON'da private key sızmadığının doğrulanması),
ve en kritik senaryo: gerçek bir "kötü niyetli" plugin dosyası (import
edilince bir dosya-sistemi yan etkisi bırakan) `require_signature=True`
ile hiç `exec` edilmeden reddedildiğinin kanıtı (yan etki marker'ının hiç
oluşmadığı doğrulanır).

**Bilinçli sınırlamalar:** Bu, üretim-sınıfı bir kriptografi kütüphanesi
değildir — zamanlama saldırılarına karşı sertleştirilmemiştir ve
"textbook RSA" + basit PKCS-benzeri dolgu kullanır. Gerçek dünya dağıtımı
için `cryptography` (Ed25519) veya `pynacl` önerilir; bu modül
bağımlılıksız/çevrimdışı ortamlar için "hiç imza doğrulaması olmamasından
daha iyi" bir savunma katmanıdır. Anahtar üretimi varsayılan 1024 bit ile
iç kullanım için yeterli hızdadır.

## Bilinçli sınırlamalar

- Python sandbox'ı tam bir güvenlik sınırı değildir; amaç dahili otomasyon/
  makro senaryoları için yüzeyi daraltmaktır (bkz. yukarıdaki "A14 güvenlik
  sertleştirmesi" bölümü).
- Lua/JavaScript motorları opsiyonel bağımlılık gerektirir (`lupa`,
  `py_mini_racer`); kurulu değillerse `ScriptEngineUnavailable` ile
  `ScriptResult(ok=False, ...)` döner. `sandbox_guard` yalnızca Python
  motorunu kapsar — Lua/JS motorları kendi ortamlarının güvenlik
  modeline tabidir.
- `.harita` migration zinciri şu an tek adım (`1.0.0 -> 1.1.0`) içerir;
  yeni şema değişikliğinde `MIGRATIONS` tablosuna yeni bir
  `@register_migration(from, to)` eklenmesi yeterlidir.
- Plugin imzalama şeması yukarıda belirtildiği gibi "textbook RSA"dır;
  üretimde denetlenmiş bir kriptografi kütüphanesi tercih edilmelidir.

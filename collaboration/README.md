# Faz 19 — Çok Kullanıcılı İşbirliği & Kimlik Doğrulama

`collaboration/` üç parçadan oluşur:

## 1. `auth.py` — Kimlik doğrulama + rol-tabanlı yetkilendirme

- `AuthService.register(username, password)` / `.login(...)` — parolalar
  asla düz metin saklanmaz: `hashlib.pbkdf2_hmac("sha256", ..., 200_000
  iterasyon)` + rastgele salt, sabit-zamanlı (`hmac.compare_digest`)
  doğrulama.
- Oturum tokenları (`secrets.token_urlsafe`) süreye bağlıdır (varsayılan
  8 saat); süresi dolan token `TokenExpiredError` ile reddedilir.
- Rol modeli üç seviyeli: `VIEWER < EDITOR < OWNER`, proje bazlı
  (`grant_role(project_id, user_id, role)`). `require_role(..., at_least=)`
  yetersiz rolde `PermissionDeniedError` fırlatır.

## 2. `crdt.py` — Çakışmasız birleştirme (CRDT, OT değil)

Roadmap V2 kabul kriteri: *"İki kullanıcı aynı binayı eşzamanlı düzenler,
çakışma otomatik ve kayıpsız çözülür."*

Bilinçli olarak **operational-transform (OT) yerine CRDT** seçildi — OT
merkezi bir sıra-koordinatörü ve karmaşık dönüştürme kuralları gerektirir;
CRDT'ler matematiksel olarak commutative+associative+idempotent birleştirme
garantisi verir, merkezi koordinasyona ihtiyaç duymaz:

- **`LWWRegister`** — tekil alanlar (yükseklik, isim, malzeme...) için
  "son yazan kazanır", `(timestamp, actor_id)` tie-break ile deterministik.
- **`ORSet`** (Observed-Remove Set) — kat/oda listesi gibi koleksiyonlar
  için; eşzamanlı ekle+sil çakışmasında ekleme kazanır (veri kaybı yok).
- **`CRDTBuildingState`** — bir binanın tüm düzenlenebilir durumunu bu iki
  ilkel üzerine kurar, `merge()` ile iki replikayı (örn. iki kullanıcının
  yerel state'i) birleştirir.

## 3. `collab_session.py` — Gerçek zamanlı köprü

`CollaborationHub`, Faz 14'ün `extensibility.websocket_api.WebSocketRouter`'ını
`auth` + `crdt` ile birleştirir: `join()` token doğrular ve rol kontrolü
yapar, `apply_field_edit()`/`apply_add_floor()` yerel CRDT state'ini
günceller ve diğer odadaki bağlantılara `crdt_patch` mesajı broadcast eder.
`collab.join`/`collab.edit` WebSocket mesaj tipleri `WebSocketRouter`'a
kayıtlıdır — gerçek bir WS sunucusu bu handler'ları doğrudan çağırabilir.

## Kabul kriteri doğrulaması

`tests/test_faz19_collaboration.py::test_two_editors_concurrent_conflicting_edit_converges`
tam senaryoyu test eder: alice ve bob aynı binanın `height_m` alanını aynı
timestamp'te farklı değerlere değiştiriyor; hem sunucu-tarafı paylaşılan
CRDT state'i hem de iki bağımsız istemci-tarafı kopyasının (hangi sırayla
merge edilirse edilsin) **aynı** nihai değere yakınsadığı doğrulanıyor.
`test_orset_concurrent_add_and_remove_add_wins` ise "kayıpsız" garantisini
(eşzamanlı ekle/sil çakışmasında veri kaybolmuyor) kanıtlıyor.

## Dürüst sınırlama (güncellendi — Roadmap V4/R1)

- **Düzeltme:** Bu bölüm önceden "`CollaborationHub` süreç yeniden
  başladığında durumu kaybeder" diyordu — bu artık **doğru değil**.
  `CollaborationHub(auth, db=ProjectDatabase(...))` verildiğinde, her
  `apply_field_edit`/`apply_add_floor`/`apply_remove_floor` sonrası oda
  durumu `persistence.ProjectDatabase.save_object()` ile diske yazılır;
  `join()` çağrısı bellek-içi oda yoksa önce kalıcı katmandan geri
  yüklemeyi dener. Bkz. `_persist_room()`/`_load_persisted_state()` ve
  `tests/test_phaseD18_collab_persistence.py` (7 test) +
  `tests/test_phaseR1_collab_persistence_depth.py` (4 test, tüm
  `LWWRegister` alanları + tüm `ORSet` üyeleri/tombstone'ları dahil
  azami derinlikte doğrulama). `db` verilmezse eski bellek-içi davranış
  tamamen korunur (geriye uyumlu, `test_state_without_db_does_not_persist`).
- **Hâlâ geçerli sınırlama:** `AuthService` (kullanıcı hesapları/oturum
  token'ları) hâlâ yalnızca bellek-içi — bu, CRDT oda durumundan ayrı bir
  kaygıdır ve kapsam dışı bırakılmaya devam ediyor.
- Gerçek bir WebSocket sunucusuna (örn. `websockets`/`asyncio` veya
  `app_shell`'in stdlib `http.server`'ı) bağlama bu oturumda yapılmadı —
  `WebSocketRouter.dispatch()` zaten soket-agnostik olduğundan (Faz 14'te
  tasarlandığı gibi) gerçek bir sunucunun `on_message` handler'ından
  doğrudan çağrılabilir; testler sahte `WSConnection` ile ağsız çalışır.
  (Roadmap V4/E8'in konusu.)
- Kimlik doğrulama HTTP/WS taşıma katmanına (örn. `Authorization` header,
  WS handshake query param) nasıl bağlanacağı belgelenmedi — `app_shell`
  ile entegrasyon ileriki bir adım.

Testler: `../tests/test_faz19_collaboration.py` — 28 test (auth: kayıt/giriş/
parola hash/token süre-aşımı/rol hiyerarşisi; crdt: LWW/OR-Set commutative/
idempotent birleştirme + çakışma yakınsaması; collab_session: katılım/
yetkilendirme/eşzamanlı düzenleme/broadcast/ayrılma uçtan uca senaryoları).

## ROADMAP_V4 — Faz E8: Gerçek WebSocket Sunucusu ✅ TAMAMLANDI

Yukarıdaki "hâlâ geçerli sınırlama" notu artık **kapatıldı**:
`ws_server.py::CollaborationWebSocketServer`, stdlib `asyncio` üzerinden
RFC 6455 uyumlu (el sıkışma + metin/ping/pong/kapama çerçeveleme) **gerçek
bir TCP soketi** dinler. `WebSocketRouter.dispatch()`'in soket-agnostik
tasarımı (Faz 14) **hiç değişmeden** kullanılıyor — sunucu yalnızca gelen
bayt akışını `WSMessage`'a çevirip `hub.router.dispatch(connection,
message)`'e besliyor; `collab.join`/`collab.edit` handler'ları, auth/CRDT/
persistence mantığı bire bir aynı kaldı.

Üçüncü parti `websockets` paketine bağımlı **değil** — `[ws]` extra
tamamen opsiyonel kalmaya devam ediyor (roadmap ilkesiyle tutarlı,
stdlib-only çekirdek).

**Kabul kriteri (roadmap'in kendi metni):** "10+ eşzamanlı gerçek
WebSocket istemcisi aynı binayı düzenler, CRDT birleşmesi gerçek ağ
gecikmesi/sıralama karışıklığı altında da kayıpsız yakınsar." —
`tests/test_phaseE8_websocket_server.py` bunu üç senaryoyla kanıtlar:
12 gerçek istemcinin farklı alanları düzenlemesi + gerçek soketten
broadcast doğrulaması, iki istemcinin aynı alana eşzamanlı çakışan
yazımı (sunucu + her iki istemcinin bağımsız CRDT birleşmesiyle aynı
nihai değere yakınsaması), ve rol tabanlı yetkilendirmenin gerçek soket
üzerinden de bozulmadan çalışması (VIEWER `collab.edit` gönderemez).

**Bilinçli kapsam dışı bırakılanlar** (bkz. `ws_server.py` docstring'i):
permessage-deflate sıkıştırma, parçalı (fragmented) çok-çerçeveli
mesajlar (CRDT patch'leri zaten küçük tekil JSON nesneleri — parçalı bir
çerçeve gelirse sessizce yanlış birleştirmek yerine açık bir
`WebSocketProtocolError` fırlatılır).

Testler: `tests/test_phaseE8_websocket_server.py` — 4 test (12-istemci
broadcast/yakınsama, 2-istemci çakışma yakınsaması, VIEWER reddi, kabul
kriteri sayısal belge testi).

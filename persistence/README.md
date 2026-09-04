# persistence

Roadmap V2 — **Faz 16 — Persistence & Proje Yönetimi**.

## Durum: ✅ Uygulandı (çalışan kod + testler, 30 test)

## Modüller

- **`project_format.py`** — Sürümlenmiş proje dosyası formatının sözleşmesi:
  `FORMAT_VERSION`, `ProjectManifest` (isim, id, oluşturulma/güncellenme
  zamanı, açıklama, etiketler, CRS), `migrate_schema()` (adım adım n -> n+1
  şema migrasyonu, tanımsız sıçramada `MigrationError`).
- **`db_backend.py`** — `ProjectDatabase`: tek bir `.hproj` dosyasını
  (stdlib `sqlite3`) saran kalıcılık katmanı.
  - `create(path, manifest)` / `open(path, auto_migrate=True)` / `close()`
  - `save_object(key, kind, data)`, `save_many(records)` (toplu/tek
    transaction), `load_object(key)`, `delete_object(key)`
  - `list_objects(kind=None)`, `count_objects(kind=None)`
  - `iter_history(limit)` — append-only değişiklik günlüğü (`history` tablosu)
  - `update_manifest(**fields)`, `read_manifest()`
  - `vacuum()` — silinen kayıtların bıraktığı boş alanı geri kazanır
  - Bozuk/tanınmayan dosyada `ProjectFormatError`; eski sürüm dosya
    `auto_migrate=True` ile şeffaf şekilde güncel şemaya taşınır.
- **`project_manager.py`** — `ProjectManager`: birden çok projeyi tek bir
  registry (kendi küçük SQLite dosyası) üzerinden yönetir.
  - `create_project(path, name, project_id, **manifest_fields)`
  - `open_project(project_id=None, path=None)` — id ile registry'den ya da
    doğrudan yoldan açar; zaten açıksa aynı `ProjectHandle`'ı döndürür
  - `close_project(project_id)`, `list_recent(limit)`, `forget_project(id)`
  - `ProjectHandle` — açık projeyi (`db` + `manifest`) sarar,
    `mark_dirty()`/`maybe_autosave(now=None)`/`save_now()` ile harici bir
    olay döngüsüne bağlı olmayan basit oto-kaydet mekanizması sağlar.

## Tasarım İlkesi

`data_engine.cache` (Faz 10) bellek-içi `ObjectCache`/`SceneCache`'e **hiç
dokunulmadı**. Bu modül onun üstüne kalıcılık ekler: sıcak veri RAM'de
(`ObjectCache`), soğuk/kalıcı veri diskte (`ProjectDatabase`) durur. İkisini
birbirine bağlamak isteyen kod, `ObjectCache.get_or_create(key, lambda:
db.load_object(key).data)` deseniyle basitçe köprü kurabilir — bu paket bu
köprüyü zorunlu kılmaz, çünkü hangi nesnelerin önbelleğe alınacağına karar
vermek uygulamaya (viewer/editor) özgüdür.

Format: tek dosya SQLite (`.hproj`) tercih edildi — stdlib `sqlite3` dışında
bağımlılık yok, dosya taşınabilir/tek parça, `VACUUM`/transaction/index gibi
olgun bir motorun tüm garantilerinden (ACID) faydalanılıyor.

## Kabul Kriteri Notu

Roadmap'teki kabul kriteri "1GB'lık bir proje dosyası <5sn içinde
açılır/kaydedilir". CI'da her PR'da 1GB'lık gerçek veri üretip ölçmek
pahalı olduğundan, `tests/test_phase16_persistence.py::test_bulk_save_and_reopen_performance`
5.000 nesnelik bir sahneyle küçültülmüş bir regresyon testi çalıştırır
(toplu kayıt + tekrar açma, ikisi de <5sn). Gerçek 1GB ölçeğinde manuel
benchmark için: `save_many` ile büyük bir `records` listesi üretip
`time.perf_counter()` ile ölçün — SQLite'ın WAL/transaction modeliyle
performans, nesne sayısıyla yaklaşık doğrusal ölçeklenir.

## Sınırlamalar (bilinçli kapsam dışı)

- Çoklu-kullanıcı eşzamanlı yazma / kilit yönetimi kapsam dışı — bkz.
  Faz 19 (Çok Kullanıcılı İşbirliği).
- PostgreSQL/PostGIS backend'i (büyük ölçek, çok kullanıcılı senaryo için
  roadmap'te bahsedilen opsiyonel yol) henüz eklenmedi; `ProjectDatabase`
  arayüzü ileride alternatif bir backend'le değiştirilebilecek şekilde
  (yalnızca `sqlite3.Connection`'a bağımlı, dar bir yüzey) tasarlandı.

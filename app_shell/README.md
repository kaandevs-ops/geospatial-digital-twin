# app_shell (Faz 18 — ROADMAP V2) — ✅ Uygulandı

Roadmap V2 Track B, Faz 18 — **"Kullanıcı Arayüzü (Web/Masaüstü
Uygulaması)"**. Önceki fazların (özellikle Faz 15 render, Faz 16
persistence, Faz 8 editor, Faz 12 AI assistant) ürettiği yetenekleri,
kullanıcının **hiç kod yazmadan** kullanabileceği tek bir uygulama kabuğu
altında birleştirir.

## Neden bu şekilde tasarlandı

Roadmap ilkesi (stdlib-only, harici framework yok) burada da korunur:

1. **`session.py`** (Python, stdlib-only) — `AppSession`: tek bir
   kullanıcı oturumunun tüm durumunu tutar (açık projeler, her projedeki
   bina koleksiyonu, her bina için ayrı `UndoRedoStack` + AI asistan
   orkestratörü) ve yüksek seviyeli operasyonları (`create_project`,
   `add_building`, `add_floor`, `undo`/`redo`, `run_assistant_command`,
   `scene_json`, ...) framework'ten bağımsız saf Python metodları olarak
   sunar.
2. **`api.py`** — Faz 14'te tanımlanmış çerçeve-bağımsız
   `extensibility.rest_api.RestRouter` üzerine inşa edilmiş bir route seti.
   `AppSession`'ı sarar; `dispatch()` ile gerçek bir soket açmadan test
   edilebilir (`editor` fazının "headless input-event testi" ilkesiyle
   birebir aynı yaklaşım).
3. **`server.py`** — stdlib `http.server.ThreadingHTTPServer` tabanlı ince
   bir sunucu katmanı: `/api/*` isteklerini `RestRouter`'a yönlendirir,
   `web/` altındaki statik uygulama kabuğunu servis eder. Harici bir web
   framework'üne (Flask/FastAPI) bağımlılık yoktur.
4. **`web/index.html`** — Faz 15 `render_engine/viewer`'ının WebGL2 render
   çekirdeğini yeniden kullanan, tek dosyalık (kurulum gerektirmeyen) tam
   uygulama kabuğu arayüzü: proje gezgini, katman/bina paneli, editör araç
   çubuğu (kat ekle/çıkar, geri al/ileri al), AI asistan sohbet paneli ve
   gerçek zamanlı 3D görüntüleyici.

## Kullanım

```bash
python -m harita.app_shell.server --port 8765 --registry ~/.harita/registry.hprojreg
```

Sonra bir tarayıcıda `http://127.0.0.1:8765/` açılır:

1. Sol panelden yeni bir proje oluşturulur (isim + `.hproj` dosya yolu).
2. Footprint noktaları (`x,y x,y ...` biçiminde) ve bina tipi girilip
   **"+ Bina ekle"** ile Faz 3 (Building Reconstruction) prosedürel olarak
   binayı üretir; sonuç anında 3D görüntüleyicide görünür (Faz 15 köprüsü).
3. Sağ panelden kat ekle/çıkar, geri al/ileri al (Faz 8 editor + undo/redo)
   ya da AI asistan sohbet kutusuna doğal dil komutu ("2 kat ekle", "çatıyı
   kırma çatı yap") yazılabilir (Faz 12 köprüsü).
4. Proje, her nesne yazımında otomatik olarak `.hproj` dosyasına kalıcı
   yazılır (Faz 16 köprüsü); "Proje Gezgini"nden daha sonra tekrar açılabilir.

Doğrudan Python içinden de (UI olmadan) kullanılabilir:

```python
from harita.app_shell import AppSession

with AppSession("/tmp/registry.hprojreg") as session:
    info = session.create_project("Kızılay", "/tmp/kizilay.hproj")
    building = session.add_building(
        info["project_id"],
        polygon_points=[(0, 0), (20, 0), (20, 15), (0, 15)],
        building_type="apartman",
        floor_count=5,
        height_m=15.0,
    )
    session.run_assistant_command(info["project_id"], building["key"], "2 kat ekle")
    scene = session.scene_json(info["project_id"])  # render_engine.Scene JSON'u
```

## Kabul kriteri karşılanma durumu

> *"Kullanıcı hiç kod yazmadan: harita verisi yükler → 3D binayı görür →
> düzenler → export eder."*

- ✅ **Bina üretme → görme → düzenleme**: web arayüzü üzerinden footprint
  girilip bina üretilir, 3D görüntüleyicide anında görünür, editör araç
  çubuğu ve AI asistan ile düzenlenebilir — hepsi tarayıcıdan, kod yazmadan.
- 🔶 **"Harita verisi yükler" (GIS dosyasından footprint)**: şu an bina
  eklemek için footprint doğrudan (nokta listesi) girilir; Faz 1
  `core_engine.gis_core` parser'larından (Shapefile/KML/GeoJSON) doğrudan
  dosya yükleyip footprint çıkarma UI'da henüz **yok** — `FootprintParser`
  zaten var ve `AppSession.add_building` aynı `Footprint` sözleşmesini
  kullanıyor, UI'a "dosya yükle" alanı eklemek doğrudan bir sonraki adım.
- 🔶 **"Export eder"**: `export/` modülü (OBJ/STL/PLY/glTF/DXF) zaten var
  ama UI'dan tetiklenen bir "Dışa Aktar" düğmesi henüz eklenmedi (kalan iş).

## Kapsam ve sınırlamalar (bilinçli, belgelenmiş)

- **Tek-tenant sunucu**: `server.py` tek bir `AppSession`/registry üzerinde
  çalışır; çoklu kullanıcı, kimlik doğrulama, yetkilendirme kapsam dışıdır
  (bkz. ROADMAP_V2 Faz 19).
- **Bina disk temsili minimaldir**: `AppSession`, her binayı diskte
  footprint + parametre (tip/kat/yükseklik/seed) olarak saklar, tam
  `Building` nesnesini değil — açılışta prosedürel üretim deterministik
  olarak tekrar çalıştırılır. Bu, `persistence` katmanını basit tutar ama
  kullanıcının üretimden *sonra* elle yaptığı serbest-form düzenlemeler
  (örn. tekil oda taşıma) şu an yalnızca oturum içi `UndoRedoStack`'te
  yaşar; kalıcı olarak "diff" şeklinde saklanmaz (bkz. Faz 8/16 birleşim,
  kalan iş).
- **Terrain/road/object editor'ları henüz UI'a bağlanmadı**: yalnızca
  `BuildingEditor` (kat ekle/çıkar) sarılı; `TerrainEditor`/`RoadEditor`/
  `ObjectEditor` için ayrı panel/route eklemek doğrudan bir sonraki
  genişleme (aynı `AppSession`/`RestRouter` deseniyle).
- **Kabuk gerçek bir masaüstü uygulaması değildir**: Faz 15 ile aynı
  yaklaşım — tarayıcı zaten her makinede var, ekstra kurulum yok; gerçek
  bir Electron/Tauri paketleme kapsam dışı bırakıldı.
- **Gerçek tarayıcı E2E testi otomatik CI'da yok**: `tests/
  test_phase18_app_shell.py`, `AppSession`/`RestRouter`'ı headless test
  eder + gerçek bir soket üzerinden statik dosya + `/api/health`
  servisini doğrular; tam bir tarayıcı (Selenium/Playwright) E2E testi
  roadmap'in diğer UI-bağımlı fazlarıyla (bkz. Faz 20 CI/CD) aynı gerekçeyle
  kapsam dışı bırakıldı.

## Güvenlik notu — proje `path` alanı kısıtlanmamış (Faz 21 denetimi)

`POST /api/projects` ve `POST /api/projects/<id>/open`, istemcinin
gönderdiği `path` alanını doğrudan `ProjectManager.create_project`/
`open_project`'e iletir — sunucu tarafında hiçbir izin-verilen-kök
(allow-listed root) kısıtlaması yoktur. Bu, **tek-tenant masaüstü
uygulaması modeli için bilinçli bir tasarımdır**: `AppSession` tek bir
yerel kullanıcının kendi sürecidir, tıpkı bir masaüstü uygulamasının
(Blender, VSCode) kullanıcının diskindeki herhangi bir dosyayı
açabilmesi gibi.

**Bu, sunucu `collaboration/` (Faz 19) ile birleştirilip paylaşımlı/
hosted çok-kullanıcılı bir moda taşınırsa geçerliliğini yitirir** —
o noktada `path` istemci tarafından belirlenen bir değer olarak
kalmaya devam ederse, bir kullanıcı diğer bir kullanıcının/sunucu
sürecinin erişebildiği herhangi bir dosyayı proje olarak açmayı
deneyebilir. Hosted bir dağıtımdan önce yapılması gerekenler:

1. `path`'i, sunucu tarafında yapılandırılmış bir kök dizinle
   (örn. `~/.harita/projects/`) sınırlamak (`Path.resolve()` +
   `relative_to()` — `server.py._serve_static()`'te zaten kullanılan
   aynı kalıp).
2. `collaboration.auth.AuthService`'in proje-bazlı VIEWER/EDITOR/OWNER
   rol kontrolünü proje aç/oluştur akışına bağlamak.

## İçerik

| Dosya | Sorumluluk |
|---|---|
| `session.py` | `AppSession`/`AppSessionError` — proje/bina durumu, Faz 8/12/15/16 köprüleri |
| `api.py` | `build_app_router()` — `RestRouter` üzerine `AppSession` route seti |
| `server.py` | `make_server()`/`main()` — stdlib `http.server` katmanı |
| `web/index.html` | Tam uygulama kabuğu arayüzü (proje gezgini, katman paneli, editör, AI sohbet, WebGL2 viewer) |

Testler: [`../tests/test_phase18_app_shell.py`](../tests/test_phase18_app_shell.py)
(18 test — `AppSession` uçtan uca akışı, `RestRouter.dispatch()` HTTP-benzeri
sözleşmesi, gerçek soket üzerinden statik dosya + health-check doğrulaması).

## ROADMAP_V4 — Faz E9 (Terrain/Road editör panellerinin tam JS entegrasyonu)

D16'da "arazi/yol katman paneli" bilinçli olarak yalnızca bir UI iskeleti
(`terrain-layer-note`: *"bu panel arayüz iskeletidir"*) olarak bırakılmıştı.
Bir önceki oturumda `AppSession.terrain_init/terrain_state/terrain_brush/
terrain_undo/terrain_redo` ve `AppSession.road_add/road_add_point/
road_move_point/...` metodları + bunlara karşılık gelen
`app_shell/api.py` REST uçları (`POST .../terrain/*`, `POST .../roads/*`)
zaten eklenmiş ve `tests/test_phaseE9_terrain_road_editor_integration.py`
(16 test — Python API + REST dispatch + kalıcılık) ile doğrulanmıştı;
`AppSession.scene_json()` de arazi/yol mesh'lerini sahneye katıyordu.
**Ancak `app_shell/web/index.html` hâlâ eski iskelet notunu gösteriyordu ve
hiçbir gerçek mouse etkileşimi bu uçlara bağlı değildi** — D5'in
`GizmoInputSession` deseni burada henüz tekrarlanmamıştı. Bu, Faz E9'un
kendi kabul kriterinin ("kullanıcı hiç kod yazmadan ... düzenler (bina VE
arazi VE yol)") fiilen karşılanmadığı anlamına geliyordu.

Bu oturumda kapatıldı:

- **`editor-tool` seçici** (kamera / arazi-yükselt / arazi-alçalt /
  arazi-düzleştir / yol-nokta-ekle / yol-nokta-taşı) + fırça yarıçapı/
  miktarı ve yol genişliği kontrolleri eklendi.
- **`screenToGroundPoint(clientX, clientY)`** — `M4.multiply`/`M4.invert`/
  `M4.transformPoint4` (yeni eklenen genel 4×4 matris tersi) ile ekran
  koordinatını gerçek `proj*view` matrisinden ters izdüşümleyip `y=0`
  zemin düzlemiyle kesiştiren tam bir ray-cast; kameranın arkasına düşen
  veya zemine paralel ray'ler güvenli biçimde `null` döner.
- **`handleEditorAction()`** — seçili araca göre `mousedown`/sürükleyerek
  `mousemove` olaylarını `terrain/brush`, `roads/{id}/points` (POST) ve
  `roads/{id}/points/{index}` (PUT) REST çağrılarına yönlendirir; araç
  `orbit` iken önceki kamera-döndürme/kaydırma davranışı **değişmeden**
  korunur (geriye uyumlu — mevcut `test_phase18_app_shell.py` etkilenmez).
  Sürükleme sırasında fırça/nokta-taşıma çağrıları 90ms throttle edilir
  (gereksiz REST taşkınını önlemek için).
- Eski `terrain-layer-note` iskelet metni kaldırıldı; panel artık gerçek
  davranışını açıklıyor.

**Kabul kriteri artık tamamen karşılanıyor:** kullanıcı arayüzden proje
açar → bina ekler/düzenler (Faz 8) → araç seçip arazi fırçalar veya yol
noktası ekler/taşır (gerçek mouse event'i → gerçek REST çağrısı →
`refreshScene()` ile anlık güncellenen mesh) → dışa aktarır (Faz D16) —
hiçbir adımda kod yazmaz.

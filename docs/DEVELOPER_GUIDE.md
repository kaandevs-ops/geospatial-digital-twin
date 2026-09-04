# Geliştirici Kılavuzu

> Roadmap Faz 5.6 ("Geliştirici kılavuzu — mimari, faz yapısı, katkı
> süreci") kapsamında oluşturuldu.

## 1. Temel ilke: stdlib-only çekirdek

`harita/` paketi kasıtlı olarak **stdlib-only**'dir. Opsiyonel özellikler
(`ml`, `geo`, ...) yüklü değilse kod heuristic/dahili bir tabloya sessizce
düşer — hiçbir zaman `ImportError` ile patlamaz. Yeni bir üçüncü parti
bağımlılık eklerken:

1. Onu `pyproject.toml`'da yeni/var olan bir `[project.optional-dependencies]`
   grubuna ekleyin (asla `dependencies = []`'e eklemeyin).
2. İçe aktarmayı `try/except ImportError` ile sarın, fallback davranışı
   tanımlayın.
3. Hem "paket var" hem "paket yok" senaryosunu test edin.

```bash
pip install -e .            # çekirdek
pip install -e ".[ml,geo]"  # + opsiyonel ML/CRS
pip install -e ".[dev]"     # + test/lint araçları
```

## 2. Mimari — modül haritası

Kod, sorumluluk alanına göre üst düzey paketlere ayrılmıştır (her birinde
bir `README.md` vardır — önce onu okuyun):

| Katman | Paketler |
|---|---|
| Coğrafi/temel motor | `core_engine/` (koordinat sistemleri, tile motoru, GIS çekirdeği, OSM istemcisi) |
| Geometri üretimi | `building_reconstruction/` (footprint → çatı/duvar/pencere/oda), `mesh_engine/`, `terrain_engine/` |
| Malzeme/görsel | `material_engine/`, `render_engine/`, `lighting/`, `visualization/` |
| Analiz | `analysis_engine/` (güneş, görünürlük, ölçüm, çevresel sim), `hazard_data/` (deprem/AFAD/USGS), `physics/` |
| Yapay zeka | `ai_reconstruction/` (yükseklik/malzeme/oda tahmin modelleri), `ai_assistant/` (doğal dil orkestrasyonu, LLM sağlayıcıları) |
| Mobilite | `mobility/` (yol bulma, trafik, kalabalık, toplu taşıma, iç mekan navigasyon) |
| Bitki örtüsü | `vegetation/` |
| Kalıcılık/işbirliği | `persistence/` (SQLite + Postgres/PostGIS backend), `collaboration/` (auth, CRDT, websocket) |
| Uygulama kabuğu | `app_shell/` (REST API, oturum, web arayüzü — `app_shell/web/index.html`) |
| Genişletilebilirlik | `extensibility/` (plugin sistemi, REST router, script API, sandbox) |
| Operasyon | `security/`, `observability/`, `performance/`, `deploy/` |
| Dijital ikiz | `digital_twin/` (IoT köprüsü, hiyerarşi) |
| Editör | `editor/` (arazi, yol, obje düzenleyicileri, gizmo, komut sistemi) |
| Çeviri | `i18n/` |
| Dışa aktarma | `export/` (CityGML, CityJSON, IFC, 3D Tiles, raporlar) |

`archive/legacy_prototype_v1/` yalnızca tarihsel referanstır — yeni kod bu
klasöre yazılmaz.

## 3. Faz yapısı ve roadmap disiplini

- **Güncel roadmap**: kök dizindeki `yeni_roadmap.md`. `ROADMAP.md`,
  `ROADMAP_V2/V3/V4.md` tarihsel kayıtlardır (her biri başında bir arşiv
  notuyla `yeni_roadmap.md`'ye yönlendirir) — yeni planlama oraya değil,
  `yeni_roadmap.md`'ye eklenir.
- Kod içi yorumlar genelde `# Roadmap Faz X.Y — "..."` biçiminde, hangi
  roadmap maddesinin karşılığı olduğunu belirtir. Yeni bir roadmap
  maddesini uygularken aynı geleneği sürdürün — bu, `scripts/verify_roadmap_sync.py`
  gibi araçların ve gelecekteki denetimlerin kod↔plan eşlemesini
  otomatik doğrulayabilmesini sağlar.
- Her faz kendi testiyle gelir; **önceki hiçbir test kırılmamalıdır**
  (regresyon ağı büyüktür — `tests/` altında 90+ dosya).

### Roadmap senkron denetimi

```bash
python3 scripts/verify_roadmap_sync.py               # tam kontrol (pytest çalıştırır)
python3 scripts/verify_roadmap_sync.py --skip-pytest  # yalnızca dosya varlığı
```

## 4. Test çalıştırma

```bash
pip install -e ".[dev]"
pytest tests/ -q
```

Testler `tests/test_phase<X>_*.py` ve `tests/test_faz<X>_*.py` adlandırma
kurallarını izler — hangi fazın hangi test dosyasıyla doğrulandığını
bulmak için dosya adındaki faz etiketine bakın. Bazı testler gerçek ağ
erişimi gerektirir (OSM Overpass, AFAD/USGS) — CI'da rate-limit'e dikkat
edin, yerelde `--skip-pytest` veya ilgili marker ile atlayabilirsiniz
(işaretleyici adı için `tests/` içindeki `pytest.mark` kullanımlarına
bakın).

### Görsel/yapısal regresyon testi

```bash
python3 scripts/visual_regression.py                  # baseline ile karşılaştır
python3 scripts/visual_regression.py --update-baseline # baseline'ı bilinçli güncelle
pytest tests/test_visual_regression_baseline.py -q     # aynı kontrolü pytest'ten çalıştırır
```

Proje stdlib-only olduğu ve gerçek bir GPU render pipeline'ı olmadığı için
(çizim `viewer/index.html` WebGL2 renderer'ına ait) piksel karşılaştırması
yapılamıyor — bunun yerine sabit bir demo bina setinin (5 farklı footprint
tipi) vertex/üçgen sayısı, bounding box, watertight/manifold durumu bir
baseline'a (`tests/fixtures/visual_regression_baseline.json`) karşı
karşılaştırılıyor. Geometri üretim kodunda (roof/facade/procedural_generator
vb.) yaptığınız bir değişiklik bu testi kırıyorsa: ya regresyon var demektir
(düzeltin), ya da kasıtlı bir geometri değişikliğidir — bu durumda
`--update-baseline` ile baseline'ı bilinçli olarak güncelleyip commit'e
dahil edin.

## 5. Uygulama kabuğu (`app_shell`) — REST API'ye yeni endpoint ekleme

1. `app_shell/api.py` içinde `build_app_router()` fonksiyonuna
   `@router.<metod>("/api/...")` ile yeni route ekleyin.
2. Hata durumlarını `AppSessionError` fırlatarak işleyin — `_err` bunu
   otomatik uygun HTTP koduna çevirir.
3. `docs/API.md`'ye ilgili tabloya bir satır ekleyin (bkz. o dosyanın
   sonundaki doğrulama komutu).
4. Arayüzden çağıracaksanız `app_shell/web/index.html` içinde `api(...)`
   yardımcı fonksiyonunu kullanın (mevcut çağrılara bakın, örn.
   `add-building-btn` handler'ı).

## 6. Ön yüz (`app_shell/web/index.html`)

Tek dosyalık, framework'süz bir arayüzdür (vanilla JS + CSS değişkenleri
ile tema desteği). Yeni bir panel/buton eklerken:

- Tema değişkenlerini (`--bg-0`, `--ink-0`, `--accent`, ...) kullanın,
  sabit renk yazmayın (karanlık/aydınlık tema otomatik çalışsın diye).
- Modal eklerken var olan `#osm-modal-overlay` / `#shortcuts-modal-overlay`
  / `#tour-modal-overlay` desenini izleyin (overlay + `.open` sınıfı).
- **`localStorage`/`sessionStorage` kasıtlı olarak kullanılmıyor** (bu
  sunucu-bağlı bir uygulama kabuğu) — kalıcı kullanıcı tercihi
  gerekiyorsa `app_shell` tarafında bir kullanıcı ayarı olarak modelleyin.
- Yeni bir klavye kısayolu eklerseniz hem `keydown` switch'ine hem
  `#shortcuts-modal` tablosuna ekleyin.

## 7. Güvenlik notları

- API key'ler asla client'a düz metin dönmemeli (`GET /api/ai/config`
  maskeli döner — bu davranışı bozmayın).
- Harici API çağrıları (Overpass, AI sağlayıcılar) `security/rate_limiter.py`
  üzerinden sınırlanır — yeni bir harici çağrı eklerken bunu atlamayın.
- Kullanıcı girdisi (GeoJSON import, dosya yükleme) doğrulanmadan
  işlenmemelidir.

## 8. Katkı süreci (özet)

1. İlgili roadmap maddesini `yeni_roadmap.md`'de bulun (yoksa önce oraya
   ekleyin — plansız kod yazılmaz).
2. Kodu + testi birlikte yazın, mevcut testleri kırmadığınızı doğrulayın.
3. Yeni bir REST endpoint/UI elemanı eklediyseniz `docs/API.md` ve/veya
   `docs/USER_GUIDE.md`'yi güncelleyin.
4. Kod içi yorumla hangi roadmap maddesinin karşılandığını belirtin.

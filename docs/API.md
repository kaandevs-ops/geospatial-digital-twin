# API Dokümantasyonu — `app_shell`

> Roadmap Faz 5.6 ("API dokümantasyonu — `app_shell/api.py` endpoint'leri
> için") kapsamında oluşturuldu. Bu doküman `app_shell/api.py` içindeki
> `build_app_router()` fonksiyonunun kaydettiği tüm route'ları listeler.
> Kaynak kod her zaman öncelikli gerçektir; bu dosya elle senkron
> tutulmalıdır (aşağı bkz. "Bu dokümanı güncel tutmak").

## Genel bilgiler

- Sunucu: `python -m harita.app_shell.server --port 8765` ile başlar.
- Tüm route'lar `extensibility.rest_api.RestRouter` üzerinden, `/api`
  önekiyle tanımlanır (framework'süz, stdlib-only bir HTTP-benzeri katman).
- İstek/yanıt gövdeleri JSON'dur. Başarısız çağrılar `AppSessionError`
  yakalanarak (`_err`) uygun HTTP durum koduna çevrilir.
- `<id>` bir proje kimliği, `<key>` bir bina anahtarı, `<road_id>` bir yol
  kimliğidir — hepsi path parametresidir.
- Kimlik doğrulama: `POST /api/auth/register` ve `/login` bir oturum
  token'ı döner; rol tabanlı yetki (`viewer`/`editor`/`owner`) proje
  bazlıdır (bkz. `collaboration/auth.py`). Bu doküman her endpoint için
  gereken minimum rolü ayrı belirtmez — üretim entegrasyonu öncesi
  `collaboration/auth.py::AuthManager.require_role` çağrılarını kontrol
  edin.

## Sağlık / gözlemlenebilirlik

| Metod | Yol | Açıklama |
|---|---|---|
| GET | `/api/health` | Basit canlılık kontrolü (docker healthcheck bunu kullanır). |
| GET | `/api/metrics` | `observability/metrics.py` çıktısı — opsiyonel, derleme/flag'e bağlı. |
| POST | `/api/performance/gpu-timing` | İstemciden (viewer) GPU zamanlama verisi raporlar. |
| GET | `/api/performance/gpu-timing` | Son raporlanan GPU zamanlama verisini döner. |

## Proje yönetimi

| Metod | Yol | Açıklama |
|---|---|---|
| GET | `/api/projects` | Kayıtlı tüm projeleri listeler (proje kaydı/registry). |
| POST | `/api/projects` | Yeni proje oluşturur. Gövde: `{name, path}`. |
| POST | `/api/projects/<id>/open` | Var olan bir `.hproj` dosyasını açar/yükler. |
| POST | `/api/projects/<id>/close` | Projeyi bellekten kapatır (kaydetmeden). |
| POST | `/api/projects/<id>/save` | Projeyi diske kaydeder. |
| GET | `/api/projects/<id>/history` | Proje versiyon/undo-redo geçmişi. |
| POST | `/api/projects/<id>/import` | Genel içe aktarma (GeoJSON vb. — bkz. gövde şeması `app_shell/api.py`). |
| POST | `/api/projects/<id>/osm/import` | Bir bbox için gerçek zamanlı OSM Overpass sorgusu + footprint içe aktarma. ODbL atfı yanıt gövdesinde döner. |
| POST | `/api/projects/<id>/export` | CityGML/CityJSON/IFC/3D Tiles gibi formatlara dışa aktarma (bkz. `export/`). |

## Binalar ve kat düzenleme

| Metod | Yol | Açıklama |
|---|---|---|
| GET | `/api/projects/<id>/buildings` | Projedeki tüm binaları listeler. |
| POST | `/api/projects/<id>/buildings` | Footprint + bina tipinden yeni bina üretir (`ProceduralBuildingGenerator`). |
| DELETE | `/api/projects/<id>/buildings/<key>` | Binayı siler. |
| POST | `/api/projects/<id>/buildings/<key>/add_floor` | Bir kat ekler. |
| POST | `/api/projects/<id>/buildings/<key>/remove_floor` | Bir kat çıkarır. |
| POST | `/api/projects/<id>/buildings/<key>/undo` | Bina üzerindeki son değişikliği geri alır. |
| POST | `/api/projects/<id>/buildings/<key>/redo` | Geri alınan değişikliği yineler. |
| GET | `/api/projects/<id>/buildings/<key>/structural-validate` | Basit yapısal tutarlılık kontrolü (`building_reconstruction/structural_validation.py`). |
| GET | `/api/projects/<id>/facade-compliance/narrate` | WWR/yönetmelik uyum sonucunu doğal dilde özetler. |

## AI asistan ve üretim

| Metod | Yol | Açıklama |
|---|---|---|
| POST | `/api/projects/<id>/buildings/<key>/assistant` | Doğal dil komutu (Türkçe) binaya uygular (`ai_assistant/orchestrator.py`). |
| POST | `/api/projects/<id>/buildings/<key>/ai/interior` | AI ile iç mekan planı alternatifleri üretir. |
| POST | `/api/projects/<id>/buildings/<key>/ai/environment` | AI ile çevre/bahçe/peyzaj üretir. |
| GET | `/api/ai/config` | Aktif AI sağlayıcı yapılandırmasını döner (key maskeli). |
| POST | `/api/ai/config` | Sağlayıcı seçimi (GGUF/OpenAI-uyumlu/Anthropic) ve ayarları kaydeder. |
| POST | `/api/ai/config/clear` | AI yapılandırmasını temizler. |
| POST | `/api/ai/test-connection` | Seçili sağlayıcıya gerçek bir bağlantı testi yapar. |

## Görselleştirme ve analiz

| Metod | Yol | Açıklama |
|---|---|---|
| GET | `/api/projects/<id>/scene` | Viewer için tam sahne (mesh/kamera) verisini döner. |
| GET | `/api/projects/<id>/section` | Kesit düzlemi (X/Y/Z) uygulanmış sahne. |
| GET | `/api/projects/<id>/explosion` | Patlatma görünümü (katları ayırarak) sahnesi. |
| POST | `/api/projects/<id>/measure` | Mesafe/alan/açı ölçümü yapar. |
| POST | `/api/projects/<id>/analysis/sun` | Güneş/gölge analizi (lat/lon/tilt/azimut girdisiyle). |
| POST | `/api/projects/<id>/analysis/visibility` | Görünürlük analizi (heatmap verisiyle). |
| POST | `/api/projects/<id>/physics/tower-test` | Basit fiziksel devrilme/kararlılık testi. |

## Bitki örtüsü ve mobilite

| Metod | Yol | Açıklama |
|---|---|---|
| POST | `/api/projects/<id>/vegetation/scatter` | Prosedürel bitki/ağaç dağılımı üretir. |
| POST | `/api/projects/<id>/vegetation/clear` | Bitki örtüsünü temizler. |
| POST | `/api/projects/<id>/mobility/path` | Yaya/araç rota hesabı (pathfinding). |

## Arazi ve yol editörü

| Metod | Yol | Açıklama |
|---|---|---|
| POST | `/api/projects/<id>/terrain/init` | Arazi ızgarasını başlatır. |
| GET | `/api/projects/<id>/terrain` | Mevcut arazi verisini döner. |
| POST | `/api/projects/<id>/terrain/brush` | Fırça ile arazi yüksekliği değiştirir. |
| POST | `/api/projects/<id>/terrain/undo` | Arazi değişikliğini geri alır. |
| POST | `/api/projects/<id>/terrain/redo` | Geri alınan arazi değişikliğini yineler. |
| GET | `/api/projects/<id>/roads` | Yolları listeler. |
| POST | `/api/projects/<id>/roads` | Yeni yol oluşturur. |
| DELETE | `/api/projects/<id>/roads/<road_id>` | Yolu siler. |
| POST | `/api/projects/<id>/roads/<road_id>/points` | Yola nokta ekler. |
| PUT | `/api/projects/<id>/roads/<road_id>/points/<index>` | Bir yol noktasını günceller. |
| DELETE | `/api/projects/<id>/roads/<road_id>/points/<index>` | Bir yol noktasını siler. |
| POST | `/api/projects/<id>/roads/<road_id>/width` | Yol genişliğini değiştirir. |
| POST | `/api/projects/<id>/roads/<road_id>/undo` | Yol değişikliğini geri alır. |
| POST | `/api/projects/<id>/roads/<road_id>/redo` | Geri alınan yol değişikliğini yineler. |

## Feature Survey (saha ölçümü)

`feature_survey/` paketinin (PENZD nokta içe aktarma, kapama/hata raporları,
WebODM fotogrametri köprüsü, S3+S4+S5 uçtan uca orkestrasyon) `app_shell`
üzerinden dışarı açılan uçları. Ayrıntılı akış için
[`USER_GUIDE.md#feature-survey-saha-ölçümü`](USER_GUIDE.md) bölümüne bakın.

| Metod | Yol | Açıklama |
|---|---|---|
| POST | `/api/projects/<id>/feature-survey/uploads` | Multipart/form-data dosya yükleme (alan adı serbest, her parça bir dosya) — ham HTTP gövdesi olarak işlenir, JSON router'ın dışındadır (bkz. `app_shell/server.py::_dispatch_upload`, `app_shell/multipart.py`). Tipik kullanım: RTK/total station ham dosyaları, fotogrametri görüntü seti. |
| POST | `/api/projects/<id>/feature-survey/import` | PENZD (Point/Elevation/Northing/Easting/Description) formatlı CSV metnini içe aktarır. Gövde: `{csv_text, has_header?, instrument?}` — `instrument` ∈ ör. `"total_station"`, `"gnss_rtk"` (bkz. `feature_survey/codes.py`). |
| GET | `/api/projects/<id>/feature-survey/summary` | İçe aktarılan saha noktalarının özetini (nokta sayısı, kod dağılımı, kapsama alanı) döner. |
| GET | `/api/projects/<id>/feature-survey/geojson` | Saha noktalarını GeoJSON olarak döner. Sorgu parametreleri: `origin_lat`, `origin_lon`, `origin_elevation` (yerel saha koordinatlarını coğrafi koordinatlara bağlamak için opsiyonel orijin). |
| POST | `/api/feature-survey/webodm/test-connection` | Bir WebODM sunucusuna gerçek bir bağlantı testi yapar. Gövde: `{base_url, token?}`. Proje-bağımsızdır (kimlik doğrulama, kurulum ekranında kullanılır). |
| POST | `/api/projects/<id>/feature-survey/webodm/submit` | Bir görüntü klasörünü WebODM'e fotogrametri işi olarak gönderir. Gövde: `{base_url, image_dir, token?, task_name?}`. |
| GET | `/api/projects/<id>/feature-survey/webodm/status` | Gönderilen WebODM işinin durumunu sorgular. |
| POST | `/api/projects/<id>/feature-survey/webodm/fetch` | Tamamlanan WebODM çıktısını (nokta bulutu/ortofoto) indirir. Gövde: `{output_dir}`. |
| POST | `/api/projects/<id>/feature-survey/orchestrate` | ROADMAP_V6 Faz S6: S3 (kapama raporu) + S4 (kontrol noktası karşılaştırması) + S5 (nokta bulutu-mesh karşılaştırması) adımlarını tek çağrıda çalıştırıp kalıcı kaydeder. Gövde alanları (hepsi opsiyonel): `checkpoint_comparisons`, `checkpoint_tolerance_horizontal_m`, `checkpoint_tolerance_vertical_m`, `checkpoint_standard_reference`, `angular_closure`, `linear_closure`, `angular_tolerance_gon`, `max_relative_precision`, `closure_standard_reference`, `pointcloud_points`, `persist` (varsayılan `true`). |
| GET | `/api/projects/<id>/feature-survey/orchestrate` | Son kalıcı kaydedilmiş orkestrasyon sonucunu döner (yeniden çalıştırmadan). |

## Kimlik doğrulama ve işbirliği

| Metod | Yol | Açıklama |
|---|---|---|
| POST | `/api/auth/register` | Yeni kullanıcı kaydı. |
| POST | `/api/auth/login` | Giriş yapar, oturum döner. |
| POST | `/api/auth/logout` | Oturumu kapatır. |
| GET | `/api/auth/whoami` | Aktif oturumun kullanıcı bilgisini döner. |
| GET | `/api/projects/<id>/members` | Projeye rolü olan kullanıcıları listeler. |
| POST | `/api/projects/<id>/members` | Bir kullanıcıya proje bazlı rol atar (`viewer`/`editor`/`owner`). |

## Bu dokümanı güncel tutmak

`app_shell/api.py` içine yeni bir `@router.<metod>("/api/...")` eklerken bu
dosyaya da ilgili tabloya bir satır eklemek gerekir. Hızlı bir doğrulama
için (endpoint sayısı eşleşiyor mu):

```bash
grep -c '@router\.' harita/app_shell/api.py
grep -c '^| [A-Z]* | `/api' harita/docs/API.md
```

**Bilinen sapma notu:** bu bölümün ilk yazıldığı oturumda (Faz 5.6) bu iki
sayı 59/59 olarak eşleşiyordu. Feature Survey bölümünün eklendiği bu
oturumda yapılan denetimde gerçek sayı **100** `@router.` kaydına çıktı
(bu dosyadaki tablo sayısı da 59'dan 62'ye çıkarıldı — yukarıdaki Feature
Survey satırları dahil). Aradaki fark, offline harita/tile önbelleği
(`/api/offline/...`), gerçek zamanlı işbirliği (`/api/.../collab/...`),
IoT köprüsü (`/api/.../iot/...`), enerji analizi (`/api/.../energy/...`),
afet/deprem (`/api/.../hazard/...`), OSM önizleme/katman importu
(`/api/.../osm/preview`, `osm/import-layers`, `osm/category-summary`) ve
yönetmelik ön-kontrolü (`/api/regulatory/...`) gibi kategorilerin zamanla
eklenip bu dosyaya hiç işlenmemiş olmasından kaynaklanıyor. Bunların
hiçbiri bu oturumun kapsamına (Feature Survey dokümantasyonu) girmiyordu,
bu yüzden buraya eklenmedi — ama "59/59 doğrulandı" ifadesi artık yanlış
olduğundan kaldırıldı. Bu kategorilerin tam dokümantasyonu ayrı bir görev
olarak kalıyor.

# Veri Seti Rehberi — Sıfırdan Nasıl Oluşturulur / Bulunur

Bu doküman, `training/` modülünün beklediği **manifest** formatını ve
gerçek (veya sentetik-ama-tutarlı) bir görüntü+yükseklik veri setini
nasıl oluşturacağını/bulacağını anlatır.

## 1. Manifest formatı

CSV (`.csv`) veya JSON Lines (`.jsonl`) — her satır bir bina örneği:

```csv
image_path,height_m,roof_type,building_type
tiles/000001.jpg,14.5,gable,house
tiles/000002.jpg,32.0,flat,office
tiles/000003.jpg,,,warehouse
```

- `image_path`: `images_root`'a göre **göreli** yol (zorunlu).
- `height_m`: gerçek bina yüksekliği, metre (opsiyonel — boşsa o satır
  yalnızca çatı-tipi/diğer görevlerde kullanılır, yükseklik kaybına dahil
  edilmez).
- `roof_type`: `config.roof_types` listesinden biri (opsiyonel).
- `building_type`: serbest metin, ekstra bir özellik olarak taşınır.

`image_path`'in gösterdiği görüntü, **binayı ortalayan, sabit boyutlu bir
kırpma** olmalı (uydu/hava fotoğrafından veya ortofotodan). Kare kırpma +
binanın taban izini (footprint) biraz taşacak şekilde (~%20 pay) almak
pratikte iyi sonuç verir.

## 2. Veri nereden gelir — 3 gerçekçi yol

### A) Açık coğrafi veri setlerini birleştirmek (önerilen, en hızlı)

Etiket (yükseklik) ve görüntüyü **ayrı kaynaklardan** alıp eşleştirirsin:

1. **Bina ayak izleri (footprint)**: OpenStreetMap (`building=*` etiketi,
   Overpass API ile indirilebilir) veya Microsoft/Google'ın açık bina
   ayak izi veri setleri (ör. "Microsoft Building Footprints",
   "Google Open Buildings").
2. **Yükseklik etiketi**:
   - OSM'de bazı binalarda zaten `building:levels` (kat sayısı) veya
     `height` etiketi bulunur — bunu `height_m = levels * 3.0` gibi kaba
     bir varsayımla ya da doğrudan kullan. **Zayıf ama gerçek** bir
     etiket kaynağıdır; kapsama oranı düşüktür (çoğu binada yoktur).
   - Bazı ülke/şehirlerin açık **LIDAR/DSM** (dijital yüzey modeli) verisi
     vardır (çoğunlukla ulusal harita/kadastro kurumlarının açık veri
     portallarında). DSM - DTM (zemin modeli) farkı, bina yüksekliğinin
     iyi bir yaklaşık değerini verir. Bu, en güvenilir gerçek etiket
     kaynağıdır ama her bölge için mevcut değildir.
3. **Görüntü**: aynı koordinatlar için bir ortofoto/uydu görüntü servisi
   (WMTS/WMS — projenin `core_engine/tile_sources` modülü bu kısmı zaten
   destekliyor, bkz. aşağıdaki "Projenin kendi tile indirici ile toplama"
   bölümü) veya ticari/açık uydu görüntü sağlayıcıları.
4. Üçünü bina koordinatı üzerinden eşleştirip yukarıdaki manifest formatına
   dök.

**Lisans uyarısı**: her veri kaynağının kullanım koşullarını kontrol et —
bazı ortofoto servisleri yalnızca görüntüleme amaçlı, model eğitimi için
yeniden dağıtım/kullanım kısıtlı olabilir.

### B) Kendi verini oluşturmak (manuel/yarı-otomatik etiketleme)

Eğer bölgene özel, küçük ama yüksek kaliteli bir veri seti istiyorsan:

1. QGIS (veya projenin kendi `editor/` modülü) ile ilgi alanındaki
   binaların taban izini çiz/indir.
2. Her bina için sahada ölçüm, kat sayısından tahmin (`kat_sayısı * 3m`),
   ya da bilinen bir referans (belediye kayıtları, emlak ilanları vb.)
   ile yükseklik gir.
3. Aynı binanın ortofoto/uydu kırpmasını `core_engine.tile_sources` ile
   indir (bkz. aşağıda) ve manifest'e ekle.

Bu yöntem yavaştır ama **en güvenilir gerçek etiket** kaynağıdır; küçük
(birkaç yüz örnek) ama temiz bir "altın standart" test seti oluşturmak
için idealdir — büyük ama gürültülü bir eğitim setini bununla doğrulayabilirsin.

### C) Sentetik veri (hızlı prototipleme, gerçek performans göstermez)

`ai_reconstruction/height_model.py::generate_synthetic_training_set()`
zaten stdlib-only sentetik footprint verisi üretiyor — ama bu **görüntü
içermez**, yalnızca sayısal özellik üretir. Görüntü-tabanlı bir modeli
uçtan uca test etmek (kodun çalıştığını doğrulamak, gerçek doğruluk değil)
için basit 3D render'lardan (ör. Blender/three.js ile kutu-bina render'ı +
bilinen yükseklik) sentetik bir görüntü seti üretebilirsin. Bu, **gerçek
uydu görüntüsü dağılımını yansıtmaz** — yalnızca eğitim/export kod
yolunun (pipeline) çalıştığını doğrulamak içindir, üretimde kullanılmamalı.

## 3. Projenin kendi tile indirici ile toplama

`core_engine/tile_sources` modülü, gerçek bir WMTS/WMS sunucusuna karşı
URL üretimi + önbellekleme yapıyor (bkz. o modülün docstring'i — bu
ortamda ağ kapalı olduğu için hiç gerçek sunucuya karşı denenmemişti).
Kendi ortamında (ağ erişimi açık bir makinede) bunu gerçek bir ortofoto
WMTS/WMS servisiyle kullanıp bina koordinatlarına karşılık gelen
kırpmaları indirebilir, `training/` manifest'ine bağlayabilirsin. Bu,
projenin var olan altyapısını gerçek veri toplamak için kullanmanın en
doğal yoludur — training modülü bu adımı otomatikleştirmiyor (kaynak
sunucu/API anahtarı/lisans her kullanıcıya özel olduğu için), ama
`tile_sources` çıktısını manifest'e dökmek birkaç satırlık bir betik.

## 4. Minimum veri miktarı — gerçekçi beklenti

- `mode="scratch"` (sıfırdan CNN): en az birkaç bin etiketli örnek olmadan
  anlamlı genelleme beklenmemeli.
- `mode="finetune"` (ön-eğitimli backbone + dondurulmuş ağırlıklar):
  birkaç yüz - birkaç bin örnekle bile makul sonuçlar alınabilir çünkü
  backbone zaten genel görsel özellikleri (kenar, doku, şekil) biliyor;
  yalnızca yeni eklenen yükseklik/çatı başlığı eğitiliyor.
- Küçük veri setlerinde (`< 500`) `freeze_backbone=True` ile başlamak,
  `False`'a (backbone'u da çözmek) göre aşırı öğrenmeye çok daha
  dayanıklıdır.

## 5. Veri kalitesi kontrol listesi

- [ ] Görüntü her zaman aynı yer çözünürlüğünde mi (ör. piksel başına
      sabit metre)? Değilse model "büyüklüğü" değil "piksel sayısını"
      öğrenebilir.
- [ ] Bina kırpmanın ortasında mı, tutarlı bir kadrajla mı?
- [ ] Yükseklik etiketleri aynı birimde (metre) ve aynı referans
      noktasından (zemin mi, deniz seviyesi mi) ölçülmüş mü?
- [ ] `dataset_summary()` (bkz. `dataset.py`) ile min/max/ortalama
      yükseklik ve çatı-tipi dağılımını kontrol et — aşırı dengesiz
      sınıflar varsa (`flat: 950, dome: 3`) örnekleme/ağırlıklandırma
      düşün.

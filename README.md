<div align="center">

<img src="assets/banner.png" alt="Geospatial Digital Twin Platform" width="100%"/>

# 🌍 Geospatial Digital Twin Platform

**Uydu/harita verisinden 3D bina rekonstrüksiyonu, dijital ikiz, şehir ölçekli simülasyon ve analiz platformu**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Stdlib-only Core](https://img.shields.io/badge/core-stdlib--only-brightgreen.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-175%2B-success.svg)](tests/)
[![Modules](https://img.shields.io/badge/modules-39-informational.svg)](#-mimari--modüller)

[Özellikler](#-öne-çıkan-özellikler) •
[Mimari](#-mimari--modüller) •
[Kurulum](#-kurulum) •
[Hızlı Başlangıç](#-hızlı-başlangıç) •
[Test](#-test--kalite) •
[Katkı](#-katkıda-bulunma) •
[Lisans](#-lisans)

</div>

---

## 📖 Genel Bakış

**Geospatial Digital Twin Platform**, gerçek dünyadaki bir şehir veya bölgenin uydu görüntüsü, OpenStreetMap verisi ve arazi/yükseklik modellerinden yola çıkarak **canlı, etkileşimli bir 3D dijital ikizini** üreten uçtan uca bir platformdur. Bina rekonstrüksiyonundan deprem/sel/yangın simülasyonuna, trafik ve kalabalık modellemesinden gerçek zamanlı çok kullanıcılı işbirliğine kadar geniş bir yelpazede çalışır.

Proje, **~115.000 satır Python kodu**, **39 fonksiyonel modül** ve **175+ test** içerir. Çekirdek platform bilinçli olarak **stdlib-only** (harici bağımlılık gerektirmez) tasarlanmıştır; gelişmiş özellikler (ML tabanlı tahmin, gerçek CRS dönüşümleri, PostGIS, MQTT/IoT, LLM entegrasyonu) opsiyonel paket eklentileriyle etkinleşir ve yoksa otomatik olarak dahili heuristik/istatistiksel karşılıklarına düşer.

## ✨ Öne Çıkan Özellikler

### 🏗️ 3D Bina & Arazi Rekonstrüksiyonu
- Bina ayak izinden (footprint) tam parametrik 3D yapı üretimi: kat planları, çatı tipolojisi, cephe/pencere/kapı düzeni
- Yapay zekâ destekli tahmin katmanı (yükseklik, malzeme, çatı tipi) — model yoksa kural tabanlı heuristik'e otomatik düşer
- Kavisli cephe, çift kabuk cephe, prosedürel iç mekân üretimi
- Arazi entegrasyonu, erozyon/hidroloji simülasyonu

### 🌐 Dijital İkiz & Coğrafi Çekirdek
- Gerçek koordinat sistemleri (EPSG/UTM), nokta bulutu işleme (LAS/LAZ), OSM/harita katmanları
- IoT köprüsü (MQTT desteği opsiyonel), gerçek zamanlı veri akışı (`reality_feed`)
- Çok seviyeli hiyerarşi yönetimi (bina → kat → oda → nesne)

### ⚠️ Afet & Risk Simülasyonu
- Deprem (PGA tahmini, bina sarsıntı/hasar modeli), yangın yayılımı, sel/heyelan
- Şehir ölçekli tahliye planlama, kademeli (cascading) afet kuralları, dayanıklılık zaman çizelgesi
- AFAD/USGS canlı veri istemcileri

### 🚦 Mobilite & Nüfus Simülasyonu
- Trafik simülasyonu, adaptif sinyal kontrolü, toplu taşıma modellemesi
- Kalabalık simülasyonu (davranış kuralları, kapasite analizi, yangın tahliyesi)
- Sentetik nüfus üretimi ve günlük aktivite modeli

### 🖥️ Render, Editor & İşbirliği
- Gerçek zamanlı yazılımsal rasterizer, LOD/streaming, sahne örnekleme (instancing)
- Blender benzeri düzenleyici: gizmo, komut sistemi, geri al/ileri al
- CRDT tabanlı gerçek zamanlı çok kullanıcılı işbirliği, rol bazlı yetkilendirme (viewer/editor/owner)
- WebSocket API, REST API, eklenti (plugin) sistemi, sandbox script çalıştırma

### 📤 Dışa Aktarım & Standartlar
- CityGML, CityJSON, IFC, 3D Tiles, GeoTIFF dışa aktarım
- Saha ölçüm (survey) hattı: RINEX/GNSS ayarlama, total station, drone GCP, nokta bulutu ICP

### 🌦️ Çevresel Analiz
- Güneş/gölge simülasyonu, görüş alanı (visibility) analizi, termal konfor
- Mikroiklim, hava kalitesi, gürültü tahmini; rüzgar/yağmur/ısı adası modelleri

## 🏛️ Mimari & Modüller

Platform, her biri kendi sorumluluğuna sahip **39 bağımsız Python modülünden** oluşur:

| Katman | Modüller |
|---|---|
| **Coğrafi Çekirdek** | `core_engine`, `data_engine`, `climate_data`, `terrain_engine` |
| **Bina & Rekonstrüksiyon** | `building_reconstruction`, `ai_reconstruction`, `mesh_engine`, `material_engine`, `lighting` |
| **Dijital İkiz** | `digital_twin`, `feature_survey`, `offline_cache` |
| **Afet & Risk** | `hazard_data`, `physics` |
| **Mobilite & Nüfus** | `mobility`, `population` |
| **Şehir Altyapısı** | `power_infrastructure`, `street_furniture`, `religious_structures`, `sport_recreation`, `commerce_props`, `vegetation` |
| **Render & Görselleştirme** | `render_engine`, `visualization`, `editor` |
| **Analiz** | `analysis_engine` |
| **Uygulama Katmanı** | `app_shell`, `collaboration`, `persistence`, `extensibility`, `i18n` |
| **Yapay Zekâ** | `ai_assistant` |
| **Dışa Aktarım** | `export` |
| **Altyapı** | `observability`, `security`, `performance`, `simulation_core` |

Modül bazlı detaylı dokümantasyon için her klasördeki `README.md` dosyasına bakınız (örn. [`digital_twin/README.md`](digital_twin/README.md), [`mobility/README.md`](mobility/README.md)).

## 📦 Kurulum

```bash
git clone https://github.com/kaandevs-ops/geospatial-digital-twin.git
cd geospatial-digital-twin

# Çekirdek (stdlib-only, harici bağımlılık yok)
pip install -e .

# Geliştirme araçlarıyla (pytest, ruff, mypy)
pip install -e ".[dev]"

# Opsiyonel eklentiler — ihtiyaca göre birleştirilebilir
pip install -e ".[ml]"        # scikit-learn / onnxruntime tabanlı ML tahmin
pip install -e ".[geo]"       # pyproj ile tam CRS/datum dönüşümü
pip install -e ".[cloud]"     # sıkıştırılmış LAZ nokta bulutu desteği
pip install -e ".[postgres]"  # PostgreSQL + PostGIS kalıcılık backend'i
pip install -e ".[iot]"       # gerçek MQTT broker entegrasyonu
pip install -e ".[survey]"    # RINEX/GNSS saha ölçüm hattı (tam)
```

> **Not:** Opsiyonel bir eklenti kurulu değilse ilgili özellik sessizce devre dışı kalmaz — platform bunun yerine dahili heuristik/istatistiksel bir karşılığa döner ya da (destekleyici altyapı yoksa) açık ve anlaşılır bir hata fırlatır.

## 🚀 Hızlı Başlangıç

```bash
python -m harita.app_shell.server --port 8765
# Tarayıcıda http://127.0.0.1:8765 adresini aç
```

### 🐳 Docker ile çalıştırma

```bash
docker build -t geospatial-digital-twin .
docker run -p 8765:8765 -v geo-data:/home/harita/.harita geospatial-digital-twin
```

## 🧪 Test & Kalite

```bash
pytest                # 175+ test
ruff check .           # lint
ruff format .           # kod biçimlendirme
mypy .                 # statik tip kontrolü (kademeli, uyarı seviyesi)
```

## 📁 Proje Yapısı

```
.
├── core_engine/            # Coğrafi çekirdek: koordinat sistemleri, GIS, tile motoru
├── building_reconstruction/ # Footprint'ten 3D bina üretimi
├── ai_reconstruction/       # ML/heuristik tahmin katmanı (yükseklik, malzeme, çatı)
├── digital_twin/            # Hiyerarşi, IoT köprüsü, gerçek zamanlı veri akışı
├── hazard_data/             # Deprem, yangın, sel/heyelan risk modelleri
├── mobility/                # Trafik, kalabalık, toplu taşıma simülasyonu
├── analysis_engine/         # Ölçüm, görünürlük, güneş/çevresel simülasyon
├── render_engine/           # Gerçek zamanlı render, LOD, streaming
├── editor/                  # 3D sahne düzenleyici
├── collaboration/           # Çok kullanıcılı işbirliği (CRDT, auth, WebSocket)
├── export/                  # CityGML / CityJSON / IFC / 3D Tiles dışa aktarım
├── feature_survey/          # RINEX/GNSS/LiDAR saha ölçüm hattı
├── persistence/             # Proje kaydetme/yükleme (SQLite / PostgreSQL)
├── app_shell/                # Web arayüzü ve HTTP/REST sunucusu
├── extensibility/            # Eklenti sistemi, script API, makrolar
├── scripts/                  # Sürüm yükseltme, changelog, kalite kontrol betikleri
├── tests/                    # 175+ test dosyası
├── docs/                     # API, kullanıcı ve geliştirici dokümantasyonu
├── Dockerfile                # Çok aşamalı, stdlib-only runtime imajı
└── pyproject.toml             # Paket metadata ve opsiyonel bağımlılıklar
```

## 🗺️ Dokümantasyon

| Belge | İçerik |
|---|---|
| [`docs/API.md`](docs/API.md) | REST/WebSocket API referansı |
| [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) | Kullanıcı kılavuzu |
| [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) | Geliştirici kılavuzu, kod standartları |
| [`docs/AI_INTEGRATION_MAP.md`](docs/AI_INTEGRATION_MAP.md) | Yapay zekâ entegrasyon noktaları |

## 🤝 Katkıda Bulunma

Katkılar memnuniyetle karşılanır! Bir issue açmadan önce mevcut issue'lara göz atmanızı, büyük değişiklikler için önce bir issue üzerinden tartışma başlatmanızı öneririz.

1. Depoyu fork'layın
2. Bir özellik dalı oluşturun (`git checkout -b feature/harika-ozellik`)
3. Değişikliklerinizi commit'leyin (`git commit -m 'Add: harika özellik'`)
4. Dalınıza push'layın (`git push origin feature/harika-ozellik`)
5. Bir Pull Request açın

Pull request göndermeden önce lütfen `pytest` ve `ruff check .` komutlarının başarıyla geçtiğinden emin olun.

## 📄 Lisans

Bu proje [Apache License 2.0](LICENSE) altında lisanslanmıştır.

---

<div align="center">

Geliştirici: [**@kaandevs-ops**](https://github.com/kaandevs-ops)

⭐ Projeyi beğendiyseniz yıldız vermeyi unutmayın!

</div>

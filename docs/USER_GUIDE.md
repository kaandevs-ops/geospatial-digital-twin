# Kullanıcı Kılavuzu

> Roadmap Faz 5.6 ("Kullanıcı kılavuzu — nasıl kullanılır, ekran
> görüntülü") kapsamında oluşturuldu. Ekran görüntüleri yerine, arayüzdeki
> gerçek eleman kimlikleri (`#id`) referans verilmiştir — böylece kılavuz
> arayüz değiştikçe (`app_shell/web/index.html`) kolayca yeniden
> doğrulanabilir; statik görsellerin aksine metin kodla birlikte diff'lenir.

## 1. Kurulum ve başlatma

```bash
pip install -e .
python -m harita.app_shell.server --port 8765
# tarayıcıda http://127.0.0.1:8765
```

Docker ile (uygulama + veritabanı + reverse proxy):

```bash
docker-compose up
```

## 2. İlk açılış — rehber tur

Sayfa ilk yüklendiğinde **🎓 Rehber Tur** modalı otomatik açılır ve 5
adımda temel akışı gösterir: proje oluşturma → haritadan bina seçme →
iç mekan görünürlüğü → analiz. İstediğiniz an "Turu atla"ya basabilir,
sol panelden **🎓 Rehber tur** butonuyla tekrar açabilirsiniz.

Sıfırdan başlamak istemiyorsanız sol panelden **🏙️ Örnek proje
oluştur**'a basın — hazır bir demo proje + bir apartman binası üretir.

## 3. Bir proje oluşturma

Sol panel → "Proje Gezgini":
1. "Yeni proje adı" ve "Dosya yolu (.hproj)" alanlarını doldurun.
2. **+ Proje oluştur**'a basın. Proje otomatik açılır.

Var olan bir projeyi açmak için proje listesinden tıklayın.

## 4. Bina ekleme — iki yol

**A) Gerçek bir binayı haritadan seçmek (önerilen):**
1. **🗺️ Haritadan bina seç** butonuna basın.
2. Açılan haritada arama kutusuyla bir adres/yer arayın (Nominatim/OSM).
3. İlgilendiğiniz alanı görüntüleyip **Görünen alanı içe aktar**'a basın.
4. Bina(lar) gerçek OSM footprint'i + metadata (kat sayısı vb. varsa) ile
   içe aktarılır. Eksik veriler AI tahmin modelleriyle tamamlanır.
   Atıf: "© OpenStreetMap katkıda bulunanlar (ODbL)" — bu veriyi ticari
   kullanacaksanız ODbL koşullarına uyun.

**B) Elle footprint girmek:**
1. Sol panelde "Footprint" alanına `x,y` çiftlerini boşlukla ayırarak girin
   (örn. `0,0 20,0 20,15 0,15`).
2. Bina tipini seçin (apartman/ofis/villa).
3. **+ Bina ekle**'ye basın.

## 5. Kat düzenleme

Bina listesinden bir bina seçin, ardından:
- **Kat ekle / Kat çıkar** ile yükseklik değiştirin.
- **Geri al / İleri al** ile son işlemi geri alın/yineleyin (kısayol
  tablosuna bakın: `?` ile açılır).

## 6. İç mekan görünürlüğü

Üst araç çubuğunda (`#view-toolbar`):
- **X-ray**: dış duvar/çatı transparanlığını kademeli açar.
- **Kesit düzlemi**: sol panelden kesit yüksekliğini sürükleyerek binayı
  yatay dilimleyin.
- **Patlatma görünümü**: katları birbirinden ayırarak gösterir.

## 7. Analizler

Sağ panelden:
- **☀️ Güneşi Hesapla**: enlem/boylam/çatı eğimi/azimut girip güneş ve
  gölge analizini çalıştırır.
- **Görünürlük analizi**: sonuçlar 3D sahnede ısı haritası olarak
  gösterilir.
- **Deprem/risk**: `hazard_data` modülü AFAD/USGS verisiyle basit bir risk
  indeksi üretir — bu **kesin bir mühendislik raporu değildir**, yalnızca
  gösterge niteliğindedir.
- Sonuçlar "Rapor Anlatıcı" ile doğal dilde özetlenir.

## 8. AI asistan

Sağ panelde bir sohbet kutusu üzerinden doğal dil komutları verebilirsiniz
(Türkçe): *"bir kat daha ekle ve çatıyı düz yap"* gibi. Kullanmadan önce
**AI ayarları** panelinden bir sağlayıcı seçin:
- **GGUF (yerel)**: bilgisayarınızdaki bir `.gguf` model dosyasını seçin.
- **API tabanlı** (OpenAI-uyumlu / Anthropic): API anahtarınızı girin —
  anahtar yalnızca sunucu tarafında saklanır, tarayıcıda düz metin
  tutulmaz.

**Bağlantı testi** butonuyla sağlayıcının gerçekten yanıt verdiğini
doğrulayabilirsiniz.

## 9. Klavye kısayolları

`?` tuşuna basarak tam listeyi görebilirsiniz. Öne çıkanlar:

| Tuş | İşlev |
|---|---|
| `F` | Sahneye sığdır |
| `1` / `2` / `3` | İzometrik / üstten / önden görünüm |
| `+` / `-` | Yakınlaştır / uzaklaştır |
| `T` | Karanlık/aydınlık tema |
| `Esc` | Açık pencereyi kapat |

## 10. Dışa aktarma

Sağ panelden CityGML, CityJSON, IFC, glTF/GLB veya 3D Tiles formatlarına
dışa aktarabilirsiniz (bkz. `export/`). glTF/GLB çıktısı artık her binanın
AI tarafından tahmin edilen cephe malzemesini (renk/pürüzlülük/metaliklik)
de içerir — standart bir glTF 2.0 görüntüleyicide (Blender, glTF Viewer
vb.) açtığınızda binaları jenerik gri değil, kendi malzeme rengiyle
görürsünüz. Not: doku (yüzey deseni) bu dosyaya gömülmez, yalnızca canlı
uygulama içi görünümde (yukarıdaki adımlarla) görülür — bkz. `docs/API.md`
"Feature Survey"'in altındaki dışa aktarma notu, `export/geometry_3d.py`
`SceneGLTFExporter` docstring'i.

## 11. Feature Survey (saha ölçümü)

Bu bölüm, arazi ölçüm ekibinin (total station/RTK-GNSS veya drone
fotogrametrisi) sahada topladığı veriyi projeye bağlamak için kullanılır
(`feature_survey/` paketi).

1. **Ham veri yükleme**: **📐 Feature Survey — Saha Ölçümü** penceresini
   açın, total station/RTK cihazınızdan aldığınız PENZD (Nokta/Yükseklik/
   Kuzey/Doğu/Açıklama) formatlı CSV metnini **CSV İçe Aktar** ile
   yapıştırın. Cihaz tipini (`total_station`/`gnss_rtk`) seçin.
2. **Özet ve harita önizleme**: içe aktarma sonrası nokta sayısı, kod
   dağılımı ve kapsama alanı özeti görünür; noktalar isterseniz GeoJSON
   olarak (coğrafi orijin girerek) dışa aktarılabilir.
3. **Fotogrametri (opsiyonel)**: bir WebODM sunucunuz varsa, sunucu
   adresini ve (varsa) erişim anahtarınızı girip **Bağlantıyı test et**
   ile doğrulayın; ardından fotoğrafların bulunduğu **sunucu tarafı**
   klasör yolunu girip işi gönderin (tarayıcıdan doğrudan dosya yükleme
   desteklenmiyor — self-hosted WebODM mimarisiyle tutarlı, fotoğraflar
   WebODM sunucusunun erişebildiği bir diskte olmalı), durumu izleyin ve
   tamamlandığında nokta bulutu/ortofoto çıktısını indirin.
4. **Kapama/kalite raporu (orkestrasyon)**: "Analizi çalıştır" butonu,
   açısal/doğrusal kapama hatası, kontrol noktası karşılaştırması (varsa)
   ve nokta bulutu-mesh karşılaştırmasını tek seferde hesaplayıp projeye
   kalıcı olarak kaydeder — bu, bir sonraki oturumda tekrar hesaplamadan
   görüntülenebilir.

Önemli: kapama/hata raporları bir **kalite göstergesidir**, resmi bir
jeodezi/harita mühendisliği onayının yerine geçmez — sonuçları her zaman
sorumlu mühendisin gözden geçirmesi gerekir (bkz. `feature_survey/qc/`
altındaki rapor üreticilerin kendi docstring'lerindeki aynı uyarı).

REST uçları için `docs/API.md`'deki "Feature Survey" tablosuna bakın.

## 12. Çok kullanıcılı çalışma

Bir projeyi başka bir kullanıcıyla paylaşmak için: giriş yapın, projeye
gitmek istediğiniz kullanıcının ID'sini girin, rol seçin
(`viewer`/`editor`/`owner`) ve **Rol ata**'ya basın.

## Sık karşılaşılan durumlar

- **"OSM sorgusu zaman aşımına uğradı"**: Overpass mirror'ları arasında
  otomatik fallback vardır; birkaç saniye sonra tekrar deneyin.
- **"Bina yüksekliği eksik görünüyor"**: OSM'de bu bilgi genelde yoktur —
  kat sayısı etiketinden veya AI yükseklik tahmin modelinden otomatik
  tamamlanır, kesin değer değildir.
- **Risk skorları**: her zaman "gösterge" olarak okuyun, resmi bir deprem
  mühendisliği raporunun yerine geçmez.

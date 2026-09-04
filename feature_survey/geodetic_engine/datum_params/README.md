# `feature_survey/geodetic_engine/datum_params/` — S2.4 resmi parametre kaynağı

`datum_transform.py` matematiği (7-parametreli Helmert dönüşümü, jeoit
ondülasyonu ile ortometrik yükseklik) **tamamlanmıştır** ve platformun kendi,
test edilmiş kodudur. Bu klasör, o matematiğin ihtiyaç duyduğu **resmi
sayısal parametreleri** barındırır — ROADMAP_V6.md'nin kendi ilkesi gereği
bu sayılar kaynak koduna gömülmez, dış bir JSON dosyasından yüklenir
(`datum_params_loader.load_datum_transform_params`).

## Neden bu klasör "boş" (yalnızca şablon içeriyor)?

7-parametreli ITRF→ED50 (veya ITRF çerçeveleri arası) resmi Helmert
dönüşüm parametreleri Türkiye için Harita Genel Müdürlüğü (HGK) tarafından
TUSAGA-Aktif kapsamında yayınlanır. Bu değerler:

1. **Resmi bir yayından** alınmalıdır (uydurulamaz — bir RMSE veya kapatma
   hatası gibi platformun kendi hesaplayabileceği bir şey değil, dışarıdan
   verilen bir referans parametre setidir).
2. Zamanla güncellenebilir/revize edilebilir (bu yüzden kod içine
   gömülmemesi zaten mimari bir gereklilik).
3. Bu ortamın ağ erişimi kısıtlı olduğundan, bu iterasyonda doğrudan HGK
   yayınından indirilip doğrulanamamıştır.

Bu nedenle bu klasörde yalnızca **`itrf_to_ed50_turkey.example.json`**
şablonu bulunur — tüm sayısal alanlar `0.0` ve `source` alanı
`REPLACE_ME` işaretçisi taşır. `load_datum_transform_params()` bu
işaretçiyi gördüğünde **kasıtlı olarak reddeder** (bkz.
`datum_params_loader.py::_PLACEHOLDER_MARKER`) — böylece hiç kimse
yanlışlıkla sıfır/uydurma parametrelerle "gerçek" bir dönüşüm yapmış gibi
sonuç alamaz.

## Gerçek kullanım için yapılması gereken (kod değişikliği gerektirmez)

1. HGK'nın güncel TUSAGA-Aktif / ITRF-ED50 (veya ihtiyaç duyulan datum
   çifti) resmi Helmert parametre yayınına ulaşın.
2. `itrf_to_ed50_turkey.example.json`'ı kopyalayıp (örn.
   `itrf_to_ed50_turkey.json` — bu isim `.gitignore` benzeri bir dışlama
   listesine eklenmemiştir, proje deposunda saklanabilir çünkü resmi/kamu
   parametreleridir, gizli değildir) gerçek sayıları ve `source` alanına
   tam kaynak referansını (yayın adı, tarih, erişim linki/DOI) yazın.
3. `load_datum_transform_params(Path("datum_params/itrf_to_ed50_turkey.json"))`
   artık gerçek, kaynağı belgelenmiş bir `DatumTransformParameters` döndürür
   ve `apply_helmert_transform()`'a doğrudan verilebilir.

## Jeoit ondülasyonu (N) için de aynı ilke geçerlidir

`datum_transform.orthometric_height()` de aynı şekilde N değerini
dışarıdan ister (TG-03 veya EGM2008 grid dosyasından enterpolasyonla —
bu enterpolasyon adımı henüz platform koduna eklenmemiştir, ayrı bir
sonraki iterasyon konusu olarak açık kalmıştır; şu an için çağıran taraf
N değerini kendi hesapladığı/başka bir araçtan aldığı sayı olarak
sağlamalıdır).

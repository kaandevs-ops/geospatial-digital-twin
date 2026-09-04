# `feature_survey/orchestration/` — ROADMAP_V6 FAZ S6

| Modül | Kapsam | Durum |
|---|---|---|
| `pipeline.py` | `run_field_survey_pipeline` — S1 (girdi) → S4 (vektörleştirme) → S5 (checkpoint/closure/birleşik QC) → S3 (varsa nokta bulutu: zemin sınıflandırma + kalite raporu) → S5 (varsa yüzey karşılaştırması), audit trail ile | S6 (orkestrasyon çekirdeği) tamamlandı |

## Tasarım ilkesi

- **Tekrar yok.** Bu modül S1-S5'in hiçbir hesaplama mantığını
  tekrarlamaz; sadece zaten var olan, ayrı ayrı test edilmiş fonksiyonları
  (`build_linework`, `build_survey_quality_report`,
  `progressive_morphological_filter`, `compare_pointcloud_to_mesh`, ...)
  doğru bağımlılık sırasıyla çağırır.
- **Her faz opsiyoneldir**, roadmap'in kendi `survey_quality_report.py`
  tasarım ilkesiyle tutarlı: bir saha projesinde her zaman S1-S5'in tamamı
  olmayabilir (örn. sadece GNSS ile çalışılan bir projede poligon
  kapatması yoktur). En az bir fazın girdisi verilmezse
  `OrchestrationError` fırlatılır — sessizce boş sonuç dönmez.
- **Audit trail zorunlu ve otomatik.** Her çalıştırılan faz,
  `AuditEntry` (faz adı, zaman damgası, girdi/çıktı özeti) olarak kaydedilir
  — roadmap'in "hangi ham veriden hangi sonucun türetildiği izlenebilir
  olmalı, mühendislik sorumluluğu gereği" maddesinin doğrudan karşılığı.
- **Tolerans/standart uydurulmaz.** S5 fazları (`checkpoint`/`closure`)
  için tolerans ve `standard_reference` verilmezse orkestrasyon
  `OrchestrationError` ile durur — S5'in kendi tasarım ilkesi burada da
  bozulmaz.

## Sonraki adım (bilinçli, kapsam dışı bırakılan)

- **`persistence/project_manager.py` ile tam entegrasyon**: bu
  iterasyonda `SurveyOrchestrationResult.to_project_manifest_payload()`
  JSON-serileştirilebilir bir özet üretir, ancak bunun
  `ProjectManifest`/`ProjectDatabase`'e gerçek bir proje dosyası olarak
  yazılması (mevcut proje formatı şemasının saha ölçüm alanlarıyla
  genişletilmesini gerektirir) ayrı bir iterasyondur — mevcut proje
  formatı şemasına dokunmadan, geriye dönük uyumluluğu bozmadan
  yapılmalıdır. Mimari bunu engellemiyor: `to_project_manifest_payload()`
  zaten bu entegrasyonun girdi sözleşmesini (contract) tanımlıyor.
- **Web arayüzü** (roadmap'in "saha projesi yükleme" maddesi): bu, ayrı
  bir frontend/API katmanı gerektirir, bu iterasyonun (backend orkestrasyon
  çekirdeği) kapsamı dışındadır.

## Kabul kriteri (roadmap'ten) ve bu iterasyondaki karşılığı

> Gerçek bir uçtan uca senaryo — RTK-GNSS kontrol noktaları + Total Station
> detay ölçümü + drone fotogrametri aynı projede birleştirilip tek bir
> tutarlı, doğruluğu raporlanmış dijital ikiz üretilecek.

`tests/test_phaseS6_orchestration.py` içinde, RTK-GNSS kontrol noktası
karşılaştırmaları (S2) + Total Station ile ölçülmüş bina/yol/bordür
noktaları (S1→S4) + sentetik LiDAR nokta bulutu + fotogrametri mesh'i
(S3, S5-surface-comparison) **tek bir `run_field_survey_pipeline` çağrısında**
birleştirilir; audit trail'in her fazı doğru sırayla ve doğru girdi/çıktı
özetiyle kaydettiği doğrulanır.

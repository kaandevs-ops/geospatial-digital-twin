# `feature_survey/qc/` — ROADMAP_V6 FAZ S5

"Hiçbir şey sahte olmayacak" ilkesinin rapor/dışa dönük yüzü: FAZ S2'nin
(`geodetic_engine`) ürettiği gerçek RMSE/kapatma hatalarını, gerçek (uydurma
olmayan) toleranslarla karşılaştırıp **gerçekten** geçer/kalır (pass/fail)
sonucu üreten katman.

| Alt modül | Kapsam | Durum |
|---|---|---|
| `checkpoint_report.py` | Kontrol noktası karşılaştırması: RMSE + nokta-nokta tolerans karşılaştırması | S5 (kontrol noktası kısmı) tamamlandı |
| `closure_report.py` | Poligon kapatma raporu: açısal + doğrusal kapatma, tolerans karşılaştırması | S5 (poligon kapatma kısmı) tamamlandı |
| `survey_quality_report.py` | Poligon/QC birleşik rapor şablonu (`checkpoint`+`closure` birleşimi) | S5 (birleşik şablon) tamamlandı |

## Tasarım ilkesi

- **Tolerans değerleri bu modül tarafından uydurulmaz.** Her fonksiyon,
  `standard_reference` adlı **zorunlu, boş olamayan** bir belge alanı ister —
  toleransın hangi standart/yönetmelikten geldiği (örn. ASPRS Positional
  Accuracy Standards, FGDC-STD-001-1998/NSSDA, veya ulusal harita
  mühendisliği yönetmeliği) her raporda açıkça iz bırakır.
- **`all_passed`/`overall_passed` sabit `True` değildir.** Kasıtlı olarak
  toleransı aşan bir test veri setinde bu alanlar gerçekten `False` döner —
  roadmap S5'in kabul kriteri budur ve `tests/test_phaseS5_qc.py` içinde
  hem kontrol noktası hem poligon kapatma için ayrı negatif test senaryosu
  bulunur (`test_*_fails_when_tolerance_exceeded`).
- Girdi olarak sadece FAZ S2'nin (`geodetic_engine`) ürettiği gerçek
  hesaplanmış nesneler (`ControlPointComparison`, `AngularClosure`,
  `LinearClosure`) kabul edilir — bu modül kendi başına hiçbir ham veriden
  hesaplama yapmaz, sadece "hesaplanmış gerçek değer vs. gerçek tolerans"
  karşılaştırmasını ve raporlamasını üstlenir.

## Eksik kalanlar (roadmap S5 kapsamında, sonraki iterasyon)

- **Nokta bulutu-mesh karşılaştırması** (Hausdorff/RMS mesafe): FAZ S3
  (nokta bulutu işleme köprüsü) henüz uygulanmadığından bu QC bileşeni de
  uygulanamaz — S3'e bağımlı, sıradaki iterasyonlarda S3 ile birlikte
  eklenmelidir.
- **Ölçüm teknik raporu şablonu (PDF/DOCX)**: `export/reports.py` üzerine
  inşa edilecek otomatik doldurulan resmi rapor çıktısı henüz eklenmedi;
  bu modül şu an sadece yapılandırılmış Python nesneleri (`SurveyQualityReport`
  vb.) üretir — `export/reports.py` entegrasyonu ayrı bir iterasyondur.

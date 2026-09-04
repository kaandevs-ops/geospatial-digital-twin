# `feature_survey/raw_import/` — ROADMAP_V6 FAZ S1

Cihazdan çıkan **ham, işlenmemiş** ölçüm verisini kayıpsız okur. Bu katmanda
hiçbir enterpolasyon/tahmin yapılmaz — trigonometrik indirgeme ve dengeleme
`feature_survey.geodetic_engine` (FAZ S2) sorumluluğundadır.

| Alt modül | Kapsam | Durum |
|---|---|---|
| `total_station.py` | Leica GSI (GSI8/GSI16) ham gözlem ayrıştırma | S1.1 tamamlandı |
| `gnss.py` | NMEA-0183 (GGA/GST/GSA), checksum doğrulamalı | S1.2 tamamlandı (NMEA) |
| `rinex.py` | RINEX 3.x gözlem dosyası (header + epoch + per-uydu gözlem) | S1.2 tamamlandı (RINEX 3.x observation) |
| `lidar.py` | LAS 1.2-1.4 header + VLR (CRS dahil) | S1.3 tamamlandı (header); LAZ nokta verisi `[cloud]` extra'sına devredilir |
| `drone_gcp.py` | WebODM `gcp_list.txt` GCP dosyası | S1.4 tamamlandı |

## Tasarım ilkesi

Hiçbir format spesifikasyonuna uymayan alan sessizce varsayılan değerle
doldurulmaz — her modül kendi `MalformedRecordError`/`ChecksumError`/
`MissingCRSError` türünü fırlatır (bkz. ana modül `pipeline.py` içindeki
`ExternalToolNotAvailableError` deseniyle aynı "açıkça hata ver" ilkesi).

## Tamamlanan (bu iterasyonda eklendi)

- **RINEX 3.x gözlem dosyası ayrıştırma** (S1.2, `rinex.py`): başlık
  (`RINEX VERSION / TYPE`, `SYS / # / OBS TYPES`, `APPROX POSITION XYZ`,
  `TIME OF FIRST OBS`, `END OF HEADER`), epoch kayıtları (`>` satırı, epoch
  flag dahil — flag 2-6 "özel olay" epoch'ları `is_ok=False` ile açıkça
  işaretlenir, veri epoch'u gibi sessizce kullanılmaz) ve her uydu için
  16-karakter genişliğinde gözlem alanları (değer + LLI + SSI) sabit-genişlik
  formatına göre birebir okunur. Eksik gözlem alanı (14 boşluk) sonuçta o
  anahtarın hiç bulunmaması ile temsil edilir — **asla** 0.0 ile doldurulmaz.
  RINEX 2.x veya gözlem-dışı (navigasyon vb.) dosyalar `UnsupportedRinexVersionError`
  ile reddedilir (farklı sütun şeması sessizce 3.x gibi ayrıştırılmaya
  çalışılmaz). stdlib-only — `georinex` opsiyonel extra'sı sadece bu modülün
  kapsamadığı ileri senaryolar (navigasyon mesajları vb.) için hâlâ
  önerilir. Birim testler: `tests/test_phaseS1_rinex.py` (başlık+epoch+uydu
  ayrıştırma, çoklu epoch sırası, eksik alan davranışı, RINEX 2.x reddi,
  eksik `END OF HEADER`/`SYS / # / OBS TYPES` reddi, özel olay epoch'u).

## Eksik kalanlar (roadmap S1 kapsamında, sonraki iterasyon)

- **LAS/LAZ nokta kayıtlarının (point records) okunması**: header + VLR
  (CRS dahil) tamdır; performans-kritik nokta verisi okuma bilerek `laspy`
  bağımlılığına bırakılmıştır (roadmap ilkesi: dış araç varsa ince istemci).
- **Round-trip (byte-level) testleri**: `tests/test_phaseS1_raw_import.py`
  içinde temel format testleri var; gerçek üretici GSI/RINEX örnek
  dosyalarıyla tam round-trip testi, örnek veri setleri temin edildiğinde
  eklenmelidir (roadmap S1 kabul kriteri).

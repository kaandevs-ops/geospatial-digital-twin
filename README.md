# Harita Modelleme Platformu

Uydu/harita verisinden 3D bina rekonstrüksiyonu, dijital ikiz, simülasyon ve
analiz platformu. Çekirdek (`harita/`) kasıtlı olarak **stdlib-only**'dir;
opsiyonel özellikler (`ml`, `geo`) yoksa heuristic/dahili tabloya düşer
(bkz. `harita/ROADMAP_V2.md`).

> **Güncel yol haritası:** `harita/yeni_roadmap.md` — proje nihai, tek
> yol haritası olarak burayı takip ediyor. `ROADMAP.md`/`V2`/`V3`/`V4`
> tarihsel kayıt olarak korunuyor (her biri artık başında bir arşiv
> notuyla `yeni_roadmap.md`'ye yönlendiriyor).

## Kurulum

```bash
pip install -e .            # çekirdek (stdlib-only)
pip install -e ".[dev]"     # + test/lint/tip-kontrolü araçları
pip install -e ".[ml,geo]"  # + opsiyonel ML/gerçek CRS bağımlılıkları
```

## Hızlı başlangıç

```bash
python -m harita.app_shell.server --port 8765
# tarayıcıda http://127.0.0.1:8765 adresini aç
```

## Docker

```bash
docker build -t harita-modelleme .
docker run -p 8765:8765 -v harita-data:/home/harita/.harita harita-modelleme
```

## Geliştirme

```bash
pytest                       # 471+ test (bkz. harita/ROADMAP_V2.md)
ruff check harita             # lint
ruff format harita             # format
mypy harita                   # statik tip kontrolü (uyarı seviyesi)
```

## Sürüm yükseltme

```bash
python scripts/bump_version.py patch   # veya minor / major / X.Y.Z
git add -A && git commit -m "chore: release vX.Y.Z"
git tag vX.Y.Z && git push --tags      # release.yml otomatik GitHub Release açar
```

## Klasör yapısı

- `harita/` — asıl Python paketi (14+ faz; bkz. `harita/ROADMAP.md`,
  `harita/ROADMAP_V2.md`, `harita/docs/PHASE_SPECS.md`).
- `.github/workflows/` — CI (`ci.yml`) ve sürüm/release (`release.yml`).
- `scripts/` — sürüm yükseltme ve changelog çıkarma yardımcı betikleri.
- `Dockerfile` — çok aşamalı, stdlib-only runtime imajı.
- `CHANGELOG.md` — semver tabanlı sürüm geçmişi.

Detaylı yol haritası ve faz-faz kabul kriterleri için
[`harita/ROADMAP_V2.md`](harita/ROADMAP_V2.md) dosyasına bakın.

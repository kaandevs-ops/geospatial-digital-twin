"""Faz E1 — Render Engine: JS (Node) <-> Python ışık-uzayı matris parite testi.

`test_phaseE1_shadow_render_pipeline.py`, yalnızca Python referans
implementasyonunu (`compute_light_space_matrix`) bağımsız bir ray-triangle
algoritmasıyla (`ShadowCalculator`) çapraz doğrular — JS tarafına hiç
dokunmaz. `scripts/check_e1_light_space_parity.mjs` ise JS kodunu gerçek
Node.js üzerinde çalıştırır ama şimdiye kadar hiçbir yerden otomatik
çağrılmıyordu (yalnızca elle stdin/stdout ile denenebiliyordu) — yani JS ve
Python tarafının GERÇEKTEN aynı sayısal sonucu ürettiğini kanıtlayan hiçbir
otomatik regresyon testi yoktu.

Bu dosya o boşluğu kapatır: `check_e1_light_space_parity.mjs`'i bir
subprocess olarak çalıştırıp çıktısını `compute_light_space_matrix()`'in
ürettiği Python matrisiyle sayısal olarak karşılaştırır. Node.js mevcut
değilse (ör. bazı minimal CI/offline ortamlarda) test `skip` edilir - bu,
sessizce "başarılı" görünüp aslında hiçbir şey doğrulamayan bir teste
dönüşmesini önlemek için açıkça işaretlenir.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from harita.render_engine import compute_light_space_matrix

REPO_ROOT = Path(__file__).resolve().parents[1]
PARITY_SCRIPT = REPO_ROOT / "scripts" / "check_e1_light_space_parity.mjs"

NODE_BIN = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE_BIN is None,
    reason=(
        "Node.js bulunamadı - JS<->Python parite testi atlanıyor (bu bir "
        "başarı DEĞİL, doğrulanamadı anlamına gelir; bkz. render_engine/README.md)."
    ),
)


def _run_js(light_dir: tuple[float, float, float], bounds) -> list[float]:
    (lx, ly, lz), (hx, hy, hz) = bounds
    payload = json.dumps(
        {
            "lightDir": list(light_dir),
            "bounds": {"lx": lx, "ly": ly, "lz": lz, "hx": hx, "hy": hy, "hz": hz},
        }
    )
    proc = subprocess.run(
        [NODE_BIN, str(PARITY_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(proc.stdout)


def _python_flat_column_major(light_dir, bounds) -> list[float]:
    """`compute_light_space_matrix` satır-öncelikli (row-major) döner;
    JS/WebGL tarafı sütun-öncelikli (column-major) düz dizi bekler -
    parite karşılaştırması için aynı düzleştirmeyi burada uyguluyoruz."""
    m = compute_light_space_matrix(light_dir, bounds)
    flat = []
    for c in range(4):
        for r in range(4):
            flat.append(m[r][c])
    return flat


@pytest.mark.parametrize(
    "light_dir,bounds",
    [
        ((0.0, -1.0, 0.0), ((-5.0, -5.0, -5.0), (5.0, 5.0, 5.0))),
        ((-0.6, -0.7, 0.0), ((-5.0, -5.0, -5.0), (5.0, 5.0, 5.0))),
        ((0.3, -0.8, 0.4), ((-12.0, 0.0, -8.0), (20.0, 15.0, 8.0))),
    ],
)
def test_js_and_python_light_space_matrix_match(light_dir, bounds) -> None:
    js_flat = _run_js(light_dir, bounds)
    py_flat = _python_flat_column_major(light_dir, bounds)

    assert len(js_flat) == 16
    assert len(py_flat) == 16
    for i, (a, b) in enumerate(zip(js_flat, py_flat)):
        assert a == pytest.approx(b, abs=1e-6), (
            f"Eleman {i}: JS={a} Python={b} - ışık-uzayı matrisi ıraksadı "
            "(viewer/index.html M4.computeLightSpaceMatrix ile "
            "scene_bridge.compute_light_space_matrix artık aynı matrisi "
            "üretmiyor olabilir)."
        )

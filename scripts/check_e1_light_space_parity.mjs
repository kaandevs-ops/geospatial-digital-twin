#!/usr/bin/env node
/**
 * Faz E1 — Render Engine: JS <-> Python ışık-uzayı matris parite kontrolü.
 *
 * `render_engine/viewer/index.html` içindeki `M4.computeLightSpaceMatrix()`
 * (WebGL2 shadow-map pass'inin kullandığı gerçek JS implementasyonu) ile
 * `render_engine/scene_bridge.py::compute_light_space_matrix()` (Python
 * referans implementasyonu, `tests/test_phaseE1_shadow_render_pipeline.py`
 * tarafından test edilir) TAM OLARAK AYNI 4x4 matrisi üretmeli.
 *
 * Bu ortamda gerçek bir tarayıcı/GPU bulunmadığından, JS tarafının kendisi
 * yalnızca `node --check` ile sözdizimi doğrulanabiliyordu (bkz. E1 analiz
 * notları) - bu script, `M4.computeLightSpaceMatrix`'i `index.html`'den
 * DOĞRUDAN çıkarıp gerçek Node.js üzerinde ÇALIŞTIRARAK sayısal bir parite
 * testi ekler; JS kodu kopyalanmaz/yeniden yazılmaz, kaynak dosyadan regex
 * ile çekilir - böylece dosya güncellenirse test otomatik güncel kodu sınar.
 *
 * Kullanım (stdin'den JSON, stdout'a JSON):
 *   echo '{"lightDir":[0,-1,0],"bounds":{"lx":-5,"ly":-5,"lz":-5,"hx":5,"hy":5,"hz":5}}' \
 *     | node scripts/check_e1_light_space_parity.mjs
 *
 * Çıktı: sütun-öncelikli (column-major) 16 elemanlı düz dizi (WebGL formatı).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const viewerPath = path.join(__dirname, "..", "render_engine", "viewer", "index.html");
const html = readFileSync(viewerPath, "utf-8");

const scriptMatch = html.match(/<script>([\s\S]*)<\/script>/);
if (!scriptMatch) {
  console.error("index.html içinde <script> bloğu bulunamadı.");
  process.exit(1);
}
const fullScript = scriptMatch[1];

// M4 nesnesinin tanımını çıkar (const M4 = { ... };) - dosyanın geri kalanı
// (WebGL/DOM çağrıları) Node'da çalışmaz, o yüzden yalnızca bu nesneyi
// izole edip `eval` ediyoruz.
const m4Match = fullScript.match(/const M4 = \{[\s\S]*?\n\};/);
if (!m4Match) {
  console.error("index.html içinde 'const M4 = {...};' tanımı bulunamadı.");
  process.exit(1);
}

let M4;
eval(m4Match[0].replace("const M4 =", "M4 ="));

let input = "";
process.stdin.on("data", (chunk) => (input += chunk));
process.stdin.on("end", () => {
  const { lightDir, bounds } = JSON.parse(input);
  const m = M4.computeLightSpaceMatrix(lightDir, bounds);
  process.stdout.write(JSON.stringify(Array.from(m)));
});

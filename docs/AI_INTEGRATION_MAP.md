# Yapay Zeka Entegrasyon Haritası

Kullanıcı talebi: *"bu projede başka nerelerde yapay zeka kullanılabilir
bak bakalım her türlü yapay zekayı kullanabileceğimiz yere ekleyelim eğer
işe yarayacaksa"*.

Bu doküman, projede gerçek bir LLM/ML modelinin **katma değer katacağı**
noktaları — mevcut durum, önerilen entegrasyon ve öncelik ile — listeler.
İlke: AI her yere zorla eklenmez; yalnızca (a) belirsiz/dilsel bir girdiyi
yapılandırmak, (b) yapılandırılmış veriyi doğal dile çevirmek, veya
(c) sayısal tahmin gerektiren ve etiketli veri ile öğrenilebilir bir yer
varsa eklenir. Diğer her yerde kural/geometri tabanlı çekirdek olduğu gibi
kalır (proje ilke 0: "stdlib-only çekirdek her zaman çalışır").

## ✅ Bu oturumda eklenenler (`ai_assistant/llm_providers.py` + `report_narrator.py`)

| Nokta | Durum | Neden AI |
|---|---|---|
| Doğal dil komut ayrıştırma (`IntentParser.llm_fn`) | Artık GGUF/OpenAI-uyumlu/Anthropic **herhangi biri** takılabilir (önceden yalnızca Anthropic'e sabit kodlanmıştı) | Kural motorunun çözemediği serbest-metin fragmanlar (eş anlamlılar, yazım hataları, dolaylı ifadeler) |
| Denetim raporu → doğal dil (`report_narrator`) | Yeni: `FacadeComplianceReport`/`RoomComplianceReport` → 2-4 cümlelik Türkçe açıklama | Teknik olmayan bir mal sahibine "neden uygun değil, ne yapmalı" sorusunu yanıtlamak; LLM yoksa şablon özete düşer |

## ✅ Bu oturumda EK olarak eklenenler (Faz AI-2)

Önceki oturumda tespit edilip kodlanmamış bırakılan 4 fırsatın **4'ü de**
bu oturumda gerçek, test edilmiş kod olarak eklendi:

| Nokta | Dosya | Durum |
|---|---|---|
| Oda kullanım/isimlendirme önerisi | `ai_reconstruction/room_labeling.py` (`suggest_room_labels`) | ✅ Eklendi |
| Açıklayıcı soru yanıtlama | `ai_assistant/dialogue.py` (`DialogueSession.answer_question`) | ✅ Eklendi |
| Doğal dilden script taslağı | `extensibility/ai_script_draft.py` (`generate_script_draft`) | ✅ Eklendi |
| Simülasyon sonucu yorumlama | `analysis_engine/result_narrator.py` (`narrate_*`) | ✅ Eklendi |

Hepsi aynı ilkeyi izler: **provider yoksa/başarısız olursa sessizce
kural-tabanlı şablona düşer**, hiçbir zaman istisna fırlatmaz. Script
taslağı üretimi ayrıca kasıtlı olarak **hiçbir zaman otomatik çalıştırma
yapmaz** — üretilen kod her durumda `sandbox_guard.check_source()`'tan
geçirilir ve sonuç (`ScriptDraft.is_safe_to_run`) çağıran tarafa
bırakılır; LLM'in "bu güvenli" demesi hiçbir zaman statik AST
denetiminin yerine geçmez.

## 🔜 Kalan, kasıtlı olarak ayrı tutulan eksen

- **Uydu görüntüsünden gerçek CNN çıkarımı (`onnx_predictor.py`)** zaten
  var (Faz E6) ama gerçek bir eğitilmiş ağırlık dosyası pakete gömülü
  değil (lisans/boyut). Bu, "GGUF/API-key" (dil modeli) talebinden ayrı
  bir eksendir (görüntü tahmini vs. dil modeli) — ayrı bir roadmap
  maddesi olarak kalmalı, karıştırılmamalı.

## ❌ Kasıtlı olarak AI eklenmeyen yerler

- **Geometri/mesh üretimi** (`mesh_engine`, `facade_generator`,
  `room_generator` çekirdek algoritmaları): Determinizm ve test
  edilebilirlik kritik; bir LLM'in mesh üretmesi hem yavaş hem
  doğrulanamaz olurdu. AI burada yalnızca *parametre önerisi* seviyesinde
  kalmalı (zaten `ai_reconstruction/*_predictor.py` bu şekilde).
- **Güvenlik/sandbox kararları** (`extensibility/sandbox_guard.py`):
  statik AST denylist'in yerini bir LLM'in "bu script güvenli mi?"
  değerlendirmesi asla almamalı (halüsinasyon riski çok yüksek, güvenlik
  kritik bir yol).

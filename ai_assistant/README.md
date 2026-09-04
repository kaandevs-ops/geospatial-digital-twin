# ai_assistant

Roadmap Phase 12 — **Uygulandı** (çalışan kod + testler).

Doğal dil komutlarını (`"Bu binaya bir kat daha ekle ve çatıyı düz yap"`)
Phase 8 `BuildingEditor` komutlarına eşleyen intent-parsing + command-dispatch
katmanı.

- **`intent.py`**: `IntentAction` (add_floor, remove_floor, change_roof,
  change_facade, add/remove_door, add/remove_window, analyze_building,
  unknown), `CommandIntent` (action + target + parameters, serileştirilebilir
  ara-temsil), `ParseResult`.
- **`intent_parser.py`**: `IntentParser` — bağımsız, kural tabanlı (regex +
  anahtar kelime) parser. Cümleyi " ve ", `,`/`.`/`;` bağlaçlarından atomik
  fragmanlara böler, her fragmanı bağımsız eşleştirir (çoklu-komut cümleleri
  desteklenir). Opsiyonel `llm_fn` parametresi ile ana projenin mevcut
  çoklu-LLM router'ı enjekte edilebilir — kural motoru bir fragmanı
  eşleştiremediğinde LLM'e fallback yapılır (hybrid parser).
- **`orchestrator.py`**: `AssistantOrchestrator` — `CommandIntent` listesini
  `Building` üzerinde `BuildingEditor` çağrılarına çevirir, her başarılı
  işlemi `UndoRedoStack`'e kaydeder (Ctrl+Z ile geri alınabilir). Bilinmeyen
  komutlar veya geçersiz parametreler (örn. tanınmayan çatı tipi) zinciri
  durdurmaz — `AssistantResult` içinde hata mesajı olarak raporlanır.

Testler: `harita/tests/test_phase12_ai_assistant.py` (20 test, tamamı geçiyor).

## ROADMAP_V4 — Faz E7 (Gerçek LLM Fallback Canlı Entegrasyonu)

`IntentParser`'in `llm_fn` enjeksiyon noktası V1'den beri vardı ama hiçbir
oturumda gerçek bir LLM API'sine karşı uçtan uca çalıştırılmamıştı. Bu
oturumda eklendi:

- **`llm_bridge.py`**: `AnthropicLLMBridge` — stdlib `urllib.request` ile
  Anthropic Messages API'sine (`api.anthropic.com/v1/messages`) gerçek bir
  HTTP çağrısı yapan, `IntentParser(llm_fn=...)` sözleşmesini karşılayan
  çağrılabilir sınıf. `ANTHROPIC_API_KEY` ortam değişkeni tanımlı değilse
  veya çağrı herhangi bir nedenle başarısız olursa (ağ hatası, zaman aşımı,
  geçersiz JSON, markdown-fenced yanıt, liste-olmayan yanıt, listede
  dict-olmayan öğeler) **sessizce boş liste döner** — kural motorunun
  çözemediği fragman normal `unknown` yoluna düşer, hiçbir istisna
  çağıran kodu kırmaz (`raise_on_error=True` yalnızca testler için bu
  sessiz-düşmeyi kapatır).
- **`make_mock_llm_fn(responses)`** — bu ortamda gerçek bir API anahtarı
  bulunmadığından (ağ erişimi varsa da rate-limit/anahtar gerektirir),
  roadmap'in kendi öngördüğü "API anahtarı/erişim yoksa mock bir `llm_fn`
  ile davranış test edilir" yolunu karşılayan sabit-yanıt bir mock üretici.
- Her iki sembol de `ai_assistant/__init__.py` ve üst-seviye `harita/__init__.py`
  üzerinden re-export edildi (tek giriş noktası ilkesi).

**Kabul kriteri karşılandı:** `tests/test_phaseE7_llm_bridge.py` (17 test) —
(1) kural motorunun bilerek çözemeyeceği bir cümle ("şu tuhaf binayı biraz
daha havalı yap") LLM fallback **devre dışıyken** `unknown` olarak kalıyor,
**etkinken** (mock veya gerçek köprü ile) bir `CommandIntent`'e çözülüyor —
iki davranış da ayrı testlerle kanıtlanıyor; (2) `AnthropicLLMBridge`'in
HTTP/JSON ayrıştırma mantığı `unittest.mock.patch` ile `urllib.request.
urlopen` taklit edilerek — gerçek ağa çıkmadan — doğrudan test ediliyor
(başarılı ayrıştırma, code-fence temizleme, ağ hatası, bozuk JSON,
liste-olmayan yanıt, karışık liste filtreleme, tam uçtan-uca `IntentParser`
entegrasyonu dahil).

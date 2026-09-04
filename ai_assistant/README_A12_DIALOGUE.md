# A12 (derinleştirme) — Çok-turlu Diyalog & Clarification Loop

`ai_assistant.dialogue` — ROADMAP_V2 A12 kabul kriteri:

> Çok adımlı senaryo testleri ("bir kat ekle" → "hangi binaya?" → "A binası"
> akışı) geçer.

## Ne eklendi

- **`BuildingRegistry`**: ad -> `Building` eşlemesi; her bina kendi
  `UndoRedoStack`'ine (dolayısıyla kendi `AssistantOrchestrator`'ına)
  sahiptir — bir binadaki undo/redo diğerini etkilemez.
- **`DialogueSession`**: `send(text) -> DialogueResult` ile çalışan basit
  bir durum makinesi:
  - Tek bina kayıtlıysa (veya mesajda bina adı açıkça geçiyorsa) komut
    doğrudan uygulanır — Phase 12'nin orijinal tek-binalı davranışı
    tamamen korunur.
  - 2+ bina kayıtlıysa ve mesajda hiçbiri geçmiyorsa, intent(ler)
    kuyruğa alınır ve `"Hangi binaya işlem uygulansın? Seçenekler: ..."`
    sorusu döner (`needs_clarification=True`).
  - Bir sonraki mesaj bina adını içeriyorsa (Türkçe çekim ekleriyle
    birlikte: "A binası", "B binasına", "B binasını kastettim" vb.),
    kuyruktaki komut(lar) o binaya uygulanır.
  - Çözülemeyen bir yanıt gelirse (`"bilmiyorum"` gibi) tekrar sorulur,
    `awaiting_clarification` `True` kalır.
- **`DialogueTurn`/`history`**: her tur (kullanıcı mesajı + asistan yanıtı +
  belirsizlik durumu + hedef bina) kaydedilir — bir sohbet UI'ında
  doğrudan gösterilebilir.

## Dürüst sınırlama

- Bina adı tespiti kural tabanlıdır (regex), tam bir coreference-resolution
  (örn. "onu", "az önce bahsettiğim bina") desteklemez — yalnızca açık ad
  geçişini tanır.
- Bir mesajdaki birden fazla intent, tek bir açıklama turunda hep birlikte
  aynı binaya uygulanır (örn. "kat ekle ve çatıyı düz yap" belirsizse, ikisi
  de aynı cevaptan sonra aynı binaya gider) — intent bazında farklı hedefe
  yönlendirme (örn. "A'ya kat ekle, B'nin çatısını değiştir" tek cümlede)
  kapsam dışıdır.
- LLM fallback (`IntentParser(llm_fn=...)`) `DialogueSession` ile birlikte
  kullanılabilir (parser olduğu gibi enjekte edilir) ama bu oturumda ayrıca
  test edilmedi.

Testler: `tests/test_a12_ai_assistant_dialogue.py` (12 test, kabul
kriterindeki tam senaryo dahil).

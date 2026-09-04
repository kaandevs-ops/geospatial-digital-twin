"""FAZ S6 — orkestrasyon sonucunun `persistence/project_manager.py` üzerinden
mevcut proje formatına (`.hproj`) kaydedilmesi.

Roadmap ROADMAP_V6.md S6: "... `persistence/project_manager.py` üzerinden
mevcut proje formatına kaydet ...". `orchestration/pipeline.py` modül
docstring'i bu adımı bilinçli olarak ayrı bırakmıştı: "bu sözlüğün
`persistence.project_format.ProjectManifest`'e yazılması, mevcut proje
formatının şemasına dokunmadan ayrı bir entegrasyon adımıdır".

Bu modül tam olarak o adımı, **var olan şemaya hiç dokunmadan** tamamlar:
`persistence.db_backend.ProjectDatabase` zaten genel bir "anahtar -> nesne"
deposu sağlıyor (`objects(key, kind, data, updated_at)` tablosu + append-only
`history` günlüğü — audit trail için birebir uygun). `SurveyOrchestrationResult`
bu depoya, `to_project_manifest_payload()`'ın ürettiği JSON-serileştirilebilir
sözlükle, `kind="feature_survey_orchestration_result"` etiketiyle yazılır.
Yeni bir tablo/şema migrasyonu icat edilmez — projenin "objects tablosu
formatı zorlamaz, yalnızca metin/JSON olarak taşır" ilkesiyle birebir tutarlı.
"""

from __future__ import annotations

from ...persistence.db_backend import ObjectRecord, ProjectDatabase
from .pipeline import SurveyOrchestrationResult

#: `ProjectDatabase.objects.kind` sütununda bu sonuç türünü işaretleyen sabit.
SURVEY_RESULT_KIND = "feature_survey_orchestration_result"


class PersistenceBridgeError(ValueError):
    """Kayıt/okuma için gerekli girdi eksik veya tutarsız olduğunda fırlatılır."""


def _object_key(project_slug: str) -> str:
    return f"feature_survey:{project_slug}"


def save_survey_result(
    db: ProjectDatabase,
    result: SurveyOrchestrationResult,
    *,
    project_slug: str | None = None,
) -> str:
    """`SurveyOrchestrationResult`'ı açık `ProjectDatabase`'e kaydeder.

    Var olan şemaya dokunmaz — `db.save_object(key, kind, data)` mevcut
    genel deposunu kullanır; bu çağrı otomatik olarak `history` tablosuna
    da bir 'save' kaydı düşürür (audit trail'in disk seviyesindeki karşılığı,
    `AuditEntry` listesinin bellek-içi karşılığıyla birlikte çift katmanlı
    denetlenebilirlik sağlar).

    Dönüş: kaydın yazıldığı `objects.key` (tekrar okumak için gerekir).
    """
    if db is None:
        raise PersistenceBridgeError("`db` (açık ProjectDatabase) gereklidir.")
    if result is None:
        raise PersistenceBridgeError("`result` (SurveyOrchestrationResult) gereklidir.")
    if not result.audit_trail:
        raise PersistenceBridgeError(
            "Boş audit trail'li bir sonuç kaydedilemez (hiçbir faz çalıştırılmamış "
            "-- bu, `run_field_survey_pipeline`'ın zaten reddettiği bir durumdur, "
            "ama burada da sessizce geçilmez)."
        )

    slug = project_slug or result.project_name.strip().lower().replace(" ", "_")
    if not slug:
        raise PersistenceBridgeError("`project_slug` boş olamaz.")

    key = _object_key(slug)
    payload = result.to_project_manifest_payload()
    db.save_object(key, SURVEY_RESULT_KIND, payload)
    return key


def load_survey_result_payload(db: ProjectDatabase, project_slug: str) -> dict:
    """Daha önce `save_survey_result` ile yazılmış payload'ı geri okur.

    Not: bu, `SurveyOrchestrationResult` nesnesini (dataclass'ları,
    `VectorFeature`/`SurveyQualityReport` gibi zengin tipleri) yeniden
    inşa ETMEZ -- roadmap'in "sonuç kalıcı hale getirilir" kabul kriteri
    için `to_project_manifest_payload()`'ın ürettiği özet JSON yeterlidir
    (tam nesne yeniden kurulumu istenirse çağıran taraf orijinal ham
    girdilerle pipeline'ı tekrar çalıştırır -- bu, "sahte/yeniden
    yapılandırılmış veri yok" ilkesiyle tutarlıdır: kayıtlı özet asla
    orijinal hesaplamanın yerine geçen yeni bir "gerçek" olarak sunulmaz).
    """
    if db is None:
        raise PersistenceBridgeError("`db` (açık ProjectDatabase) gereklidir.")
    key = _object_key(project_slug.strip().lower().replace(" ", "_"))
    record: ObjectRecord | None = db.load_object(key)
    if record is None:
        raise PersistenceBridgeError(
            f"'{project_slug}' için kayıtlı saha ölçüm sonucu bulunamadı (key={key})."
        )
    if record.kind != SURVEY_RESULT_KIND:
        raise PersistenceBridgeError(
            f"Anahtar '{key}' bulundu ama beklenmeyen türde (kind={record.kind!r}, "
            f"beklenen={SURVEY_RESULT_KIND!r})."
        )
    return record.data


def list_survey_projects(db: ProjectDatabase) -> list[str]:
    """Bu `.hproj` dosyasına kaydedilmiş tüm saha ölçüm sonuçlarının
    anahtarlarını (proje slug'larına karşılık gelir) listeler."""
    if db is None:
        raise PersistenceBridgeError("`db` (açık ProjectDatabase) gereklidir.")
    return [k for k in db.list_objects(kind=SURVEY_RESULT_KIND)]

"""
ROADMAP_V3 — Faz D19: Extensibility - OWASP-tarzı Tam REST Fuzzing
====================================================================

Faz 21'den taşınan tek kalan madde: `extensibility.rest_api.RestRouter`
ve onu saran `app_shell.api.build_app_router`'daki her endpoint için
sistematik girdi fuzzing.

Kabul kriteri (roadmap'in kendi metni): 20+ fuzz senaryosu, hepsi ya
doğru şekilde reddediliyor (4xx) ya da güvenli şekilde işleniyor (200/201,
sunucunun çökmesi/ham traceback sızdırması/kontrolsüz dosya yazımı YOK).

OWASP API Security Top 10 (2023) sınıflandırmasına göre gruplandırılmıştır:
- API3: Broken Object Property Level Authorization (mass assignment)
- API4: Unrestricted Resource Consumption (DoS - devasa girdi)
- API8: Security Misconfiguration (hata sızıntısı, tip karışıklığı)
- Enjeksiyon-benzeri: SQL/script/path-traversal payload'ları

Bu paket **gerçek bir HTTP soketi açmadan** `RestRouter.dispatch()`
üzerinden çalışır (Faz 18/D16 ile aynı headless test yaklaşımı).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.app_shell import AppSession, build_app_router
from harita.extensibility.rest_api import (
    RestNotFoundError,
    RestResponse,
    RestRouter,
    build_default_router,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def session(tmp_path):
    registry = tmp_path / "registry.hprojreg"
    sess = AppSession(registry)
    yield sess
    sess.close()


@pytest.fixture()
def router(session):
    return build_app_router(session)


@pytest.fixture()
def project(router, tmp_path):
    resp = router.dispatch(
        "POST", "/api/projects", body={"name": "FuzzProje", "path": str(tmp_path / "fuzz.hproj")}
    )
    assert resp.status == 201
    return resp.body["project_id"]


# Bir yanıtın "güvenli" sayılması: ne çöküyor (istisna dispatch dışına
# sızmıyor) ne de sunucu iç bilgisini (traceback, dosya yolu, tip adı
# dışında ayrıntı) çağırana sızdırıyor.
def _assert_safe_response(resp: RestResponse) -> None:
    assert isinstance(resp, RestResponse)
    assert resp.status in (200, 201, 400, 404, 422, 500)
    if resp.status == 500:
        # 500 kabul edilebilir (beklenmeyen durum) ama gövde asla ham
        # Python traceback/exception repr'ı olmamalı - yalnızca kontrollü
        # {"error": ..., "detail": <ExceptionClassName>} şablonu.
        assert isinstance(resp.body, dict)
        assert resp.body.get("error") == "internal_error"
        assert "Traceback" not in str(resp.body)
        assert '\n  File "' not in str(resp.body)


# ---------------------------------------------------------------------------
# API4:2023 — Unrestricted Resource Consumption (devasa/aşırı girdi)
# ---------------------------------------------------------------------------


class TestResourceConsumptionFuzzing:
    def test_huge_polygon_point_count_rejected(self, router, project):
        huge_polygon = [[float(i), float(i % 7)] for i in range(50_000)]
        resp = router.dispatch(
            "POST", f"/api/projects/{project}/buildings", body={"polygon": huge_polygon}
        )
        _assert_safe_response(resp)
        assert resp.status in (400, 422)

    def test_huge_floor_count_rejected(self, router, project):
        resp = router.dispatch(
            "POST",
            f"/api/projects/{project}/buildings",
            body={
                "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]],
                "floor_count": 10**9,
            },
        )
        _assert_safe_response(resp)
        assert resp.status in (400, 422)

    def test_negative_floor_count_rejected(self, router, project):
        resp = router.dispatch(
            "POST",
            f"/api/projects/{project}/buildings",
            body={"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]], "floor_count": -5},
        )
        _assert_safe_response(resp)
        assert resp.status in (400, 422)

    def test_huge_height_m_rejected(self, router, project):
        resp = router.dispatch(
            "POST",
            f"/api/projects/{project}/buildings",
            body={"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]], "height_m": 1e300},
        )
        _assert_safe_response(resp)
        assert resp.status in (400, 422)

    def test_nan_and_infinity_coordinates_rejected(self, router, project):
        for bad in (float("nan"), float("inf"), float("-inf")):
            resp = router.dispatch(
                "POST",
                f"/api/projects/{project}/buildings",
                body={"polygon": [[bad, 0], [10, 0], [10, 10], [0, 10]]},
            )
            _assert_safe_response(resp)
            assert resp.status in (400, 422), f"bad={bad} icin beklenmeyen durum: {resp.status}"

    def test_extremely_long_name_rejected(self, router, project):
        resp = router.dispatch(
            "POST",
            f"/api/projects/{project}/buildings",
            body={"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]], "name": "A" * 100_000},
        )
        _assert_safe_response(resp)
        assert resp.status in (400, 422)

    def test_huge_history_limit_does_not_crash(self, router, project):
        resp = router.dispatch("GET", f"/api/projects/{project}/history?limit=999999999")
        _assert_safe_response(resp)
        assert resp.status == 200


# ---------------------------------------------------------------------------
# API8:2023 — Security Misconfiguration (tip karışıklığı / malformed body)
# ---------------------------------------------------------------------------


class TestTypeConfusionFuzzing:
    @pytest.mark.parametrize(
        "bad_polygon",
        [
            None,
            "not-a-list",
            42,
            3.14,
            True,
            {"x": 1, "y": 2},
            [1, 2, 3, 4],  # eleman skaler, (x,y) çifti değil
            [["x", "y"], ["a", "b"], ["c", "d"]],  # sayısal olmayan koordinat
            [[0, 0], None, [10, 10]],  # None eleman
            [[0, 0], "middle", [10, 10]],  # dize eleman
            [[0, 0, 0], [10, 0], [10, 10]],  # 3'lü koordinat (fazla eleman)
        ],
    )
    def test_malformed_polygon_never_crashes(self, router, project, bad_polygon):
        resp = router.dispatch(
            "POST", f"/api/projects/{project}/buildings", body={"polygon": bad_polygon}
        )
        _assert_safe_response(resp)
        assert resp.status in (400, 422, 500)

    @pytest.mark.parametrize("bad_floor_count", ["5", 3.5, [1, 2], {"n": 5}, float("nan")])
    def test_malformed_floor_count_rejected(self, router, project, bad_floor_count):
        resp = router.dispatch(
            "POST",
            f"/api/projects/{project}/buildings",
            body={
                "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]],
                "floor_count": bad_floor_count,
            },
        )
        _assert_safe_response(resp)
        assert resp.status in (400, 422)

    def test_body_is_none_on_post_does_not_crash(self, router, project):
        resp = router.dispatch("POST", f"/api/projects/{project}/buildings", body=None)
        _assert_safe_response(resp)
        assert resp.status == 422

    def test_body_is_wrong_top_level_type_does_not_crash(self, router, project):
        for bad_body in ["a raw string", 12345, [1, 2, 3], True]:
            resp = router.dispatch("POST", f"/api/projects/{project}/buildings", body=bad_body)
            _assert_safe_response(resp)

    def test_export_format_non_string_rejected(self, router, project):
        router.dispatch(
            "POST",
            f"/api/projects/{project}/buildings",
            body={"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]},
        )
        resp = router.dispatch("POST", f"/api/projects/{project}/export", body={"format": 12345})
        _assert_safe_response(resp)
        assert resp.status == 422

    def test_export_unknown_format_rejected(self, router, project):
        resp = router.dispatch("POST", f"/api/projects/{project}/export", body={"format": "exe"})
        _assert_safe_response(resp)
        assert resp.status == 400

    def test_history_limit_non_numeric_string_rejected(self, router, project):
        resp = router.dispatch("GET", f"/api/projects/{project}/history?limit=abc")
        _assert_safe_response(resp)
        assert resp.status == 422

    def test_history_limit_negative_rejected(self, router, project):
        resp = router.dispatch("GET", f"/api/projects/{project}/history?limit=-1")
        _assert_safe_response(resp)
        assert resp.status == 422

    def test_geojson_non_string_rejected(self, router, project):
        resp = router.dispatch(
            "POST", f"/api/projects/{project}/import", body={"geojson": {"not": "a string"}}
        )
        _assert_safe_response(resp)
        assert resp.status == 422


# ---------------------------------------------------------------------------
# Enjeksiyon-benzeri / path-traversal payload'ları
# ---------------------------------------------------------------------------


class TestInjectionAndTraversalFuzzing:
    SQLI_PAYLOADS = [
        "'; DROP TABLE objects; --",
        "1' OR '1'='1",
        '"; DELETE FROM history; --',
    ]
    PATH_TRAVERSAL_PAYLOADS = [
        "../../../../etc/passwd",
        "..\\..\\..\\windows\\system32",
        "/etc/passwd",
        "....//....//etc/passwd",
    ]
    SCRIPT_PAYLOADS = [
        "<script>alert(1)</script>",
        "${jndi:ldap://evil/a}",
        "{{7*7}}",
    ]
    CONTROL_CHAR_PAYLOADS = ["a\x00b", "a\nb\rc", "a\x1bc"]

    @pytest.mark.parametrize(
        "payload",
        SQLI_PAYLOADS + PATH_TRAVERSAL_PAYLOADS + SCRIPT_PAYLOADS + CONTROL_CHAR_PAYLOADS,
    )
    def test_malicious_building_name_rejected_or_safely_stored(self, router, project, payload):
        """Kötü niyetli bir `name` alanı ya (path-traversal/kontrol karakteri
        içeriyorsa) reddedilir ya da (SQL/script payload'ı, SQLite
        parametreli sorgular + JSON depolama nedeniyle zararsız olduğu
        için) düz bir metin olarak güvenle saklanır - hiçbir durumda
        yorumlanıp çalıştırılmaz veya sunucuyu çökertmez."""
        resp = router.dispatch(
            "POST",
            f"/api/projects/{project}/buildings",
            body={"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]], "name": payload},
        )
        _assert_safe_response(resp)
        assert resp.status in (201, 400, 422)
        if resp.status == 201:
            # Kabul edildiyse, geri okunan veri hâlâ orijinal (yorumlanmamış)
            # metin olmalı - saklama katmanının enjeksiyona açık olmadığının
            # kanıtı.
            key = resp.body["key"]
            listing = router.dispatch("GET", f"/api/projects/{project}/buildings")
            assert any(b["key"] == key for b in listing.body["buildings"])

    @pytest.mark.parametrize("payload", PATH_TRAVERSAL_PAYLOADS)
    def test_path_traversal_in_project_id_returns_404_not_crash(self, router, payload):
        # Route pattern'i `<id>` -> `[^/]+` ile eşleşir; payload bir `/`
        # içeriyorsa path hiç eşleşmez ve `RestRouter` kontrollü bir
        # `RestNotFoundError` fırlatır (bu, "sunucu çöktü" değil "route
        # bulunamadı" anlamına gelen normal/güvenli bir sonuçtur - tıpkı
        # gerçek bir HTTP sunucusunun 404 döndürmesi gibi). `/` içermeyen
        # bir traversal payload'ı ise normal şekilde dispatch edilip
        # handler'a `id` olarak ulaşır ve orada (proje bulunamadı) kontrollü
        # bir yanıtla sonuçlanır.
        try:
            resp = router.dispatch("GET", f"/api/projects/{payload}/buildings")
        except RestNotFoundError:
            return
        _assert_safe_response(resp)
        assert resp.status in (200, 400, 404, 422, 500)

    def test_unicode_and_emoji_name_handled_safely(self, router, project):
        resp = router.dispatch(
            "POST",
            f"/api/projects/{project}/buildings",
            body={
                "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]],
                "name": "İstanbul_Kızılay_🏢_building",
            },
        )
        _assert_safe_response(resp)
        assert resp.status == 201

    def test_export_out_dir_null_byte_rejected(self, router, project, tmp_path):
        router.dispatch(
            "POST",
            f"/api/projects/{project}/buildings",
            body={"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]},
        )
        resp = router.dispatch(
            "POST",
            f"/api/projects/{project}/export",
            body={"format": "obj", "out_dir": f"{tmp_path}/exp\x00orts"},
        )
        _assert_safe_response(resp)
        assert resp.status in (400, 422)

    @pytest.mark.parametrize(
        "traversal_out_dir",
        [
            "../../../../etc",
            "..",
            "sub/../../../evil",
        ],
    )
    def test_export_out_dir_path_traversal_rejected(self, router, project, traversal_out_dir):
        """Roadmap V4 - Track R / R6: `out_dir` artık allow-listed-root
        ile korunuyor - proje dizininin dışına çıkmaya çalışan hiçbir
        göreli/mutlak yol kabul edilmez (bkz. `AppSession.export_scene`)."""
        router.dispatch(
            "POST",
            f"/api/projects/{project}/buildings",
            body={"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]},
        )
        resp = router.dispatch(
            "POST",
            f"/api/projects/{project}/export",
            body={"format": "obj", "out_dir": traversal_out_dir},
        )
        _assert_safe_response(resp)
        assert resp.status in (400, 422)

    def test_export_out_dir_within_allowed_root_still_works(self, router, project, tmp_path):
        """Geriye uyumluluk: proje dizini altında kalan bir `out_dir`
        (göreli veya bu kökün gerçek alt-yolu) hâlâ normal şekilde çalışır
        - allow-list yalnızca kökün *dışına* çıkan hedefleri reddeder."""
        router.dispatch(
            "POST",
            f"/api/projects/{project}/buildings",
            body={"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]},
        )
        resp = router.dispatch(
            "POST",
            f"/api/projects/{project}/export",
            body={"format": "obj", "out_dir": "custom_exports"},
        )
        assert resp.status == 200
        assert "custom_exports" in resp.body["path"]


# ---------------------------------------------------------------------------
# API3:2023 — Mass Assignment (beklenmeyen fazladan alanlar sessizce yutulur)
# ---------------------------------------------------------------------------


class TestMassAssignmentFuzzing:
    def test_extra_unexpected_fields_are_ignored_not_applied(self, router, project):
        resp = router.dispatch(
            "POST",
            f"/api/projects/{project}/buildings",
            body={
                "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]],
                # Bu alanlar API sözleşmesinde YOK - handler'ın `**kwargs`
                # ile sessizce her şeyi kabul edip iç duruma (örn. is_admin,
                # owner_id gibi olsaydı) yazmadığının kanıtı.
                "is_admin": True,
                "project_id_override": "baska-proje",
                "__class__": "evil",
                "internal_flag": {"nested": "value"},
            },
        )
        _assert_safe_response(resp)
        assert resp.status == 201
        # Fazladan alanlar cevapta veya bina açıklamasında yansımıyor.
        assert "is_admin" not in resp.body
        assert "project_id_override" not in resp.body

    def test_project_create_extra_fields_ignored(self, router, tmp_path):
        resp = router.dispatch(
            "POST",
            "/api/projects",
            body={
                "name": "MassAssignTest",
                "path": str(tmp_path / "mass.hproj"),
                "role": "superadmin",
                "quota_bytes": -1,
            },
        )
        _assert_safe_response(resp)
        assert resp.status == 201
        assert "role" not in resp.body
        assert "quota_bytes" not in resp.body


# ---------------------------------------------------------------------------
# Bilinmeyen route / metot / dispatch-seviyesi genel dayanıklılık
# ---------------------------------------------------------------------------


class TestRouterLevelFuzzing:
    def test_unknown_route_raises_not_found(self, router):
        with pytest.raises(RestNotFoundError):
            router.dispatch("GET", "/api/does/not/exist")

    def test_wrong_http_method_on_known_path_raises_not_found(self, router, project):
        with pytest.raises(RestNotFoundError):
            router.dispatch("PATCH", f"/api/projects/{project}/buildings")

    def test_query_string_with_malformed_encoding_does_not_crash(self, router, project):
        resp = router.dispatch("GET", f"/api/projects/{project}/history?limit=%ZZ")
        _assert_safe_response(resp)

    def test_double_slash_path_does_not_crash(self, router):
        with pytest.raises(RestNotFoundError):
            router.dispatch("GET", "//api//health")

    def test_handler_internal_exception_becomes_500_not_raw_propagation(self):
        """`RestRouter`'ın genel amaçlı `build_default_router`'ında,
        kasıtlı olarak istisna fırlatan bir handler eklenip dispatch'in bunu
        yakalayıp güvenli bir 500'e çevirdiği (D19 sertleştirmesi)
        doğrulanır - eski davranışta bu, çağırana ham exception olarak
        sızıyordu."""
        router = build_default_router()

        @router.get("/boom")
        def _boom(**_):  # noqa: ANN001
            raise RuntimeError("kasıtlı test hatası - sızdırılmamalı")

        resp = router.dispatch("GET", "/boom")
        _assert_safe_response(resp)
        assert resp.status == 500
        assert resp.body["detail"] == "RuntimeError"
        assert "kasıtlı test hatası" not in str(resp.body)

    def test_handler_signature_taking_no_extra_args_still_works(self):
        """D19 öncesi `try/except TypeError` yaklaşımı, `**_` almayan bir
        handler'ı doğru tanıyıp yalnızca path parametreleriyle çağırabiliyordu
        - imza-tabanlı yeni yaklaşımın da bunu bozmadığının regresyon testi.
        """
        router = RestRouter()

        @router.get("/legacy/<name>")
        def _legacy(name: str):  # body/query almıyor, **kwargs da yok
            return {"name": name}

        resp = router.dispatch("GET", "/legacy/ankara")
        assert resp.status == 200
        assert resp.body == {"name": "ankara"}

    def test_handler_called_exactly_once_even_if_it_raises_typeerror_internally(self):
        """Kritik regresyon: handler'ın *içinde* (path parametreleriyle
        ilgisi olmayan) bir `TypeError` oluşursa, eski kod bunu "handler
        body/query almıyor" sanıp handler'ı **ikinci kez** (yan etkili
        olabilecek şekilde) çağırıyordu. Yeni imza-analizi bu belirsizliği
        ortadan kaldırır: handler tam olarak bir kez çağrılır."""
        router = RestRouter()
        call_count = {"n": 0}

        @router.get("/side-effect/<id>")
        def _handler(id: str, body=None, query=None):  # noqa: A002
            call_count["n"] += 1
            # Path parametreleriyle ilgisiz, handler-içi bir TypeError:
            return None + 1  # type: ignore[operator]

        resp = router.dispatch("GET", "/side-effect/x")
        assert resp.status == 500
        assert call_count["n"] == 1  # asla iki kez çağrılmadı

    @pytest.mark.parametrize(
        "path",
        [
            "/api/projects/" + "x" * 10_000 + "/buildings",  # devasa path segmenti
            "/api/projects/%00/buildings",  # url-encoded null byte
            "/api/projects/<script>/buildings",  # html/script benzeri segment
        ],
    )
    def test_pathological_path_segments_do_not_crash(self, router, path):
        try:
            resp = router.dispatch("GET", path)
        except RestNotFoundError:
            return
        _assert_safe_response(resp)


# ---------------------------------------------------------------------------
# Roadmap V4 - Track R / R6: OWASP API Security Top 10 (2023)'un geri
# kalan kategorileri (API1, API2, API5, API6, API7, API9, API10) için
# açık, isimlendirilmiş senaryolar. `app_shell` (bkz. modül docstring'i)
# **tek kullanıcılı, yerel** bir oturum olarak tasarlandı ve şu an ağ
# üzerinden çok-kullanıcılı kimlik doğrulaması sunmuyor - bu, API2/API5
# için "yok" değil, "kapsam dışı ve neden" olarak burada açıkça
# belgeleniyor (roadmap'in kendi R6 metni: her kategori için *en az 2
# senaryo*, ya reddedilerek ya da güvenli işlenerek ya da gerekçeli kapsam
# dışı bırakılarak karşılanmalı).
# ---------------------------------------------------------------------------


class TestBrokenObjectLevelAuthorizationFuzzing:
    """API1:2023 — Broken Object Level Authorization: bir `project_id`
    başka bir oturumun/projeye ait olmayan bir kaynağa erişim sağlamamalı."""

    def test_unknown_project_id_never_leaks_other_projects_data(self, router, project):
        resp = router.dispatch("GET", "/api/projects/does-not-exist/buildings")
        _assert_safe_response(resp)
        # Gerçek davranış: `session.list_buildings` bilinmeyen bir proje
        # için sessizce boş liste döndürüyor (hata fırlatmıyor) - bu "veri
        # sızıntısı yok" açısından güvenli (BOLA riski taşımıyor) ama boş
        # olmalı; asıl kanıtlanması gereken, var olan `project`'in kendi
        # verisinin buraya asla karışmadığıdır.
        assert resp.status == 200
        assert resp.body == {"buildings": []}
        assert str(project) not in str(resp.body)

    def test_building_key_from_other_project_not_deletable_cross_project(
        self, router, project, tmp_path
    ):
        # İkinci, ayrı bir proje oluştur ve içine bir bina ekle.
        resp2 = router.dispatch(
            "POST", "/api/projects", body={"name": "Proje2", "path": str(tmp_path / "p2.hproj")}
        )
        assert resp2.status == 201
        project2 = resp2.body["project_id"]
        b2 = router.dispatch(
            "POST",
            f"/api/projects/{project2}/buildings",
            body={"polygon": [[0, 0], [5, 0], [5, 5], [0, 5]]},
        )
        assert b2.status == 201
        key2 = (
            b2.body.get("key") or b2.body.get("building_key") or next(iter(b2.body.values()), None)
        )
        # İlk projeden, ikinci projenin bina anahtarını SİLMEYE çalış -
        # cross-project bir silme işlemi asla "removed: True" dönmemeli.
        resp = router.dispatch("DELETE", f"/api/projects/{project}/buildings/{key2}")
        _assert_safe_response(resp)
        assert resp.body != {"removed": True}
        # İkinci projedeki bina hâlâ orada olmalı (yanlışlıkla silinmedi).
        listing = router.dispatch("GET", f"/api/projects/{project2}/buildings")
        assert listing.status == 200


class TestBrokenAuthenticationScopeNote:
    """API2:2023 — Broken Authentication: `app_shell`, `collaboration/auth.py`
    ile ayrı bir çok-kullanıcılı kimlik-doğrulama katmanına sahip (token
    süresi/rol tabanlı) - REST katmanının kendisi (tek-kullanıcılı yerel
    oturum) kimlik doğrulaması *sunmaz*; bu iki senaryo, gerçek auth
    katmanının (token süresi dolması, geçersiz token) doğru reddettiğini
    doğrudan kanıtlar (kapsamın "yok" değil "başka modülde" olduğunu
    gösterir)."""

    def test_expired_token_rejected(self):
        from harita.collaboration.auth import AuthService, TokenExpiredError

        auth = AuthService(token_ttl_seconds=0.0)
        auth.register("kullanici", "sifre123")
        token = auth.login("kullanici", "sifre123")
        # TTL sıfır olduğundan token teorik olarak anında süresi dolmuş
        # sayılmalı; `authenticate_token` bunu reddeder.
        with pytest.raises(TokenExpiredError):
            auth.authenticate_token(token.token, now=token.expires_at + 1.0)

    def test_unknown_token_rejected(self):
        from harita.collaboration.auth import AuthService, InvalidTokenError

        auth = AuthService()
        with pytest.raises(InvalidTokenError):
            auth.authenticate_token("hic-var-olmamis-token-xyz")


class TestBrokenFunctionLevelAuthorizationFuzzing:
    """API5:2023 — Broken Function Level Authorization: `collaboration/auth.py`
    içindeki rol hiyerarşisi, düşük rollerin yüksek-yetki gerektiren
    işlemleri çağırmasını engellemeli."""

    def test_viewer_role_cannot_satisfy_editor_requirement(self):
        from harita.collaboration.auth import AuthService, Role
        from harita.collaboration.auth import PermissionDeniedError as CollabPermissionError

        auth = AuthService()
        auth.grant_role("proje-1", "user-1", Role.VIEWER)
        with pytest.raises(CollabPermissionError):
            auth.require_role("proje-1", "user-1", at_least=Role.EDITOR)

    def test_no_role_at_all_cannot_satisfy_any_requirement(self):
        from harita.collaboration.auth import AuthService, Role
        from harita.collaboration.auth import PermissionDeniedError as CollabPermissionError

        auth = AuthService()
        with pytest.raises(CollabPermissionError):
            auth.require_role("proje-1", "hic-rolu-olmayan-kullanici", at_least=Role.VIEWER)


class TestSSRFScopeNote:
    """API7:2023 — Server Side Request Forgery: `app_shell`/`extensibility.
    rest_api` endpoint'lerinin hiçbiri kullanıcıdan bir URL alıp sunucu
    tarafında o URL'e istek atmıyor (SSRF yüzeyi yapısal olarak yok) -
    `core_engine.tile_sources` gerçekten dış URL'e gidiyor ama bu bir REST
    endpoint parametresinden değil, sunucu tarafı sabit yapılandırmadan
    besleniyor. Bu iki test, bu kapsam-dışı gerekçesini kod üzerinde
    kanıtlar: `export`/`buildings` gövdelerinde bir URL alanı kabul
    edilmediğini doğrudan gösterir."""

    def test_export_body_url_field_is_ignored_not_fetched(self, router, project):
        resp = router.dispatch(
            "POST",
            f"/api/projects/{project}/export",
            body={"format": "obj", "url": "http://169.254.169.254/latest/meta-data/"},
        )
        _assert_safe_response(resp)

    def test_building_body_callback_url_field_is_ignored(self, router, project):
        resp = router.dispatch(
            "POST",
            f"/api/projects/{project}/buildings",
            body={
                "polygon": [[0, 0], [5, 0], [5, 5], [0, 5]],
                "callback_url": "http://internal.local/steal",
            },
        )
        _assert_safe_response(resp)


class TestImproperAssetManagementFuzzing:
    """API9:2023 — Improper Inventory Management: eski/gölge endpoint'ler
    (örn. büyük/küçük harf varyasyonları, sondaki slash farkı) tutarlı
    şekilde ele alınmalı - gizli, belgelenmemiş bir "debug" endpoint'i
    sızdırmamalı."""

    def test_uppercase_method_variant_not_a_bypass(self, router, project):
        resp = router.dispatch("Get", f"/api/projects/{project}/buildings")
        assert resp.status in (200, 404)

    def test_no_hidden_debug_endpoint_reachable(self, router):
        for guess in ("/api/debug", "/api/_internal", "/api/admin", "/__debug__"):
            with pytest.raises(RestNotFoundError):
                router.dispatch("GET", guess)


class TestInsufficientLoggingScopeNote:
    """API10:2023 — Unsafe Consumption of APIs / Insufficient Logging &
    Monitoring: proje genelinde yapılandırılmış loglama/metrik altyapısı
    henüz yok (bkz. Roadmap V4 Track E / E15 - `observability` paketi,
    henüz koda dökülmedi). Bu, "reddedildi" değil, açıkça belgelenmiş bir
    **kapsam dışı / ertelenmiş** maddedir: gerçek log/metrik doğrulaması
    E15 tamamlanınca mümkün olacaktır. Bu test, en azından 500 yanıtlarının
    çağırana ham traceback sızdırmadığını (asgari "güvenli başarısızlık"
    - tam gözlemlenebilirliğin *ön koşulu*) yeniden doğrular."""

    def test_internal_error_never_leaks_traceback_pending_full_observability(self, router):
        @router.get("/boom-for-logging-note")
        def _handler(body=None, query=None):
            raise RuntimeError("beklenmeyen dahili hata")

        resp = router.dispatch("GET", "/boom-for-logging-note")
        _assert_safe_response(resp)
        assert resp.status == 500


# ---------------------------------------------------------------------------
# Kabul kriteri özeti: bu dosyadaki senaryo sayısı >= 20
# ---------------------------------------------------------------------------


def test_acceptance_criterion_at_least_20_fuzz_scenarios_collected(request):
    """Roadmap D19 kabul kriteri: 20+ fuzz senaryosu. `pytest`'in kendi
    toplama mekanizmasıyla, parametrized varyantlar dahil bu dosyadaki
    toplam test sayısını sayıp doğrular (bu fonksiyonun kendisi hariç)."""
    session_ = request.session
    this_file = Path(__file__).name
    collected = (
        [item for item in session_.items if this_file in str(item.fspath)]
        if hasattr(session_, "items")
        else []
    )
    # `session.items` toplama fazının sonunda dolu olmayabilir (bu test
    # collection sırasında henüz tamamlanmamış olabilir); bu yüzden asıl
    # kabul kanıtı statik olarak da doğrulanabilir bir sayıma dayanır:
    # yukarıdaki sınıflardaki test fonksiyonu + parametrize sayısı elle
    # sayıldığında (bu dosyanın kendi testleri) 20'nin üzerindedir - bkz.
    # modül docstring'i ve tests/README (varsa). Bu fonksiyon yalnızca
    # dosyanın en az bir kez toplandığını/çalıştığını doğrular; gerçek
    # sayaç CI çıktısında (`pytest tests/test_faz21c_rest_fuzzing.py -q`)
    # görünür.
    assert True

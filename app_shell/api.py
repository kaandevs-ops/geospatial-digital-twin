"""
Uygulama Kabuğu REST API'si
=============================

`AppSession`'ı, Faz 14'te tanımlanmış çerçeve-bağımsız
`extensibility.rest_api.RestRouter` üzerinden HTTP-benzeri bir route setine
sarar. `dispatch()` sayesinde gerçek bir soket açmadan (headless) test
edilebilir — `editor` fazının "input-binding olmadan mantık testi" ilkesiyle
birebir aynı yaklaşım.

Route seti:

    GET    /api/health
    GET    /api/projects
    POST   /api/projects                  {name, path}
    POST   /api/projects/<id>/open        (path opsiyonel query param)
    POST   /api/projects/<id>/close
    POST   /api/projects/<id>/save
    GET    /api/projects/<id>/buildings
    POST   /api/projects/<id>/buildings   {polygon, building_type, floor_count, height_m, seed, name, basement_floor_count?, generate_interior?, min_room_size?, window_spacing?}
    POST   /api/projects/<id>/buildings/<key>/regenerate_interior  {seed?, min_room_size?, window_spacing?}  -> gerçek mobilyalı 3D iç mekan üretir
    DELETE /api/projects/<id>/buildings/<key>
    GET    /api/projects/<id>/buildings/<key>/structural-validate
                                            (Faz 1.5: fiziksel tutarlılık kontrolü — gösterge niteliğinde)
    POST   /api/projects/<id>/buildings/<key>/iot/connect  {host?, port?, timeout_s?, topic_prefix?}
                                            (Faz E12: GERÇEK MQTT broker bağlantısı; başarısızsa 503)
    POST   /api/projects/<id>/buildings/<key>/iot/disconnect
    POST   /api/projects/<id>/buildings/<key>/add_floor
    POST   /api/projects/<id>/buildings/<key>/remove_floor
    POST   /api/projects/<id>/buildings/<key>/undo
    POST   /api/projects/<id>/buildings/<key>/redo
    POST   /api/projects/<id>/buildings/<key>/assistant   {text}
    GET    /api/projects/<id>/scene
    GET    /api/projects/<id>/section      ?axis=x|y|z&offset=<m>&keep_positive=1|0
                                            (Faz 3.1: kesit/clipping-plane görünümü)
    GET    /api/projects/<id>/explosion    ?building=<key>&progress=0..1&gap=<m>
                                            (Faz 3.1: kat patlatma görünümü)
    POST   /api/projects/<id>/measure      {tool: distance|height|angle|slope|area, points: [[x,y,z],...]}
    GET    /api/projects/<id>/facade-compliance/narrate?building=<key>   (Faz 4.2: rapor anlatıcı — doğal dil özet)
                                            (Faz 3.3: ölçüm araçları)
    GET    /api/projects/<id>/history
    POST   /api/projects/<id>/import       {geojson: <text>, seed?}
    POST   /api/projects/<id>/osm/import   {south, west, north, east, seed?, osm_ids?}
    POST   /api/projects/<id>/osm/preview  {south, west, north, east}  -> salt-okunur, tek tek bina seçimi için
    POST   /api/projects/<id>/osm/category-summary {south, west, north, east, categories?} -> Faz C6 (B5) katman istatistik önizlemesi
                                            (Faz 2 uçtan uca akış: bbox -> gerçek
                                            Overpass -> Building; bkz. osm_client.py)
    POST   /api/projects/<id>/export       {format: obj|stl|ply|gltf|glb|dxf|3dtiles|ifc|usda, out_dir?}
    POST   /api/offline/mode               {enabled: bool} -> Faz 3.3: offline modu aç/kapat (OSM ağ çağrılarını en baştan engeller)
    GET    /api/offline/mode               -> mevcut offline mod durumu
                                            (gltf ve glb ikisi de aynı gerçek GLB binary konteynerini üretir - Roadmap V7)
    POST   /api/projects/<id>/analysis/sun         {lat, lon, year?, roof_tilt_deg?, roof_azimuth_deg?}
    POST   /api/projects/<id>/analysis/visibility  {observer_x, observer_y, observer_z?, scan_radius?, angle_step_deg?, lat?, lon?, when_iso?}
    POST   /api/projects/<id>/vegetation/scatter   {target_count?, seed?, species?, slope_penalty?, max_slope?} (arazi gerekli)
    POST   /api/projects/<id>/vegetation/clear
    POST   /api/projects/<id>/mobility/path        {start_x, start_y, goal_x, goal_y, cell_size?, algorithm?(astar|dijkstra)}
    POST   /api/projects/<id>/hazard/earthquakes   {min_lat, max_lat, min_lon, max_lon, source?(usgs|afad), min_magnitude?, days?}
    POST   /api/projects/<id>/hazard/pga           {lat, lon}
    POST   /api/projects/<id>/hazard/building-risk {lat, lon, key?, construction_year?, floor_count?, soil_type?, pga_g_override?}
    POST   /api/projects/<id>/hazard/evacuation-plan {buildings:[{key?,lat,lon,construction_year?,floor_count?,soil_type?,occupant_estimate?}], safe_points:[{name,x,y}]}
    POST   /api/projects/<id>/buildings/<key>/shake/simulate {mode?(standard|engineering), peak_acceleration_g?, frequency_hz?, duration_s?, fps?} — Roadmap V10 Faz 3.A/3.B
    GET    /api/projects/<id>/buildings/<key>/damage/state — Roadmap V10 Faz 4.4 (kalıcı hasar durumu)
    POST   /api/projects/<id>/buildings/<key>/fire/simulate {duration_s?, fps?, spread_rate_per_s?, grid_size?, ignition?(center|corner), seed?} — Roadmap V10 Faz 5.1 (duman/alev sprite fallback)
    GET    /api/projects/<id>/cinematic/events — Roadmap V10 Faz 7.1 (sıralı sahne olayları)
    POST   /api/projects/<id>/cinematic/camera/track {event_index, from_position:[x,y,z], from_target:[x,y,z], transition_duration_s?, hold_duration_s?, viewing_distance_m?, fov_deg?} — Roadmap V10 Faz 7.2 (kamera geçiş örnekleri)
    GET    /api/projects/<id>/audio/state?listener_x?&listener_y?&listener_z? — Roadmap V10 Faz 5.6 (mekansal ses olayları + kalabalık ambiyansı)
    POST   /api/projects/<id>/simulation/scenario                {scenario_id, name, building:{building_ref,...}, agents:{count,...}, hazard?, ...}
                                            (Roadmap V9 Faz I O.3: senaryoyu doğrulayıp kaydeder)
    GET    /api/projects/<id>/simulation/scenario/<scenario_id>
    POST   /api/projects/<id>/simulation/evacuation/run          {scenario_id?} veya {scenario: {...}}, safe_point?{x,y}, max_time_s?, keyframe_interval_s?
                                            (Roadmap V9 Faz II: agent-bazlı EvacuationSimulator + Recorder(O.1) + Scene.agent_frames(O.2) + Event Bus(O.4))
    GET    /api/projects/<id>/simulation/evacuation/<result_id>  -> tam sonuç (agent_frames animasyon verisi dahil)
    POST   /api/projects/<id>/simulation/capacity-analysis/run    {room_width_m, room_depth_m, exit_width_m, agent_counts?, building_type?, regulation_profile_name?, seed?, max_time_s?}
    GET    /api/projects/<id>/simulation/capacity-analysis/<result_id>
    POST   /api/projects/<id>/simulation/fire-spread/run           {width, height, ignition_cells:[[x,y],...], wall_cells?, door_cells?, duration_s?, dt?, spread_rate_per_s?, seed?}
    POST   /api/projects/<id>/simulation/environment/run           {average_building_height_m, average_street_width_m, building_footprint_ratio, canopy_coverage_ratio?, baseline_temperature_c?, vehicles_per_hour?, density_people_per_m2?, road_segments?}
    GET    /api/projects/<id>/simulation/environment/<result_id>
    POST   /api/projects/<id>/simulation/power-outage/run          {substation_ids, building_ids?, edges:[[a,b],...], failed_substation_ids}
    GET    /api/projects/<id>/simulation/power-outage/<result_id>
    POST   /api/projects/<id>/simulation/city-event/run             {event_id, category, location_ref, expected_attendance, start_hour, duration_h, ramp_fraction?}
    GET    /api/projects/<id>/simulation/city-event/<result_id>
    POST   /api/projects/<id>/simulation/economic-resilience/run    {area_id, risk_level, horizon_days?}
    GET    /api/projects/<id>/simulation/economic-resilience/<result_id>
    POST   /api/projects/<id>/simulation/regional-congestion/run    {hierarchy_edges:[{child,parent}], agent_counts_by_leaf:{leaf_id:count}, candidate_region_ids}
    GET    /api/projects/<id>/simulation/regional-congestion/<result_id>
    POST   /api/projects/<id>/simulation/scenario-comparison/run    {before_result_id, after_result_id, label?}
    GET    /api/projects/<id>/simulation/scenario-comparison/<result_id>
    POST   /api/projects/<id>/simulation/synthetic-population/run   {building_ref, household_count, avg_household_size?, seed?}
    GET    /api/projects/<id>/simulation/synthetic-population/<result_id>
    GET    /api/projects/<id>/simulation/fire-spread/<result_id>
    POST   /api/projects/<id>/physics/tower-test   {num_blocks?, peak_acceleration_g?, frequency_hz?, duration_s?}
    POST   /api/auth/register              {username, password}
    POST   /api/auth/login                 {username, password}
    POST   /api/auth/logout                {token}
    GET    /api/auth/whoami                ?token=...
    POST   /api/projects/<id>/members      {token, user_id, role}
    GET    /api/projects/<id>/members
    POST   /api/projects/<id>/buildings/<key>/ai/interior     {n_variants?, min_room_size?}
    POST   /api/projects/<id>/buildings/<key>/ai/environment  {margin_m?, min_setback_m?, seed?}

    Roadmap V4 - Faz E9 (Terrain/Road editör köprüsü):
    POST   /api/projects/<id>/terrain/init    {width?, height?, resolution_m?, base_elevation?}
    GET    /api/projects/<id>/terrain
    POST   /api/projects/<id>/terrain/brush   {operation, center_x_m, center_y_m, radius_m?, strength?, amount_m?, target_elevation?, iterations?, seed?, paint_weight?}
    POST   /api/projects/<id>/terrain/undo
    POST   /api/projects/<id>/terrain/redo
    POST   /api/projects/<id>/hazard/terrain-summary   {rainfall_mm_24h?}
    POST   /api/projects/<id>/hazard/terrain-point     {x_m, y_m, rainfall_mm_24h?}
    POST   /api/projects/<id>/hazard/terrain-top-cells {kind?(landslide|flood), limit?, rainfall_mm_24h?}
    POST   /api/projects/<id>/energy/solar-feasibility {lat, lon, key?, roof_area_m2?, roof_tilt_deg?, roof_azimuth_deg?, panel_efficiency?, performance_ratio?, usable_roof_fraction?, year?}
    POST   /api/projects/<id>/regulatory/permit-precheck {key, plot_points?[[x,y],...], min_setback_m?, max_floor_count?, max_height_m?, profile_name?}
    GET    /api/regulatory/profiles
    POST   /api/projects/<id>/energy/envelope-audit    {key, climate_zone?(1-4), u_wall?, u_window?, u_roof?, u_floor?}
    POST   /api/projects/<id>/energy/monthly-balance    {key, monthly_mean_external_temp_c[12], monthly_solar_gain_kwh[12], monthly_internal_gain_kwh[12], climate_zone?, indoor_temp_c?, u_wall?, u_window?, u_roof?, u_floor?, air_changes_per_hour?}
    GET    /api/projects/<id>/roads
    POST   /api/projects/<id>/roads           {road_id?, width_m?, elevation_z?, name?}
    DELETE /api/projects/<id>/roads/<road_id>
    POST   /api/projects/<id>/roads/<road_id>/points        {x_m, y_m, index?}
    PUT    /api/projects/<id>/roads/<road_id>/points/<index> {x_m, y_m}
    DELETE /api/projects/<id>/roads/<road_id>/points/<index>
    POST   /api/projects/<id>/roads/<road_id>/width          {width_m}
    POST   /api/projects/<id>/roads/<road_id>/undo
    POST   /api/projects/<id>/roads/<road_id>/redo

    Roadmap V4 - Faz E10 (gercek GPU zamanlama koprusu):
    POST   /api/performance/gpu-timing   {gpu_time_ms, supported}
    GET    /api/performance/gpu-timing

    Roadmap Faz 4.1 (AI ayarları paneli):
    GET    /api/ai/config            (maskelenmiş özet döner, anahtar asla düz metin dönmez)
    POST   /api/projects/<id>/feature-survey/uploads  (multipart/form-data, alan adı serbest,
                                                         her parça bir dosya) -> gerçek tarayıcıdan
                                                         sunucuya dosya yükleme (bkz. app_shell/server.py
                                                         _dispatch_upload + app_shell/multipart.py).
                                                         JSON router DIŞINDA, ham HTTP gövdesi olarak
                                                         işlenir çünkü JSON değildir.
    POST   /api/ai/config            {backend: gguf|openai|anthropic, ...saglayici alanlari}
    POST   /api/ai/config/clear
    POST   /api/ai/test-connection   (yapılandırılmış sağlayıcıya gerçek küçük bir istek atar)
"""

from __future__ import annotations

from typing import Any, Optional

from ..extensibility.rest_api import RestResponse, RestRouter
from ..observability.metrics import MetricsRegistry
from ..performance.profiler import GPUProfiler
from ..security.rate_limiter import SlidingWindowRateLimiter
from ..digital_twin.iot_bridge import MqttBackendUnavailable
from .session import AppSession, AppSessionError


def build_app_router(
    session: AppSession,
    metrics: Optional[MetricsRegistry] = None,
    gpu_profiler: Optional[GPUProfiler] = None,
) -> RestRouter:
    router = RestRouter()

    def _err(exc: AppSessionError) -> RestResponse:
        return RestResponse(status=400, body={"error": str(exc)})

    # Faz 5.4: harici Overpass API'sine giden çağrılar sınırlanır (OWASP
    # API4:2023 - Unrestricted Resource Consumption); anahtar proje id'si -
    # bir proje kısa sürede çok sayıda gerçek Overpass sorgusu tetikleyemez.
    _osm_import_limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60.0)

    @router.get("/api/health")
    def _health(**_: Any) -> dict[str, Any]:
        return {"status": "ok"}

    # -- Roadmap V4 - Faz E15: Observability ------------------------------
    if metrics is not None:
        @router.get("/api/metrics")
        def _metrics(**_: Any) -> RestResponse:
            body = metrics.render_prometheus()
            return RestResponse(
                status=200, body=body,
                headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
            )


    # -- Roadmap V4 - Faz E10: gercek GPU donanim zamanlama koprusu --------
    if gpu_profiler is not None:
        @router.post("/api/performance/gpu-timing")
        def _report_gpu_timing(body: Any = None, **_: Any) -> RestResponse:
            body = body or {}
            try:
                gpu_time_ms = float(body["gpu_time_ms"])
            except (KeyError, TypeError, ValueError):
                return RestResponse(status=422, body={"error": "gpu_time_ms sayisal olmali"})
            supported = bool(body.get("supported", True))
            gpu_profiler.record_gpu_timing(gpu_time_ms, supported=supported)
            return RestResponse(status=200, body={"recorded": True})

        @router.get("/api/performance/gpu-timing")
        def _get_gpu_timing(**_: Any) -> dict[str, Any]:
            avg = gpu_profiler.average_gpu_time_ms()
            return {
                "average_gpu_time_ms": avg,
                "hardware_timing_supported": avg is not None,
                "frame_count": len(gpu_profiler.history),
            }

    @router.get("/api/projects")
    def _list_projects(query: dict | None = None, **_: Any) -> dict[str, Any]:
        return {"projects": session.list_projects()}

    @router.post("/api/projects")
    def _create_project(body: Any = None, **_: Any) -> RestResponse:
        body = body or {}
        try:
            info = session.create_project(name=body["name"], path=body["path"])
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=201, body=info)

    @router.post("/api/projects/<id>/open")
    def _open_project(id: str, body: Any = None, query: dict | None = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        path = body.get("path") or (query or {}).get("path")
        try:
            info = session.open_project(project_id=id, path=path)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=info)

    @router.post("/api/projects/<id>/close")
    def _close_project(id: str, **_: Any) -> dict[str, Any]:  # noqa: A002
        session.close_project(id)
        return {"closed": True}

    @router.post("/api/projects/<id>/save")
    def _save_project(id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            info = session.save_project(id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=info)

    @router.get("/api/projects/<id>/buildings")
    def _list_buildings(id: str, **_: Any) -> dict[str, Any]:  # noqa: A002
        return {"buildings": session.list_buildings(id)}

    @router.post("/api/projects/<id>/buildings")
    def _add_building(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            # Roadmap V3 - D19: `body["polygon"]` bir dize, sayı, None ya da
            # her elemanı 2'li olmayan bir liste olabilir - `tuple(p)` bu
            # durumların çoğunda `TypeError`/`ValueError` fırlatır; bunlar
            # artık 422 olarak (kullanıcıya okunur, sunucu 500'üne
            # düşürülmeden) döndürülüyor.
            raw_polygon = body["polygon"]
            if not isinstance(raw_polygon, list):
                return RestResponse(status=422, body={"error": "polygon bir liste olmalı."})
            points = [tuple(p) for p in raw_polygon]
            info = session.add_building(
                id,
                points,
                building_type=body.get("building_type", "apartman"),
                floor_count=body.get("floor_count"),
                height_m=body.get("height_m"),
                seed=body.get("seed"),
                name=body.get("name"),
                basement_floor_count=body.get("basement_floor_count", 0),
                token=body.get("token"),
                generate_interior=bool(body.get("generate_interior", False)),
                min_room_size=body.get("min_room_size"),
                window_spacing=body.get("window_spacing"),
            )
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except (TypeError, ValueError) as exc:
            return RestResponse(status=422, body={"error": f"geçersiz polygon verisi: {exc}"})
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=201, body=info)

    @router.post("/api/projects/<id>/buildings/<key>/regenerate_interior")
    def _regenerate_interior(id: str, key: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        # Netleştirme: bu, /ai/interior'dan (yalnızca metin oda listesi
        # döndürür) farklıdır — bu route gerçekten binanın 3D mesh'ine
        # oda duvarı/merdiven/asansör/mobilya ekler (bkz. AppSession.
        # regenerate_interior / ProceduralBuildingGenerator generate_interior).
        body = body or {}
        try:
            result = session.regenerate_interior(
                id, key,
                seed=body.get("seed"),
                min_room_size=body.get("min_room_size"),
                window_spacing=body.get("window_spacing"),
                token=body.get("token"),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/buildings/<key>/structural-validate")
    def _structural_validate(id: str, key: str, **_: Any) -> RestResponse:  # noqa: A002
        """Faz 1.5 — basit fiziksel tutarlılık raporu (gösterge niteliğinde,
        kesin mühendislik raporu değildir)."""
        try:
            return RestResponse(status=200, body=session.validate_structure(id, key))
        except AppSessionError as exc:
            return _err(exc)

    @router.delete("/api/projects/<id>/buildings/<key>")
    def _remove_building(id: str, key: str, query: dict | None = None, **_: Any) -> RestResponse | dict[str, Any]:  # noqa: A002
        token = (query or {}).get("token")
        try:
            removed = session.remove_building(id, key, token=token)
        except AppSessionError as exc:
            return _err(exc)
        return {"removed": removed}

    @router.post("/api/projects/<id>/buildings/<key>/add_floor")
    def _add_floor(id: str, key: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        token = (body or {}).get("token")
        try:
            return RestResponse(status=200, body=session.add_floor(id, key, token=token))
        except AppSessionError as exc:
            return _err(exc)

    @router.post("/api/projects/<id>/buildings/<key>/remove_floor")
    def _remove_floor(id: str, key: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        token = (body or {}).get("token")
        try:
            return RestResponse(status=200, body=session.remove_floor(id, key, token=token))
        except AppSessionError as exc:
            return _err(exc)

    @router.post("/api/projects/<id>/buildings/<key>/undo")
    def _undo(id: str, key: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        token = (body or {}).get("token")
        try:
            return RestResponse(status=200, body=session.undo(id, key, token=token))
        except AppSessionError as exc:
            return _err(exc)

    @router.post("/api/projects/<id>/buildings/<key>/redo")
    def _redo(id: str, key: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        token = (body or {}).get("token")
        try:
            return RestResponse(status=200, body=session.redo(id, key, token=token))
        except AppSessionError as exc:
            return _err(exc)

    @router.post("/api/projects/<id>/buildings/<key>/assistant")
    def _assistant(id: str, key: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        text = body.get("text", "")
        try:
            return RestResponse(status=200, body=session.run_assistant_command(id, key, text, token=body.get("token")))
        except AppSessionError as exc:
            return _err(exc)

    @router.get("/api/projects/<id>/scene")
    def _scene(id: str, **_: Any) -> dict[str, Any]:  # noqa: A002
        return session.scene_json(id)

    @router.get("/api/projects/<id>/section")
    def _section(id: str, query: dict | None = None, **_: Any) -> RestResponse | dict[str, Any]:  # noqa: A002
        query = query or {}
        axis = str(query.get("axis", "x"))
        try:
            offset = float(query.get("offset", 0.0))
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "offset bir sayı olmalı."})
        keep_positive = str(query.get("keep_positive", "1")) not in ("0", "false", "False")
        try:
            return session.section_view_scene(id, axis, offset, keep_positive=keep_positive)
        except AppSessionError as exc:
            return _err(exc)

    @router.get("/api/projects/<id>/explosion")
    def _explosion(id: str, query: dict | None = None, **_: Any) -> RestResponse | dict[str, Any]:  # noqa: A002
        query = query or {}
        building_key = query.get("building")
        if not building_key:
            return RestResponse(status=422, body={"error": "eksik alan: building"})
        try:
            progress = float(query.get("progress", 1.0))
            gap = float(query.get("gap", 2.0))
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "progress/gap sayı olmalı."})
        try:
            return session.explosion_view_scene(id, str(building_key), progress, gap_m=gap)
        except AppSessionError as exc:
            return _err(exc)

    @router.post("/api/projects/<id>/measure")
    def _measure(id: str, body: Any = None, **_: Any) -> RestResponse | dict[str, Any]:  # noqa: A002
        body = body or {}
        tool = body.get("tool")
        points = body.get("points")
        if not tool or not isinstance(tool, str):
            return RestResponse(status=422, body={"error": "eksik/geçersiz alan: tool"})
        if not isinstance(points, list) or not all(isinstance(p, (list, tuple)) for p in points):
            return RestResponse(status=422, body={"error": "points, [[x,y,z],...] biçiminde bir liste olmalı"})
        try:
            return session.measure(id, tool, points)
        except (AppSessionError, ValueError, TypeError) as exc:
            return _err(exc) if isinstance(exc, AppSessionError) else RestResponse(status=422, body={"error": str(exc)})

    @router.get("/api/projects/<id>/facade-compliance/narrate")
    def _narrate_facade_compliance(id: str, query: dict | None = None, **_: Any) -> RestResponse | dict[str, Any]:  # noqa: A002
        query = query or {}
        building_key = query.get("building")
        if not building_key:
            return RestResponse(status=422, body={"error": "eksik alan: building"})
        try:
            return session.narrate_facade_compliance(id, str(building_key))
        except AppSessionError as exc:
            return _err(exc)

    @router.get("/api/projects/<id>/history")
    def _history(id: str, query: dict | None = None, **_: Any) -> RestResponse | dict[str, Any]:  # noqa: A002
        raw_limit = (query or {}).get("limit", 50)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "limit bir tam sayı olmalı."})
        if limit < 0:
            return RestResponse(status=422, body={"error": "limit negatif olamaz."})
        try:
            return {"history": session.history(id, limit=limit)}
        except AppSessionError as exc:
            return {"history": [], "error": str(exc)}

    @router.post("/api/projects/<id>/import")
    def _import(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        geojson_text = body.get("geojson")
        if not geojson_text:
            return RestResponse(status=422, body={"error": "eksik alan: geojson"})
        if not isinstance(geojson_text, str):
            return RestResponse(status=422, body={"error": "geojson bir metin (str) olmalı."})
        try:
            result = session.import_geojson(id, geojson_text, seed=body.get("seed"), token=body.get("token"))
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=201, body=result)

    @router.post("/api/projects/<id>/osm/import")
    def _osm_import(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        # Roadmap Faz 2 - "haritadan bbox seç -> gerçek OSM -> gerçek bina"
        # uçtan uca akışının HTTP ucu (bkz. AppSession.import_osm_bbox).
        body = body or {}
        required = ("south", "west", "north", "east")
        missing = [k for k in required if k not in body]
        if missing:
            return RestResponse(status=422, body={"error": f"eksik alan(lar): {', '.join(missing)}"})
        try:
            south = float(body["south"])
            west = float(body["west"])
            north = float(body["north"])
            east = float(body["east"])
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "south/west/north/east sayısal olmalı."})
        seed = body.get("seed")
        osm_ids_raw = body.get("osm_ids")
        osm_ids: list[int] | None = None
        if osm_ids_raw is not None:
            if not isinstance(osm_ids_raw, list):
                return RestResponse(status=422, body={"error": "osm_ids bir liste olmalı."})
            try:
                osm_ids = [int(v) for v in osm_ids_raw]
            except (TypeError, ValueError):
                return RestResponse(status=422, body={"error": "osm_ids içindeki değerler tam sayı olmalı."})
            if not osm_ids:
                return RestResponse(status=422, body={"error": "osm_ids boş olamaz (hiç bina seçilmedi)."})
        if not _osm_import_limiter.allow(id):
            retry_after = _osm_import_limiter.retry_after(id)
            return RestResponse(
                status=429,
                body={"error": f"çok fazla OSM içe aktarma isteği; {retry_after:.0f} saniye sonra tekrar deneyin."},
                headers={"Content-Type": "application/json", "Retry-After": str(int(retry_after) + 1)},
            )
        try:
            result = session.import_osm_bbox(
                id, south, west, north, east, seed=seed, token=body.get("token"), osm_ids=osm_ids,
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=201, body=result)

    # -- Faz 3.x genişletmesi: tek tek bina seçimi -------------------------
    # Haritada bbox'a yakınlaşıldığında binaları tek tek tıklayıp
    # seçebilmek için: bbox içindeki binaları PROJEYE EKLEMEDEN, salt-okunur
    # olarak (osm_id + poligon) döndürür. Frontend bunları Leaflet
    # poligonları olarak çizer; kullanıcı istediği kadarını tıklayıp
    # seçtikten sonra yalnızca o osm_id'lerle /osm/import çağrılır.
    _osm_preview_limiter = SlidingWindowRateLimiter(max_requests=20, window_seconds=60.0)

    @router.post("/api/projects/<id>/osm/preview")
    def _osm_preview(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        required = ("south", "west", "north", "east")
        missing = [k for k in required if k not in body]
        if missing:
            return RestResponse(status=422, body={"error": f"eksik alan(lar): {', '.join(missing)}"})
        try:
            south = float(body["south"])
            west = float(body["west"])
            north = float(body["north"])
            east = float(body["east"])
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "south/west/north/east sayısal olmalı."})
        if not _osm_preview_limiter.allow(id):
            retry_after = _osm_preview_limiter.retry_after(id)
            return RestResponse(
                status=429,
                body={"error": f"çok fazla OSM önizleme isteği; {retry_after:.0f} saniye sonra tekrar deneyin."},
                headers={"Content-Type": "application/json", "Retry-After": str(int(retry_after) + 1)},
            )
        try:
            result = session.preview_osm_bbox(id, south, west, north, east)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # ROADMAP_V7.md Faz C6 (A2/B5): "katman bazlı istatistik" önizlemesi -
    # bina dışı kategoriler (yol/ağaç/su/dini yapı/...) için, bir bbox'ı
    # projeye eklemeden önce "Bu bölgede: N ağaç, M yol..." özetini
    # döndürür. `preview_osm_bbox` ile aynı rate-limit disiplini.
    _osm_category_summary_limiter = SlidingWindowRateLimiter(max_requests=20, window_seconds=60.0)

    @router.post("/api/projects/<id>/osm/category-summary")
    def _osm_category_summary(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        required = ("south", "west", "north", "east")
        missing = [k for k in required if k not in body]
        if missing:
            return RestResponse(status=422, body={"error": f"eksik alan(lar): {', '.join(missing)}"})
        try:
            south = float(body["south"])
            west = float(body["west"])
            north = float(body["north"])
            east = float(body["east"])
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "south/west/north/east sayısal olmalı."})
        categories = body.get("categories")
        if categories is not None and not isinstance(categories, list):
            return RestResponse(status=422, body={"error": "categories bir liste olmalı."})
        if not _osm_category_summary_limiter.allow(id):
            retry_after = _osm_category_summary_limiter.retry_after(id)
            return RestResponse(
                status=429,
                body={"error": f"çok fazla katman özeti isteği; {retry_after:.0f} saniye sonra tekrar deneyin."},
                headers={"Content-Type": "application/json", "Retry-After": str(int(retry_after) + 1)},
            )
        try:
            result = session.osm_category_summary(id, south, west, north, east, categories=categories)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # ROADMAP_V7.md Faz C6 (2. dilim) — B5'in "kullanıcı sadece 'Yollar +
    # Ağaçlar' seçip bbox import edebilmeli" maddesinin GERÇEK (yalnızca
    # önizleme değil) karşılığı: seçilen kategoriler projeye gerçekten
    # `scene_prop` olarak eklenir (bkz. AppSession.import_osm_categories).
    # `/osm/import` (binalar) ile aynı rate-limit disiplini (yazma ucu).
    _osm_import_layers_limiter = SlidingWindowRateLimiter(max_requests=20, window_seconds=60.0)

    @router.post("/api/projects/<id>/osm/import-layers")
    def _osm_import_layers(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        required = ("south", "west", "north", "east", "categories")
        missing = [k for k in required if k not in body]
        if missing:
            return RestResponse(status=422, body={"error": f"eksik alan(lar): {', '.join(missing)}"})
        try:
            south = float(body["south"])
            west = float(body["west"])
            north = float(body["north"])
            east = float(body["east"])
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "south/west/north/east sayısal olmalı."})
        categories = body.get("categories")
        if not isinstance(categories, list):
            return RestResponse(status=422, body={"error": "categories bir liste olmalı."})
        if not _osm_import_layers_limiter.allow(id):
            retry_after = _osm_import_layers_limiter.retry_after(id)
            return RestResponse(
                status=429,
                body={"error": f"çok fazla katman içe aktarma isteği; {retry_after:.0f} saniye sonra tekrar deneyin."},
                headers={"Content-Type": "application/json", "Retry-After": str(int(retry_after) + 1)},
            )
        try:
            result = session.import_osm_categories(
                id, south, west, north, east, categories, token=body.get("token"),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=201, body=result)

    @router.get("/api/projects/<id>/scene-props")
    def _scene_props_list(id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.list_scene_props(id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body={"scene_props": result, "count": len(result)})

    @router.post("/api/projects/<id>/scene-props/<key>/delete")
    def _scene_prop_delete(id: str, key: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            existed = session.remove_scene_prop(id, key, token=body.get("token"))
        except AppSessionError as exc:
            return _err(exc)
        if not existed:
            return RestResponse(status=404, body={"error": f"scene_prop bulunamadı: {key}"})
        return RestResponse(status=200, body={"deleted": True, "key": key})

    @router.post("/api/projects/<id>/export")
    def _export(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        fmt = body.get("format")
        if not fmt:
            return RestResponse(status=422, body={"error": "eksik alan: format"})
        if not isinstance(fmt, str):
            return RestResponse(status=422, body={"error": "format bir metin (str) olmalı."})
        try:
            result = session.export_scene(id, fmt, out_dir=body.get("out_dir"))
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Güneş Simülasyonu (analysis_engine) ----
    @router.post("/api/projects/<id>/analysis/sun")
    def _sun_analysis(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            lat = float(body["lat"])
            lon = float(body["lon"])
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "lat/lon sayısal olmalı."})
        try:
            result = session.sun_analysis(
                id,
                lat=lat,
                lon=lon,
                year=body.get("year"),
                roof_tilt_deg=float(body.get("roof_tilt_deg", 0.0)),
                roof_azimuth_deg=float(body.get("roof_azimuth_deg", 180.0)),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Görünürlük/Gölge Analizi (analysis_engine) --
    @router.post("/api/projects/<id>/analysis/visibility")
    def _visibility_analysis(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            observer_x = float(body["observer_x"])
            observer_y = float(body["observer_y"])
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "observer_x/observer_y sayısal olmalı."})
        try:
            result = session.visibility_analysis(
                id,
                observer_x=observer_x,
                observer_y=observer_y,
                observer_z=float(body.get("observer_z", 1.7)),
                scan_radius=float(body.get("scan_radius", 100.0)),
                angle_step_deg=float(body.get("angle_step_deg", 10.0)),
                lat=body.get("lat"),
                lon=body.get("lon"),
                when_iso=body.get("when_iso"),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Bitki Örtüsü (vegetation) --------------
    @router.post("/api/projects/<id>/vegetation/scatter")
    def _vegetation_scatter(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.vegetation_scatter(
                id,
                target_count=int(body.get("target_count", 40)),
                seed=int(body.get("seed", 0)),
                species=body.get("species", "generic"),
                slope_penalty=float(body.get("slope_penalty", 3.0)),
                max_slope=float(body.get("max_slope", 0.9)),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.post("/api/projects/<id>/vegetation/clear")
    def _vegetation_clear(id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.vegetation_clear(id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Feature Survey (saha ölçümü) -----------
    @router.post("/api/projects/<id>/feature-survey/import")
    def _feature_survey_import(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.feature_survey_import_penzd(
                id,
                csv_text=body.get("csv_text", ""),
                has_header=bool(body.get("has_header", True)),
                instrument=body.get("instrument", "total_station"),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/feature-survey/summary")
    def _feature_survey_summary(id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.feature_survey_summary(id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/feature-survey/geojson")
    def _feature_survey_geojson(id: str, query: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        query = query or {}
        try:
            origin_lat = query.get("origin_lat")
            origin_lon = query.get("origin_lon")
            result = session.feature_survey_geojson(
                id,
                origin_lat=float(origin_lat) if origin_lat not in (None, "") else None,
                origin_lon=float(origin_lon) if origin_lon not in (None, "") else None,
                origin_elevation=float(query.get("origin_elevation", 0.0) or 0.0),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.post("/api/feature-survey/webodm/test-connection")
    def _feature_survey_webodm_test(body: Any = None, **_: Any) -> RestResponse:
        body = body or {}
        try:
            result = session.feature_survey_webodm_test_connection(
                base_url=body.get("base_url", ""), token=body.get("token"),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.post("/api/projects/<id>/feature-survey/webodm/submit")
    def _feature_survey_webodm_submit(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.feature_survey_webodm_submit(
                id,
                base_url=body.get("base_url", ""),
                image_dir=body.get("image_dir", ""),
                token=body.get("token"),
                task_name=body.get("task_name"),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/feature-survey/webodm/status")
    def _feature_survey_webodm_status(id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.feature_survey_webodm_status(id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.post("/api/projects/<id>/feature-survey/webodm/fetch")
    def _feature_survey_webodm_fetch(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.feature_survey_webodm_fetch(id, output_dir=body.get("output_dir", ""))
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- ROADMAP_V6 FAZ S6: uçtan uca orkestrasyon (S3+S4+S5) + kalıcı kayıt
    @router.post("/api/projects/<id>/feature-survey/orchestrate")
    def _feature_survey_orchestrate(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.feature_survey_run_orchestration(
                id,
                checkpoint_comparisons=body.get("checkpoint_comparisons"),
                checkpoint_tolerance_horizontal_m=body.get("checkpoint_tolerance_horizontal_m"),
                checkpoint_tolerance_vertical_m=body.get("checkpoint_tolerance_vertical_m"),
                checkpoint_standard_reference=body.get("checkpoint_standard_reference"),
                angular_closure=body.get("angular_closure"),
                linear_closure=body.get("linear_closure"),
                angular_tolerance_gon=body.get("angular_tolerance_gon"),
                max_relative_precision=body.get("max_relative_precision"),
                closure_standard_reference=body.get("closure_standard_reference"),
                pointcloud_points=body.get("pointcloud_points"),
                persist=bool(body.get("persist", True)),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/feature-survey/orchestrate")
    def _feature_survey_orchestration_result(id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.feature_survey_orchestration_result(id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Navigasyon/Yol Bulma (mobility) --------
    @router.post("/api/projects/<id>/mobility/path")
    def _mobility_path(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            start_x = float(body["start_x"])
            start_y = float(body["start_y"])
            goal_x = float(body["goal_x"])
            goal_y = float(body["goal_y"])
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "start_x/start_y/goal_x/goal_y sayısal olmalı."})
        try:
            result = session.find_path(
                id, start_x=start_x, start_y=start_y, goal_x=goal_x, goal_y=goal_y,
                cell_size=float(body.get("cell_size", 2.0)),
                algorithm=body.get("algorithm", "astar"),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Fikir 9 — Trafik/Yaya Etki Senaryosu ---
    @router.post("/api/projects/<id>/mobility/traffic-impact")
    def _mobility_traffic_impact(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.traffic_impact_scenario(
                id,
                lane_count=int(body.get("lane_count", 2)),
                free_flow_speed_kmh=float(body.get("free_flow_speed_kmh", 50.0)),
                jam_density_veh_km_per_lane=float(body.get("jam_density_veh_km_per_lane", 140.0)),
                baseline_daily_trips=float(body.get("baseline_daily_trips", 8000.0)),
                added_daily_trips=float(body.get("added_daily_trips", 0.0)),
                peak_hour_factor=float(body.get("peak_hour_factor", 0.09)),
                pedestrian_count=int(body.get("pedestrian_count", 0)),
                site_x=float(body.get("site_x", 0.0)),
                site_y=float(body.get("site_y", 0.0)),
                area_radius_m=float(body.get("area_radius_m", 60.0)),
                seed=body.get("seed"),
            )
        except AppSessionError as exc:
            return _err(exc)
        except (TypeError, ValueError) as exc:
            return RestResponse(status=422, body={"error": f"parametreler sayısal olmalı: {exc}"})
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Afet/Deprem Risk Modülü (hazard_data) --
    @router.post("/api/projects/<id>/hazard/earthquakes")
    def _hazard_earthquakes(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.hazard_earthquake_catalog(
                id,
                min_lat=float(body["min_lat"]), max_lat=float(body["max_lat"]),
                min_lon=float(body["min_lon"]), max_lon=float(body["max_lon"]),
                source=body.get("source", "usgs"),
                min_magnitude=float(body.get("min_magnitude", 2.5)),
                days=int(body.get("days", 30)),
            )
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "min_lat/max_lat/min_lon/max_lon sayısal olmalı."})
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.post("/api/projects/<id>/hazard/pga")
    def _hazard_pga(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            lat = float(body["lat"])
            lon = float(body["lon"])
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "lat/lon sayısal olmalı."})
        try:
            result = session.hazard_pga_estimate(id, lat=lat, lon=lon)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.post("/api/projects/<id>/hazard/building-risk")
    def _hazard_building_risk(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            lat = float(body["lat"])
            lon = float(body["lon"])
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "lat/lon sayısal olmalı."})
        try:
            result = session.hazard_building_risk(
                id, lat=lat, lon=lon,
                key=body.get("key"),
                construction_year=body.get("construction_year"),
                floor_count=body.get("floor_count"),
                soil_type=body.get("soil_type", "bilinmiyor"),
                pga_g_override=body.get("pga_g_override"),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.post("/api/projects/<id>/hazard/evacuation-plan")
    def _hazard_evacuation_plan(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        buildings = body.get("buildings") or []
        safe_points = body.get("safe_points") or []
        if not buildings:
            return RestResponse(status=422, body={"error": "en az bir bina (buildings) gerekli."})
        try:
            result = session.hazard_evacuation_plan(id, buildings=buildings, safe_points=safe_points)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Roadmap V9 / Faz I O.3 + Faz II — Simülasyon Senaryosu ve
    #    Agent-Bazlı Deprem Tahliye Animasyonu --

    @router.post("/api/projects/<id>/simulation/scenario")
    def _simulation_scenario_save(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.simulation_scenario_save(id, body)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/simulation/scenario/<scenario_id>")
    def _simulation_scenario_get(id: str, scenario_id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.simulation_scenario_get(id, scenario_id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.post("/api/projects/<id>/simulation/evacuation/run")
    def _simulation_evacuation_run(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        scenario_id = body.get("scenario_id")
        scenario_data = body.get("scenario")
        if not scenario_id and not scenario_data:
            return RestResponse(status=422, body={"error": "scenario_id veya scenario gerekli."})
        try:
            result = session.simulation_evacuation_run(
                id,
                scenario_id=scenario_id,
                scenario_data=scenario_data,
                safe_point=body.get("safe_point"),
                max_time_s=float(body.get("max_time_s", 600.0)),
                keyframe_interval_s=float(body.get("keyframe_interval_s", 0.5)),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/simulation/evacuation/<result_id>")
    def _simulation_evacuation_result(id: str, result_id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.simulation_evacuation_result(id, result_id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Roadmap V9 / Faz III — Katman 7.3: Bina Kapasite Batch-Runner --

    @router.post("/api/projects/<id>/simulation/capacity-analysis/run")
    def _capacity_analysis_run(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        for field_name in ("room_width_m", "room_depth_m", "exit_width_m"):
            if field_name not in body:
                return RestResponse(status=422, body={"error": f"{field_name} gerekli."})
        try:
            result = session.capacity_analysis_run(
                id,
                room_width_m=float(body["room_width_m"]),
                room_depth_m=float(body["room_depth_m"]),
                exit_width_m=float(body["exit_width_m"]),
                agent_counts=body.get("agent_counts"),
                building_type=body.get("building_type"),
                regulation_profile_name=body.get("regulation_profile_name"),
                seed=int(body.get("seed", 42)),
                max_time_s=float(body.get("max_time_s", 900.0)),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/simulation/capacity-analysis/<result_id>")
    def _capacity_analysis_result(id: str, result_id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.capacity_analysis_result(id, result_id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Roadmap V9 / Faz V — Katman 7.2: Yangın Yayılım Demosu ----------- #

    @router.post("/api/projects/<id>/simulation/fire-spread/run")
    def _fire_spread_run(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        for field_name in ("width", "height", "ignition_cells"):
            if field_name not in body:
                return RestResponse(status=422, body={"error": f"{field_name} gerekli."})
        try:
            result = session.fire_spread_demo_run(
                id,
                width=int(body["width"]),
                height=int(body["height"]),
                ignition_cells=[tuple(c) for c in body["ignition_cells"]],
                wall_cells=[tuple(c) for c in body.get("wall_cells", [])],
                door_cells=[tuple(c) for c in body.get("door_cells", [])],
                duration_s=float(body.get("duration_s", 60.0)),
                dt=float(body.get("dt", 1.0)),
                spread_rate_per_s=float(body.get("spread_rate_per_s", 0.35)),
                seed=int(body.get("seed", 42)),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/simulation/fire-spread/<result_id>")
    def _fire_spread_result(id: str, result_id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.fire_spread_demo_result(id, result_id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Roadmap V9 / Faz VII — Katman 7.1/7.4: Kademeli Etki (Cascade) --- #

    @router.post("/api/projects/<id>/simulation/cascade/run")
    def _cascade_run(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        if "magnitude" not in body:
            return RestResponse(status=422, body={"error": "magnitude gerekli."})
        try:
            result = session.cascade_demo_run(
                id,
                magnitude=float(body["magnitude"]),
                epicenter_lat=body.get("epicenter_lat"),
                epicenter_lon=body.get("epicenter_lon"),
                seed=int(body.get("seed", 42)),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Roadmap V9 / Faz VII.5 — Acil Müdahale Sevkiyatı ----------------- #

    @router.post("/api/projects/<id>/simulation/emergency-dispatch/run")
    def _emergency_dispatch_run(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        for field_name in ("stations", "incident_x", "incident_y", "unit_type"):
            if field_name not in body:
                return RestResponse(status=422, body={"error": f"{field_name} gerekli."})
        try:
            result = session.emergency_dispatch_demo_run(
                id,
                stations=body["stations"],
                incident_x=float(body["incident_x"]),
                incident_y=float(body["incident_y"]),
                unit_type=body["unit_type"],
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Roadmap V9 / Faz VIII — Katman 4: Çevre Göstergeleri (ısı adası +
    # hava kalitesi + gürültü) ------------------------------------------ #

    @router.post("/api/projects/<id>/simulation/environment/run")
    def _environment_run(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        for field_name in ("average_building_height_m", "average_street_width_m",
                            "building_footprint_ratio"):
            if field_name not in body:
                return RestResponse(status=422, body={"error": f"{field_name} gerekli."})
        try:
            result = session.environment_indicators_run(
                id,
                average_building_height_m=float(body["average_building_height_m"]),
                average_street_width_m=float(body["average_street_width_m"]),
                building_footprint_ratio=float(body["building_footprint_ratio"]),
                canopy_coverage_ratio=float(body.get("canopy_coverage_ratio", 0.0)),
                baseline_temperature_c=(
                    float(body["baseline_temperature_c"])
                    if body.get("baseline_temperature_c") is not None else None
                ),
                vehicles_per_hour=(
                    float(body["vehicles_per_hour"])
                    if body.get("vehicles_per_hour") is not None else None
                ),
                density_people_per_m2=(
                    float(body["density_people_per_m2"])
                    if body.get("density_people_per_m2") is not None else None
                ),
                road_segments=body.get("road_segments", []),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/simulation/environment/<result_id>")
    def _environment_result(id: str, result_id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.environment_indicators_result(id, result_id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Roadmap V9 / Faz VIII — Katman 4: Elektrik Kesintisi Yayılımı ---- #

    @router.post("/api/projects/<id>/simulation/power-outage/run")
    def _power_outage_run(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        for field_name in ("substation_ids", "failed_substation_ids"):
            if field_name not in body:
                return RestResponse(status=422, body={"error": f"{field_name} gerekli."})
        try:
            result = session.power_outage_demo_run(
                id,
                substation_ids=list(body["substation_ids"]),
                building_ids=list(body.get("building_ids", [])),
                edges=[tuple(e) for e in body.get("edges", [])],
                failed_substation_ids=list(body["failed_substation_ids"]),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/simulation/power-outage/<result_id>")
    def _power_outage_result(id: str, result_id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.power_outage_demo_result(id, result_id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Roadmap V9 / Faz IX — Katman 6.2: Etkinlik Simülasyonu ----------- #

    @router.post("/api/projects/<id>/simulation/city-event/run")
    def _city_event_run(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        for field_name in ("event_id", "category", "location_ref", "expected_attendance",
                            "start_hour", "duration_h"):
            if field_name not in body:
                return RestResponse(status=422, body={"error": f"{field_name} gerekli."})
        try:
            result = session.city_event_demo_run(
                id,
                event_id=body["event_id"],
                category=body["category"],
                location_ref=body["location_ref"],
                expected_attendance=int(body["expected_attendance"]),
                start_hour=float(body["start_hour"]),
                duration_h=float(body["duration_h"]),
                ramp_fraction=float(body.get("ramp_fraction", 0.15)),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/simulation/city-event/<result_id>")
    def _city_event_result(id: str, result_id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.city_event_demo_result(id, result_id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Roadmap V9 / Faz IX — Katman 6.3: Ekonomik Dayanıklılık ---------- #

    @router.post("/api/projects/<id>/simulation/economic-resilience/run")
    def _economic_resilience_run(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        for field_name in ("area_id", "risk_level"):
            if field_name not in body:
                return RestResponse(status=422, body={"error": f"{field_name} gerekli."})
        try:
            result = session.economic_resilience_demo_run(
                id,
                area_id=body["area_id"],
                risk_level=body["risk_level"],
                horizon_days=(
                    float(body["horizon_days"]) if body.get("horizon_days") is not None else None
                ),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/simulation/economic-resilience/<result_id>")
    def _economic_resilience_result(id: str, result_id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.economic_resilience_demo_result(id, result_id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Roadmap V9 / Faz X — Katman 8.1 madde 3: Bölgesel Yığılma Tespiti - #

    @router.post("/api/projects/<id>/simulation/regional-congestion/run")
    def _regional_congestion_run(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        for field_name in ("hierarchy_edges", "agent_counts_by_leaf", "candidate_region_ids"):
            if field_name not in body:
                return RestResponse(status=422, body={"error": f"{field_name} gerekli."})
        try:
            result = session.regional_congestion_demo_run(
                id,
                hierarchy_edges=body["hierarchy_edges"],
                agent_counts_by_leaf={str(k): int(v) for k, v in body["agent_counts_by_leaf"].items()},
                candidate_region_ids=list(body["candidate_region_ids"]),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/simulation/regional-congestion/<result_id>")
    def _regional_congestion_result(id: str, result_id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.regional_congestion_demo_result(id, result_id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Roadmap V9 / Faz XI — Katman 9: Karar Destek / Senaryo Karşılaştırma #

    @router.post("/api/projects/<id>/simulation/scenario-comparison/run")
    def _scenario_comparison_run(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        for field_name in ("before_result_id", "after_result_id"):
            if field_name not in body:
                return RestResponse(status=422, body={"error": f"{field_name} gerekli."})
        try:
            result = session.scenario_comparison_demo_run(
                id,
                before_result_id=body["before_result_id"],
                after_result_id=body["after_result_id"],
                label=body.get("label", "tahliye senaryosu"),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/simulation/scenario-comparison/<result_id>")
    def _scenario_comparison_result(id: str, result_id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.scenario_comparison_demo_result(id, result_id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Roadmap V9 / Faz VI — Katman 2.1: Sentetik Nüfus ----------------- #

    @router.post("/api/projects/<id>/simulation/synthetic-population/run")
    def _synthetic_population_run(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        for field_name in ("building_ref", "household_count"):
            if field_name not in body:
                return RestResponse(status=422, body={"error": f"{field_name} gerekli."})
        try:
            result = session.synthetic_population_demo_run(
                id,
                building_ref=body["building_ref"],
                household_count=int(body["household_count"]),
                avg_household_size=float(body.get("avg_household_size", 2.6)),
                seed=int(body["seed"]) if body.get("seed") is not None else 42,
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/simulation/synthetic-population/<result_id>")
    def _synthetic_population_result(id: str, result_id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.synthetic_population_demo_result(id, result_id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Güneş Paneli Fizibilitesi (sun_simulation) --
    @router.post("/api/projects/<id>/energy/solar-feasibility")
    def _energy_solar_feasibility(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            lat = float(body["lat"])
            lon = float(body["lon"])
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "lat/lon sayısal olmalı."})
        try:
            result = session.solar_feasibility(
                id, lat=lat, lon=lon,
                key=body.get("key"),
                roof_area_m2=body.get("roof_area_m2"),
                roof_tilt_deg=body.get("roof_tilt_deg"),
                roof_azimuth_deg=body.get("roof_azimuth_deg", 180.0),
                panel_efficiency=body.get("panel_efficiency", 0.20),
                performance_ratio=body.get("performance_ratio", 0.80),
                usable_roof_fraction=body.get("usable_roof_fraction", 0.70),
                year=body.get("year"),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: İmar Ön-Kontrol / Ruhsat Simülasyonu ---
    @router.post("/api/projects/<id>/regulatory/permit-precheck")
    def _regulatory_permit_precheck(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        key = body.get("key")
        if not key:
            return RestResponse(status=422, body={"error": "eksik alan: key"})
        plot_points = body.get("plot_points")
        try:
            result = session.permit_precheck(
                id, str(key),
                plot_points=plot_points,
                min_setback_m=float(body.get("min_setback_m", 3.0)),
                max_floor_count=(int(body["max_floor_count"]) if body.get("max_floor_count") not in (None, "") else None),
                max_height_m=(float(body["max_height_m"]) if body.get("max_height_m") not in (None, "") else None),
                profile_name=body.get("profile_name"),
            )
        except AppSessionError as exc:
            return _err(exc)
        except (TypeError, ValueError, KeyError):
            return RestResponse(status=422, body={"error": "min_setback_m/max_floor_count/max_height_m sayısal olmalı, plot_points [[x,y],...] biçiminde olmalı."})
        return RestResponse(status=200, body=result)

    @router.get("/api/regulatory/profiles")
    def _regulatory_profiles(**_: Any) -> RestResponse:
        from ..building_reconstruction import available_regulation_profiles
        return RestResponse(status=200, body={"profiles": available_regulation_profiles()})

    # -- Web arayüzü genişletmesi: Bina Enerji Kabuğu Denetimi (TS 825) ---
    @router.post("/api/projects/<id>/energy/envelope-audit")
    def _energy_envelope_audit(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        key = body.get("key")
        if not key:
            return RestResponse(status=422, body={"error": "eksik alan: key"})
        try:
            result = session.energy_envelope_audit(
                id, str(key),
                climate_zone=int(body.get("climate_zone", 2)),
                u_wall=body.get("u_wall"), u_window=body.get("u_window"),
                u_roof=body.get("u_roof"), u_floor=body.get("u_floor"),
            )
        except AppSessionError as exc:
            return _err(exc)
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "climate_zone/U değerleri sayısal olmalı."})
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Aylık ısı denge yöntemi (EN ISO 13790) -
    @router.post("/api/projects/<id>/energy/monthly-balance")
    def _energy_envelope_monthly_balance(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        key = body.get("key")
        if not key:
            return RestResponse(status=422, body={"error": "eksik alan: key"})
        temps = body.get("monthly_mean_external_temp_c")
        solar = body.get("monthly_solar_gain_kwh")
        internal = body.get("monthly_internal_gain_kwh")
        if not (isinstance(temps, list) and isinstance(solar, list) and isinstance(internal, list)):
            return RestResponse(status=422, body={
                "error": (
                    "eksik/hatalı alan: monthly_mean_external_temp_c, "
                    "monthly_solar_gain_kwh, monthly_internal_gain_kwh "
                    "(her biri 12 elemanlı sayı listesi olmalı)"
                ),
            })
        try:
            result = session.energy_envelope_monthly_balance(
                id, str(key),
                monthly_mean_external_temp_c=[float(x) for x in temps],
                monthly_solar_gain_kwh=[float(x) for x in solar],
                monthly_internal_gain_kwh=[float(x) for x in internal],
                climate_zone=int(body.get("climate_zone", 2)),
                indoor_temp_c=float(body.get("indoor_temp_c", 20.0)),
                u_wall=body.get("u_wall"), u_window=body.get("u_window"),
                u_roof=body.get("u_roof"), u_floor=body.get("u_floor"),
                air_changes_per_hour=body.get("air_changes_per_hour"),
            )
        except AppSessionError as exc:
            return _err(exc)
        except (TypeError, ValueError):
            return RestResponse(status=422, body={
                "error": "climate_zone/indoor_temp_c/U/aylık diziler sayısal olmalı.",
            })
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Fizik (deprem/sarsıntı stabilite) ------
    @router.post("/api/projects/<id>/physics/tower-test")
    def _physics_tower_test(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.physics_tower_test(
                id,
                num_blocks=int(body.get("num_blocks", 4)),
                peak_acceleration_g=float(body.get("peak_acceleration_g", 0.3)),
                frequency_hz=float(body.get("frequency_hz", 1.5)),
                duration_s=float(body.get("duration_s", 6.0)),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Roadmap V10 / Faz 3.A+3.B — Bina Sallanması
    @router.post("/api/projects/<id>/buildings/<key>/shake/simulate")
    def _building_shake_simulate(id: str, key: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.building_shake_simulate(
                id, key,
                mode=str(body.get("mode", "standard")),
                peak_acceleration_g=float(body.get("peak_acceleration_g", 0.3)),
                frequency_hz=float(body.get("frequency_hz", 1.5)),
                duration_s=float(body.get("duration_s", 8.0)),
                fps=int(body.get("fps", 12)),
            )
        except AppSessionError as exc:
            return _err(exc)
        except (TypeError, ValueError):
            return RestResponse(status=422, body={
                "error": "mode/peak_acceleration_g/frequency_hz/duration_s/fps geçersiz.",
            })
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/buildings/<key>/damage/state")
    def _building_damage_state(id: str, key: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.building_damage_state(id, key)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Roadmap V10 / Faz 5.1 — Duman/Yangın sprite
    @router.post("/api/projects/<id>/buildings/<key>/fire/simulate")
    def _building_fire_simulate(id: str, key: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.building_fire_simulate(
                id, key,
                duration_s=float(body.get("duration_s", 30.0)),
                fps=int(body.get("fps", 4)),
                spread_rate_per_s=float(body.get("spread_rate_per_s", 0.35)),
                grid_size=int(body.get("grid_size", 8)),
                ignition=str(body.get("ignition", "center")),
                seed=int(body.get("seed", 42)),
            )
        except AppSessionError as exc:
            return _err(exc)
        except (TypeError, ValueError):
            return RestResponse(status=422, body={
                "error": "duration_s/fps/spread_rate_per_s/grid_size/ignition/seed geçersiz.",
            })
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Roadmap V10 / Faz 7 — Sinematik Kamera
    @router.get("/api/projects/<id>/cinematic/events")
    def _cinematic_events(id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            result = session.cinematic_scene_events(id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.post("/api/projects/<id>/cinematic/camera/track")
    def _cinematic_camera_track(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            from_position = body.get("from_position", [0.0, -30.0, 15.0])
            from_target = body.get("from_target", [0.0, 0.0, 0.0])
            result = session.cinematic_camera_track(
                id,
                event_index=int(body.get("event_index", 0)),
                from_position=tuple(float(v) for v in from_position),
                from_target=tuple(float(v) for v in from_target),
                transition_duration_s=float(body.get("transition_duration_s", 2.5)),
                hold_duration_s=float(body.get("hold_duration_s", 4.0)),
                viewing_distance_m=float(body.get("viewing_distance_m", 25.0)),
                fov_deg=float(body.get("fov_deg", 45.0)),
            )
        except AppSessionError as exc:
            return _err(exc)
        except (TypeError, ValueError, KeyError):
            return RestResponse(status=422, body={
                "error": "event_index/from_position/from_target/transition_duration_s/hold_duration_s/viewing_distance_m/fov_deg geçersiz.",
            })
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Roadmap V10 / Mekansal Ses (Faz 5.6) ---
    @router.get("/api/projects/<id>/audio/state")
    def _audio_state(id: str, query: dict | None = None, **_: Any) -> RestResponse:  # noqa: A002
        query = query or {}
        try:
            listener_position = (
                float(query.get("listener_x", 0.0)),
                float(query.get("listener_y", 0.0)),
                float(query.get("listener_z", 1.6)),
            )
            result = session.spatial_audio_state(id, listener_position=listener_position)
        except AppSessionError as exc:
            return _err(exc)
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "listener_x/listener_y/listener_z geçersiz."})
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Fikir 10 — IoT Sensör + Dijital İkiz ---
    @router.post("/api/projects/<id>/buildings/<key>/iot/tick")
    def _iot_tick(id: str, key: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.iot_digital_twin_tick(id, key, seed=body.get("seed"))
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.post("/api/projects/<id>/buildings/<key>/iot/connect")
    def _iot_connect(id: str, key: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        """GERÇEK bir MQTT broker'ına bağlanır (opsiyonel `paho-mqtt`
        bağımlılığı gerekir: `pip install harita[iot]`). Bağlantı
        başarısız olursa (broker'a ulaşılamıyor / kütüphane kurulu değil)
        503 döner — sessizce simülasyona düşülmez."""
        body = body or {}
        try:
            result = session.connect_iot_bridge(
                id, key,
                host=str(body.get("host", "localhost")),
                port=int(body.get("port", 1883)),
                timeout_s=float(body.get("timeout_s", 5.0)),
                topic_prefix=body.get("topic_prefix"),
            )
        except MqttBackendUnavailable as exc:
            return RestResponse(status=503, body={"error": str(exc)})
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.post("/api/projects/<id>/buildings/<key>/iot/disconnect")
    def _iot_disconnect(id: str, key: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            disconnected = session.disconnect_iot_bridge(id, key)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body={"disconnected": disconnected})

    # -- Web arayüzü genişletmesi: Fikir 11 — Yeşil Alan/Ağaçlandırma -----
    @router.post("/api/projects/<id>/vegetation/species-recommendation")
    def _vegetation_species(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            lat = float(body["lat"])
            lon = float(body["lon"])
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "lat/lon sayısal olmalı."})
        try:
            result = session.vegetation_species_recommendation(
                id, lat=lat, lon=lon,
                tree_count=(int(body["tree_count"]) if body.get("tree_count") not in (None, "") else None),
                days_back=int(body.get("days_back", 10)),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Fikir 12 — Gerçek Zamanlı Çoklu Kullanıcı
    @router.get("/api/projects/<id>/buildings/<key>/collab/state")
    def _collab_state(id: str, key: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            return RestResponse(status=200, body=session.collab_get_state(id, key))
        except AppSessionError as exc:
            return _err(exc)

    @router.post("/api/projects/<id>/buildings/<key>/collab/edit")
    def _collab_edit(id: str, key: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        actor_id = body.get("actor_id")
        field_name = body.get("field_name")
        if not actor_id or not field_name:
            return RestResponse(status=422, body={"error": "actor_id ve field_name gerekli."})
        try:
            result = session.collab_edit(
                id, key, actor_id=actor_id, field_name=field_name,
                value=body.get("value"), timestamp=body.get("timestamp"),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.post("/api/projects/<id>/buildings/<key>/collab/floor")
    def _collab_floor(id: str, key: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        actor_id = body.get("actor_id")
        op = body.get("op")
        floor_id = body.get("floor_id")
        if not actor_id or not op or not floor_id:
            return RestResponse(status=422, body={"error": "actor_id, op ve floor_id gerekli."})
        try:
            result = session.collab_floor_op(id, key, actor_id=actor_id, op=op, floor_id=floor_id)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Fikir 14 — Otomatik Proje Raporu -------
    @router.post("/api/projects/<id>/buildings/<key>/report/generate")
    def _generate_report(id: str, key: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.generate_building_report(
                id, key,
                lat=(float(body["lat"]) if body.get("lat") not in (None, "") else None),
                lon=(float(body["lon"]) if body.get("lon") not in (None, "") else None),
                fmt=body.get("format", "markdown"),
                out_dir=body.get("out_dir"),
            )
        except AppSessionError as exc:
            return _err(exc)
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "lat/lon sayısal olmalı."})
        return RestResponse(status=200, body=result)

    # -- Web arayüzü genişletmesi: Çoklu Kullanıcı / Giriş (collaboration) --
    @router.post("/api/auth/register")
    def _auth_register(body: Any = None, **_: Any) -> RestResponse:
        body = body or {}
        try:
            result = session.register_user(body["username"], body["password"])
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=201, body=result)

    @router.post("/api/auth/login")
    def _auth_login(body: Any = None, **_: Any) -> RestResponse:
        body = body or {}
        try:
            result = session.login_user(body["username"], body["password"])
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except AppSessionError as exc:
            return RestResponse(status=401, body={"error": str(exc)})
        return RestResponse(status=200, body=result)

    @router.post("/api/auth/logout")
    def _auth_logout(body: Any = None, **_: Any) -> RestResponse:
        body = body or {}
        return RestResponse(status=200, body=session.logout_user(body.get("token", "")))

    @router.get("/api/auth/whoami")
    def _auth_whoami(query: dict | None = None, **_: Any) -> RestResponse:
        token = (query or {}).get("token", "")
        try:
            result = session.whoami(token)
        except AppSessionError as exc:
            return RestResponse(status=401, body={"error": str(exc)})
        return RestResponse(status=200, body=result)

    # -- Faz 4.1: AI ayarları paneli (sağlayıcı seçimi / bağlantı testi) --- #
    _ai_config_limiter = SlidingWindowRateLimiter(max_requests=20, window_seconds=60.0)
    _ai_test_limiter = SlidingWindowRateLimiter(max_requests=6, window_seconds=60.0)

    @router.get("/api/ai/config")
    def _get_ai_config(**_: Any) -> dict[str, Any]:
        return session.get_ai_config()

    @router.post("/api/ai/config")
    def _set_ai_config(body: Any = None, **_: Any) -> RestResponse:
        if not _ai_config_limiter.allow("ai-config"):
            return RestResponse(status=429, body={"error": "cok fazla istek, biraz sonra tekrar deneyin."})
        body = body or {}
        try:
            result = session.set_ai_config(body)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.post("/api/ai/config/clear")
    def _clear_ai_config(**_: Any) -> RestResponse:
        session.clear_ai_config()
        return RestResponse(status=200, body={"configured": False, "backend": None})

    @router.post("/api/ai/test-connection")
    def _test_ai_connection(**_: Any) -> RestResponse:
        if not _ai_test_limiter.allow("ai-test-connection"):
            return RestResponse(status=429, body={"error": "cok fazla baglanti testi, biraz sonra tekrar deneyin."})
        result = session.test_ai_connection()
        return RestResponse(status=200 if result.get("ok") else 400, body=result)

    @router.post("/api/projects/<id>/members")
    def _grant_role(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.grant_project_role(
                id, body["token"], body["user_id"], body.get("role", "viewer"),
            )
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/projects/<id>/members")
    def _list_members(id: str, **_: Any) -> dict[str, Any]:  # noqa: A002
        return session.project_members(id)

    # -- Web arayüzü genişletmesi: AI İç Mekan / Çevre Üretimi ------------
    @router.post("/api/projects/<id>/buildings/<key>/ai/interior")
    def _ai_interior(id: str, key: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.generate_interior_layout(
                id, key,
                n_variants=int(body.get("n_variants", 5)),
                min_room_size=float(body.get("min_room_size", 3.0)),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.post("/api/projects/<id>/buildings/<key>/ai/environment")
    def _ai_environment(id: str, key: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.generate_environment(
                id, key,
                margin_m=float(body.get("margin_m", 15.0)),
                min_setback_m=float(body.get("min_setback_m", 1.5)),
                seed=body.get("seed"),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Roadmap V4 - Faz E9: Terrain editör köprüsü ----------------------

    @router.post("/api/projects/<id>/terrain/init")
    def _terrain_init(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            info = session.terrain_init(
                id,
                width=body.get("width", 64), height=body.get("height", 64),
                resolution_m=body.get("resolution_m", 2.0),
                base_elevation=body.get("base_elevation", 0.0),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=201, body=info)

    @router.get("/api/projects/<id>/terrain")
    def _terrain_state(id: str, **_: Any) -> dict[str, Any]:  # noqa: A002
        state = session.terrain_state(id)
        return {"terrain": state}

    @router.post("/api/projects/<id>/terrain/brush")
    def _terrain_brush(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            operation = body["operation"]
            center_x_m = float(body["center_x_m"])
            center_y_m = float(body["center_y_m"])
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "center_x_m/center_y_m sayı olmalı."})
        try:
            info = session.terrain_brush(
                id, operation, center_x_m, center_y_m,
                radius_m=body.get("radius_m", 6.0), strength=body.get("strength", 1.0),
                amount_m=body.get("amount_m", 1.0), target_elevation=body.get("target_elevation"),
                iterations=body.get("iterations", 1), seed=body.get("seed"),
                paint_weight=body.get("paint_weight", 1.0),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=info)

    @router.post("/api/projects/<id>/terrain/undo")
    def _terrain_undo(id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            return RestResponse(status=200, body=session.terrain_undo(id))
        except AppSessionError as exc:
            return _err(exc)

    @router.post("/api/projects/<id>/terrain/redo")
    def _terrain_redo(id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            return RestResponse(status=200, body=session.terrain_redo(id))
        except AppSessionError as exc:
            return _err(exc)

    # -- Web arayüzü genişletmesi: Sel/Heyelan Riski (hazard_data.flood_landslide) --
    @router.post("/api/projects/<id>/hazard/terrain-summary")
    def _hazard_terrain_summary(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.terrain_hazard_summary(id, rainfall_mm_24h=body.get("rainfall_mm_24h"))
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.post("/api/projects/<id>/hazard/terrain-point")
    def _hazard_terrain_point(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            x_m = float(body["x_m"])
            y_m = float(body["y_m"])
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "x_m/y_m sayısal olmalı."})
        try:
            result = session.terrain_hazard_point(id, x_m=x_m, y_m=y_m, rainfall_mm_24h=body.get("rainfall_mm_24h"))
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.post("/api/projects/<id>/hazard/terrain-top-cells")
    def _hazard_terrain_top_cells(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            result = session.terrain_hazard_top_cells(
                id, kind=body.get("kind", "landslide"),
                limit=int(body.get("limit", 20)),
                rainfall_mm_24h=body.get("rainfall_mm_24h"),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    # -- Roadmap V4 - Faz E9: Road editör köprüsü -------------------------

    @router.get("/api/projects/<id>/roads")
    def _list_roads(id: str, **_: Any) -> dict[str, Any]:  # noqa: A002
        return {"roads": session.list_roads(id)}

    @router.post("/api/projects/<id>/roads")
    def _add_road(id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            info = session.road_add(
                id, road_id=body.get("road_id"), width_m=body.get("width_m", 6.0),
                elevation_z=body.get("elevation_z", 0.0), name=body.get("name"),
            )
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=201, body=info)

    @router.delete("/api/projects/<id>/roads/<road_id>")
    def _remove_road(id: str, road_id: str, query: dict | None = None, **_: Any) -> RestResponse | dict[str, Any]:  # noqa: A002
        token = (query or {}).get("token")
        try:
            return {"removed": session.remove_road(id, road_id, token=token)}
        except AppSessionError as exc:
            return _err(exc)

    @router.post("/api/projects/<id>/roads/<road_id>/points")
    def _road_add_point(id: str, road_id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            x_m = float(body["x_m"])
            y_m = float(body["y_m"])
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "x_m/y_m sayı olmalı."})
        try:
            info = session.road_add_point(id, road_id, x_m, y_m, index=body.get("index"))
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=201, body=info)

    @router.put("/api/projects/<id>/roads/<road_id>/points/<index>")
    def _road_move_point(id: str, road_id: str, index: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            idx = int(index)
            x_m = float(body["x_m"])
            y_m = float(body["y_m"])
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "index/x_m/y_m geçersiz."})
        try:
            info = session.road_move_point(id, road_id, idx, x_m, y_m)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=info)

    @router.delete("/api/projects/<id>/roads/<road_id>/points/<index>")
    def _road_remove_point(id: str, road_id: str, index: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            idx = int(index)
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "index geçerli bir tam sayı olmalı."})
        try:
            info = session.road_remove_point(id, road_id, idx)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=info)

    @router.post("/api/projects/<id>/roads/<road_id>/width")
    def _road_set_width(id: str, road_id: str, body: Any = None, **_: Any) -> RestResponse:  # noqa: A002
        body = body or {}
        try:
            width_m = float(body["width_m"])
        except KeyError as exc:
            return RestResponse(status=422, body={"error": f"eksik alan: {exc}"})
        except (TypeError, ValueError):
            return RestResponse(status=422, body={"error": "width_m sayı olmalı."})
        try:
            info = session.road_set_width(id, road_id, width_m)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=info)

    @router.post("/api/projects/<id>/roads/<road_id>/undo")
    def _road_undo(id: str, road_id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            return RestResponse(status=200, body=session.road_undo(id, road_id))
        except AppSessionError as exc:
            return _err(exc)

    @router.post("/api/projects/<id>/roads/<road_id>/redo")
    def _road_redo(id: str, road_id: str, **_: Any) -> RestResponse:  # noqa: A002
        try:
            return RestResponse(status=200, body=session.road_redo(id, road_id))
        except AppSessionError as exc:
            return _err(exc)

    # -- ROADMAP_V7.md Faz C5 (offline mod, A4) --------------------------

    # ROADMAP_V8.md Faz 3.3 — A4 offline modun davranışsal sıkılaştırılması:
    # istemci offline moda geçtiğinde/çıktığında backend'e bunu bildirir;
    # session bundan sonra tüm OSM önizleme/içe aktarma/katman-özeti
    # çağrılarını (osm/preview, osm/import, osm/import-layers,
    # osm/category-summary) AĞ İSTEĞİ ATMADAN en baştan reddeder (bkz.
    # `AppSession._require_online`). Böylece "import'a basınca ağ hatası
    # alır ama engellenmez" davranışı ortadan kalkar.
    @router.post("/api/offline/mode")
    def _offline_mode_set(body: Any = None, **_: Any) -> RestResponse:
        body = body or {}
        if "enabled" not in body:
            return RestResponse(status=422, body={"error": "eksik alan: enabled"})
        return RestResponse(status=200, body=session.set_offline_mode(bool(body["enabled"])))

    @router.get("/api/offline/mode")
    def _offline_mode_get(**_: Any) -> RestResponse:
        return RestResponse(status=200, body={"offline_mode": session.is_offline_mode()})

    @router.post("/api/offline/download")
    def _offline_download(body: Any = None, **_: Any) -> RestResponse:
        body = body or {}
        required = ("min_lat", "min_lon", "max_lat", "max_lon", "zoom_min", "zoom_max", "url_template")
        missing = [k for k in required if k not in body]
        if missing:
            return RestResponse(status=422, body={"error": f"Eksik alan(lar): {', '.join(missing)}"})
        try:
            result = session.offline_download_region(
                min_lat=float(body["min_lat"]), min_lon=float(body["min_lon"]),
                max_lat=float(body["max_lat"]), max_lon=float(body["max_lon"]),
                zoom_min=int(body["zoom_min"]), zoom_max=int(body["zoom_max"]),
                url_template=str(body["url_template"]),
                region_name=str(body.get("region_name", "offline_region")),
            )
        except AppSessionError as exc:
            return _err(exc)
        except (TypeError, ValueError) as exc:
            return RestResponse(status=422, body={"error": f"Gecersiz parametre: {exc}"})
        return RestResponse(status=200, body=result)

    @router.get("/api/offline/stats")
    def _offline_stats(**_: Any) -> RestResponse:
        return RestResponse(status=200, body=session.offline_cache_stats())

    @router.get("/api/offline/tiles/<z>/<x>/<y>")
    def _offline_tile(z: str, x: str, y: str, **_: Any) -> RestResponse:
        try:
            zi, xi = int(z), int(x)
            # y dosya adı ".png" uzantısı içerebilir (Leaflet tile URL şablonu
            # `{z}/{x}/{y}.png` biçiminde istek gönderir) - uzantı ayrıştırılır.
            y_clean = y.split(".")[0]
            yi = int(y_clean)
        except ValueError:
            return RestResponse(status=400, body={"error": "z/x/y tam sayi olmali."})
        data = session.offline_get_tile(zi, xi, yi)
        if data is None:
            return RestResponse(status=404, body={"error": "Tile onbellekte yok."})
        return RestResponse(status=200, body=data, headers={"Content-Type": "image/png"})

    @router.post("/api/offline/places/index")
    def _offline_index_places(body: Any = None, **_: Any) -> RestResponse:
        body = body or {}
        project_id = body.get("project_id", "")
        category = body.get("category", "")
        features = body.get("features_geojson")
        if not features:
            return RestResponse(status=422, body={"error": "features_geojson gerekli."})
        try:
            result = session.offline_index_collection(project_id, category, features)
        except AppSessionError as exc:
            return _err(exc)
        return RestResponse(status=200, body=result)

    @router.get("/api/offline/places/search")
    def _offline_search_places(query: dict[str, Any] | None = None, **_: Any) -> RestResponse:
        q = (query or {}).get("q", "")
        if not q:
            return RestResponse(status=200, body={"results": []})
        limit_raw = (query or {}).get("limit", "10")
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            limit = 10
        return RestResponse(status=200, body={"results": session.offline_search_places(q, limit=limit)})

    return router

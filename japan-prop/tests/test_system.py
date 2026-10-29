import json
import pytest
from fastapi.testclient import TestClient

from backend.municipalities import (
    PREFECTURES,
    get_municipalities_by_prefecture,
    load_municipalities,
    resolve_location,
)
from backend.connectors.parser_utils import (
    compute_canonical_hash,
    parse_area_sqm,
    parse_build_year,
    parse_japanese_price,
    parse_ratios,
    parse_station_info,
)
from backend.connectors.gsi_gov import (
    analyze_hazard_and_topography,
    fetch_elevation,
    geocode_address,
)
from backend.database import (
    add_agent_evaluation,
    add_to_watchlist,
    get_database_stats,
    get_property_by_id,
    init_db,
    point_in_polygon,
    query_properties,
)
from backend.analyzer import (
    generate_comparison_report,
    simulate_investment_yield,
    simulate_land_building_volume,
)
from backend.cad_3d_engine import (
    export_dxf_file,
    export_gltf_file,
    export_obj_file,
    export_property_svg,
    fetch_urban_3d_scene,
    generate_architectural_model,
)
from backend.main import app
import mcp_server


def test_municipalities_and_prefectures():
    assert len(PREFECTURES) == 47
    munis = load_municipalities()
    assert len(munis) > 1700
    tokyo_munis = get_municipalities_by_prefecture("13")
    assert any(m["muni_name"] == "港区" for m in tokyo_munis)
    loc = resolve_location("長野県", "松本市")
    assert loc["prefecture"]["code"] == "20"
    assert loc["municipality"]["muni_code"] == "20202"


def test_japanese_parsers_and_dedup_hash():
    assert parse_japanese_price("1億3,800万円") == 138_000_000
    assert parse_japanese_price("5740万円～8980万円") == 57_400_000
    assert parse_japanese_price("18.5万円") == 185_000
    assert parse_area_sqm("9473.1㎡（2865.59坪）") == 9473.1
    assert parse_area_sqm("51.32m2（実測）") == 51.32
    cov, far = parse_ratios("建ぺい率：60％ / 容積率：160％")
    assert cov == 60.0 and far == 160.0
    st, walk = parse_station_info("東京メトロ千代田線「赤坂」徒歩5分")
    assert st == "赤坂" and walk == 5
    assert parse_build_year("1998年11月") == 1998

    # 異なるポータル間で丁目表記の有無が異なっても同一ハッシュに名寄せされることを検証
    h1 = compute_canonical_hash("売地", "東京都港区東麻布2", 255_000_000, 58.17, None)
    h2 = compute_canonical_hash("売地", "東京都港区東麻布", 255_000_000, 58.17, None)
    assert h1 == h2


@pytest.mark.asyncio
async def test_gsi_live_geocoding_and_hazard():
    geo = await geocode_address("東京都港区芝公園4丁目")
    assert geo is not None
    assert 35.60 < geo["latitude"] < 35.70
    assert 139.70 < geo["longitude"] < 139.80

    elev = await fetch_elevation(geo["latitude"], geo["longitude"])
    assert elev["elevation_m"] is not None

    hz = await analyze_hazard_and_topography(geo["latitude"], geo["longitude"])
    assert "flood" in hz["hazard_layers"]
    assert "tsunami" in hz["hazard_layers"]


def test_simulators_and_spatial_polygon():
    vol = simulate_land_building_volume(
        land_area_sqm=100.0,
        building_coverage_ratio=60.0,
        floor_area_ratio=200.0,
        road_width_m=4.0,
        zoning_category="住居系",
    )
    # 4m道路×40% = 実効容積率160%に制限されることを検証
    assert vol["result"]["effective_floor_area_ratio_pct"] == 160.0
    assert vol["result"]["max_total_floor_area_sqm"] == 160.0

    yld = simulate_investment_yield(
        price_yen=80_000_000,
        expected_monthly_rent_yen=500_000,
        down_payment_yen=10_000_000,
        loan_interest_rate_pct=1.8,
        loan_years=30,
    )
    assert yld["summary"]["gross_yield_pct"] == 7.5
    assert yld["summary"]["annual_noi_yen"] > 0

    poly = [[35.60, 139.70], [35.60, 139.80], [35.70, 139.80], [35.70, 139.70]]
    assert point_in_polygon(35.65, 139.75, poly) is True
    assert point_in_polygon(35.80, 139.75, poly) is False


def test_database_mcp_and_fastapi_endpoints():
    init_db()
    stats = get_database_stats()
    assert stats["total_properties"] > 50
    assert stats["multi_portal_merged_count"] >= 5

    props = query_properties(prefecture_code="13", category="売地", limit=5)
    assert len(props) > 0
    first_id = props[0]["id"]

    # MCPツール経由での評価登録・ウォッチリスト登録・比較レポート出力
    ev_json = mcp_server.record_agent_property_evaluation(
        property_id=first_id,
        agent_name="claude",
        score=91,
        recommendation="強く推奨",
        pros="都心駅徒歩圏・資産性良好",
        cons="建築条件・前面道路幅員の確認推奨",
        notes="Claudeによる自動評価テスト",
    )
    ev_data = json.loads(ev_json)
    assert ev_data["score"] == 91
    assert ev_data["agent_name"] == "クロード"

    wl_json = mcp_server.manage_watchlist_tool(
        action="add",
        property_id=first_id,
        status="現地確認予定",
        memo="優先確認候補",
        agent_name="codex",
    )
    wl_data = json.loads(wl_json)
    assert wl_data["property_id"] == first_id

    comp_json = mcp_server.compare_properties_tool([first_id])
    comp_data = json.loads(comp_json)
    assert comp_data["count"] == 1
    assert "クロード" in comp_data["markdown_report"]

    # FastAPI エンドポイント検証
    client = TestClient(app)
    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    assert r_health.json()["total_properties"] > 50

    r_props = client.get("/api/properties?prefecture_code=13&only_undervalued=true")
    assert r_props.status_code == 200
    assert r_props.json()["count"] > 0


@pytest.mark.asyncio
async def test_cad_floorplan_and_3d_models():
    # 1. 個別敷地・建築2D平面図および3Dモデル・斜線制限・日影計算検証
    model = generate_architectural_model(
        land_area_sqm=120.0,
        building_coverage_ratio=60.0,
        floor_area_ratio=200.0,
        road_width_m=5.4,
        road_direction="南",
        stories=3,
        floor_plan_type="4LDK",
        latitude=35.6645,
        longitude=139.7385,
        title="港区六本木計画敷地",
    )
    assert model["building"]["stories"] == 3
    assert len(model["building"]["floors"]) == 3
    assert len(model["slant_envelope"]["vertices_3d"]) == 10
    assert "冬至" in model["solar_simulation"]
    assert len(model["solar_simulation"]["冬至"]) >= 5

    svg_1f = export_property_svg(model, floor_number=1)
    assert "<svg" in svg_1f and "敷地間口" in svg_1f
    dxf_path = export_dxf_file(model, "test_prop.dxf")
    obj_path = export_obj_file(model, "test_prop.obj")
    gltf_path = export_gltf_file(model, "test_prop.gltf")
    assert dxf_path.exists() and dxf_path.stat().st_size > 100
    assert obj_path.exists() and obj_path.stat().st_size > 100
    assert gltf_path.exists() and gltf_path.stat().st_size > 100

    # 2. 国土地理院DEM標高メッシュ・等高線・ベクトルタイル実在建物3D抽出検証
    urban = await fetch_urban_3d_scene(lat=35.6645, lon=139.7385, radius_m=220, zoom=15)
    assert urban["terrain"]["grid_size"] >= 18
    assert urban["buildings_count"] > 10
    assert urban["roads_count"] > 5

    # 3. FastAPI /api/cad3d/* エンドポイント検証
    client = TestClient(app)
    r_cad_prop = client.post(
        "/api/cad3d/property",
        json={"property_id": 4, "stories": 3, "agent_name": "antigravity"},
    )
    assert r_cad_prop.status_code == 200
    cad_prop_data = r_cad_prop.json()
    assert cad_prop_data["model"]["building"]["stories"] == 3
    assert "1" in cad_prop_data["floor_svgs"]

    r_cad_urban = client.post(
        "/api/cad3d/urban",
        json={"property_id": 4, "radius_m": 220, "agent_name": "antigravity"},
    )
    assert r_cad_urban.status_code == 200
    assert r_cad_urban.json()["urban"]["buildings_count"] > 10

    r_dl = client.get("/api/cad3d/download/property_model.gltf")
    assert r_dl.status_code == 200


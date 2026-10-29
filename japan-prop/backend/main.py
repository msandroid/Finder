import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.municipalities import (
    PREFECTURES,
    get_municipalities_by_prefecture,
    load_municipalities,
)
from backend.connectors.gsi_gov import (
    GSI_MAP_TILES,
    HAZARD_MAP_TILES,
    analyze_hazard_and_topography,
    fetch_mlit_transactions,
    fetch_surrounding_facilities,
    geocode_address,
)
from backend.connectors.aggregator import live_search_and_sync
from backend.database import (
    add_agent_evaluation,
    add_to_watchlist,
    get_database_stats,
    get_property_by_id,
    get_ui_sync_state,
    get_watchlists,
    init_db,
    query_properties,
    record_agent_activity,
    remove_from_watchlist,
    update_ui_sync_state,
)
from backend.analyzer import (
    generate_comparison_report,
    simulate_investment_yield,
    simulate_land_building_volume,
)
from backend.cad_3d_engine import (
    EXPORT_DIR,
    export_dxf_file,
    export_gltf_file,
    export_obj_file,
    export_property_svg,
    export_urban_svg,
    fetch_urban_3d_scene,
    generate_architectural_model,
)

@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    asyncio.get_running_loop().run_in_executor(None, load_municipalities)
    yield


app = FastAPI(
    title="日本全国不動産・土地網羅検索プラットフォーム API",
    description="Claude / Codex / Antigravity とネイティブ連携する日本全国対応の不動産・土地検索・空間分析基盤",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        dead: List[WebSocket] = []
        for conn in self.active_connections:
            try:
                await conn.send_json(payload)
            except Exception:
                dead.append(conn)
        for d in dead:
            self.disconnect(d)


ws_manager = ConnectionManager()


class LiveSearchRequest(BaseModel):
    prefecture: str = Field(default="13", description="都道府県コード（01〜47）または都道府県名")
    municipality: Optional[str] = Field(default=None, description="市区町村名または5桁JISコード")
    category: str = Field(default="売地", description="売地 / 戸建て / マンション / 空き家・古民家 / 収益物件 / 賃貸 / すべて")
    keyword: Optional[str] = Field(default=None, description="絞り込みキーワード")
    agent_name: Optional[str] = Field(default=None, description="呼び出し元エージェント名（claude / codex / antigravity / 人間）")


class PolygonSearchRequest(BaseModel):
    polygon: List[List[float]] = Field(..., description="ポリゴン頂点座標リスト [[lat, lon], ...]")
    category: Optional[str] = None
    min_price: Optional[int] = None
    max_price: Optional[int] = None
    min_land_area_sqm: Optional[float] = None
    only_undervalued: bool = False
    sort_by: str = "price_asc"


class AgentEvaluationRequest(BaseModel):
    agent_name: str = Field(default="アンチグラビティ", description="claude / codex / antigravity / 人間")
    score: int = Field(default=80, ge=0, le=100)
    recommendation: str = Field(default="推奨", description="強く推奨 / 推奨 / 要検討 / 見送り")
    pros: str = Field(default="")
    cons: str = Field(default="")
    notes: str = Field(default="")


class WatchlistAddRequest(BaseModel):
    property_id: int
    watchlist_name: str = "重点調査案件リスト"
    status: str = "調査中"
    memo: str = ""
    added_by: str = "人間"


class VolumeSimRequest(BaseModel):
    land_area_sqm: float
    building_coverage_ratio: float = 60.0
    floor_area_ratio: float = 200.0
    road_width_m: float = 6.0
    zoning_category: str = "住居系"
    corner_lot_relaxation: bool = False
    structure_type: str = "木造"


class YieldSimRequest(BaseModel):
    price_yen: int
    expected_monthly_rent_yen: int
    renovation_or_build_cost_yen: int = 0
    acquisition_expense_rate_pct: float = 7.0
    vacancy_rate_pct: float = 5.0
    opex_rate_pct: float = 15.0
    down_payment_yen: int = 0
    loan_interest_rate_pct: float = 1.8
    loan_years: int = 30


class CompareRequest(BaseModel):
    property_ids: List[int]


class UISyncUpdateRequest(BaseModel):
    key: str
    value: Any
    notify_agents: bool = True


class PropertyCad3DRequest(BaseModel):
    property_id: Optional[int] = None
    land_area_sqm: Optional[float] = None
    building_coverage_ratio: Optional[float] = None
    floor_area_ratio: Optional[float] = None
    road_width_m: float = 5.4
    road_direction: str = "南"
    stories: Optional[int] = None
    floor_plan_type: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    title: Optional[str] = None
    address: Optional[str] = None
    agent_name: Optional[str] = None


class UrbanCad3DRequest(BaseModel):
    property_id: Optional[int] = None
    latitude: float = 35.6586
    longitude: float = 139.7454
    radius_m: int = 220
    zoom: int = 15
    agent_name: Optional[str] = None


@app.get("/api/health")
async def health_check() -> Dict[str, Any]:
    stats = get_database_stats()
    return {
        "status": "正常稼働中",
        "total_properties": stats["total_properties"],
        "websocket_clients": len(ws_manager.active_connections),
    }


@app.get("/api/prefectures")
async def list_prefectures() -> List[Dict[str, Any]]:
    prefs = list(PREFECTURES.values())
    prefs.sort(key=lambda x: x["code"])
    return prefs


@app.get("/api/municipalities/{pref_code}")
async def list_municipalities(pref_code: str) -> List[Dict[str, str]]:
    return get_municipalities_by_prefecture(pref_code)


@app.get("/api/layers")
async def list_map_layers() -> Dict[str, Any]:
    return {
        "base_layers": list(GSI_MAP_TILES.values()),
        "hazard_layers": list(HAZARD_MAP_TILES.values()),
    }


@app.get("/api/stats")
async def database_statistics() -> Dict[str, Any]:
    return get_database_stats()


@app.get("/api/properties")
async def search_properties_endpoint(
    prefecture_code: Optional[str] = Query(default=None),
    municipality_name: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    transaction_type: Optional[str] = Query(default=None),
    min_price: Optional[int] = Query(default=None),
    max_price: Optional[int] = Query(default=None),
    min_land_area_sqm: Optional[float] = Query(default=None),
    max_land_area_sqm: Optional[float] = Query(default=None),
    min_building_area_sqm: Optional[float] = Query(default=None),
    max_walk_min: Optional[int] = Query(default=None),
    min_coverage_ratio: Optional[float] = Query(default=None),
    min_floor_area_ratio: Optional[float] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    min_lat: Optional[float] = Query(default=None),
    min_lon: Optional[float] = Query(default=None),
    max_lat: Optional[float] = Query(default=None),
    max_lon: Optional[float] = Query(default=None),
    only_undervalued: bool = Query(default=False),
    sort_by: str = Query(default="price_asc"),
    limit: int = Query(default=120, le=500),
) -> Dict[str, Any]:
    bbox = None
    if None not in (min_lat, min_lon, max_lat, max_lon):
        bbox = (float(min_lat), float(min_lon), float(max_lat), float(max_lon))

    items = query_properties(
        prefecture_code=prefecture_code,
        municipality_name=municipality_name,
        category=category,
        transaction_type=transaction_type,
        min_price=min_price,
        max_price=max_price,
        min_land_area_sqm=min_land_area_sqm,
        max_land_area_sqm=max_land_area_sqm,
        min_building_area_sqm=min_building_area_sqm,
        max_walk_min=max_walk_min,
        min_coverage_ratio=min_coverage_ratio,
        min_floor_area_ratio=min_floor_area_ratio,
        keyword=keyword,
        bbox=bbox,
        only_undervalued=only_undervalued,
        sort_by=sort_by,
        limit=limit,
    )
    return {
        "count": len(items),
        "properties": items,
    }


@app.post("/api/properties/polygon-search")
async def polygon_search_endpoint(req: PolygonSearchRequest) -> Dict[str, Any]:
    items = query_properties(
        category=req.category,
        min_price=req.min_price,
        max_price=req.max_price,
        min_land_area_sqm=req.min_land_area_sqm,
        polygon=req.polygon,
        only_undervalued=req.only_undervalued,
        sort_by=req.sort_by,
        limit=200,
    )
    mv = get_ui_sync_state().get("map_view", {})
    mv["polygon"] = req.polygon
    update_ui_sync_state("map_view", mv)
    return {
        "count": len(items),
        "properties": items,
    }


@app.post("/api/search/live")
async def trigger_live_search(req: LiveSearchRequest) -> Dict[str, Any]:
    result = await live_search_and_sync(
        prefecture=req.prefecture,
        municipality=req.municipality,
        category=req.category,
        keyword=req.keyword,
        agent_name=req.agent_name,
    )
    await ws_manager.broadcast({
        "type": "LIVE_SEARCH_COMPLETED",
        "location": result["location"],
        "fetched_raw_count": result["fetched_raw_count"],
        "upsert_summary": result["upsert_summary"],
        "ui_sync_state": get_ui_sync_state(),
    })
    return result


@app.get("/api/properties/{property_id}")
async def get_property_detail_endpoint(property_id: int) -> Dict[str, Any]:
    prop = get_property_by_id(property_id)
    if not prop:
        raise HTTPException(status_code=404, detail="指定された物件が見つかりません。")
    return prop


@app.get("/api/properties/{property_id}/spatial-analysis")
async def get_property_spatial_analysis(property_id: int) -> Dict[str, Any]:
    prop = get_property_by_id(property_id)
    if not prop:
        raise HTTPException(status_code=404, detail="指定された物件が見つかりません。")

    lat = prop.get("latitude")
    lon = prop.get("longitude")
    if lat is None or lon is None:
        geo = await geocode_address(prop["address"])
        if geo:
            lat, lon = geo["latitude"], geo["longitude"]
        else:
            p_info = PREFECTURES.get(prop["prefecture_code"], PREFECTURES["13"])
            lat, lon = p_info["lat"], p_info["lon"]

    hazard_task = analyze_hazard_and_topography(lat, lon)
    facilities_task = fetch_surrounding_facilities(lat, lon, radius_m=800)
    mlit_task = fetch_mlit_transactions(prop["prefecture_code"])

    hazard_res, facilities_res, mlit_res = await asyncio.gather(
        hazard_task, facilities_task, mlit_task
    )

    return {
        "property_id": property_id,
        "title": prop["title"],
        "address": prop["address"],
        "hazard_and_topography": hazard_res,
        "surrounding_facilities": facilities_res,
        "mlit_public_data": mlit_res,
    }


@app.post("/api/properties/{property_id}/evaluations")
async def create_property_evaluation(
    property_id: int, req: AgentEvaluationRequest
) -> Dict[str, Any]:
    prop = get_property_by_id(property_id)
    if not prop:
        raise HTTPException(status_code=404, detail="指定された物件が見つかりません。")

    ev = add_agent_evaluation(
        property_id=property_id,
        agent_name=req.agent_name,
        score=req.score,
        recommendation=req.recommendation,
        pros=req.pros,
        cons=req.cons,
        notes=req.notes,
    )
    await ws_manager.broadcast({
        "type": "AGENT_EVALUATION_ADDED",
        "evaluation": ev,
        "ui_sync_state": get_ui_sync_state(),
    })
    return ev


@app.get("/api/watchlists")
async def list_watchlists_endpoint() -> List[Dict[str, Any]]:
    return get_watchlists()


@app.post("/api/watchlists/items")
async def add_watchlist_item_endpoint(req: WatchlistAddRequest) -> Dict[str, Any]:
    res = add_to_watchlist(
        property_id=req.property_id,
        watchlist_name=req.watchlist_name,
        status=req.status,
        memo=req.memo,
        added_by=req.added_by,
    )
    await ws_manager.broadcast({
        "type": "WATCHLIST_UPDATED",
        "item": res,
        "ui_sync_state": get_ui_sync_state(),
    })
    return res


@app.delete("/api/watchlists/{watchlist_id}/items/{property_id}")
async def delete_watchlist_item_endpoint(watchlist_id: int, property_id: int) -> Dict[str, Any]:
    removed = remove_from_watchlist(watchlist_id, property_id)
    await ws_manager.broadcast({
        "type": "WATCHLIST_UPDATED",
        "ui_sync_state": get_ui_sync_state(),
    })
    return {"removed": removed}


@app.post("/api/simulate/volume")
async def simulate_volume_endpoint(req: VolumeSimRequest) -> Dict[str, Any]:
    return simulate_land_building_volume(
        land_area_sqm=req.land_area_sqm,
        building_coverage_ratio=req.building_coverage_ratio,
        floor_area_ratio=req.floor_area_ratio,
        road_width_m=req.road_width_m,
        zoning_category=req.zoning_category,
        corner_lot_relaxation=req.corner_lot_relaxation,
        structure_type=req.structure_type,
    )


@app.post("/api/simulate/yield")
async def simulate_yield_endpoint(req: YieldSimRequest) -> Dict[str, Any]:
    return simulate_investment_yield(
        price_yen=req.price_yen,
        expected_monthly_rent_yen=req.expected_monthly_rent_yen,
        renovation_or_build_cost_yen=req.renovation_or_build_cost_yen,
        acquisition_expense_rate_pct=req.acquisition_expense_rate_pct,
        vacancy_rate_pct=req.vacancy_rate_pct,
        opex_rate_pct=req.opex_rate_pct,
        down_payment_yen=req.down_payment_yen,
        loan_interest_rate_pct=req.loan_interest_rate_pct,
        loan_years=req.loan_years,
    )


@app.post("/api/compare")
async def compare_properties_endpoint(req: CompareRequest) -> Dict[str, Any]:
    return generate_comparison_report(req.property_ids)


@app.post("/api/cad3d/property")
async def generate_property_cad3d_endpoint(req: PropertyCad3DRequest) -> Dict[str, Any]:
    prop = get_property_by_id(req.property_id) if req.property_id is not None else None

    land_area = (
        req.land_area_sqm
        or (prop.get("land_area_sqm") if prop else None)
        or (prop.get("building_area_sqm") if prop else None)
        or 110.0
    )
    cov = (
        req.building_coverage_ratio
        or (prop.get("building_coverage_ratio") if prop else None)
        or 60.0
    )
    far = (
        req.floor_area_ratio
        or (prop.get("floor_area_ratio") if prop else None)
        or 200.0
    )
    lat = req.latitude or (prop.get("latitude") if prop else None) or 35.6586
    lon = req.longitude or (prop.get("longitude") if prop else None) or 139.7454
    title = req.title or (prop.get("title") if prop else None) or "標準敷地・建築計画モデル"
    address = req.address or (prop.get("address") if prop else None) or "東京都港区"
    floor_plan = req.floor_plan_type or (prop.get("floor_plan") if prop else None) or "3LDK"

    model = generate_architectural_model(
        land_area_sqm=land_area,
        building_coverage_ratio=cov,
        floor_area_ratio=far,
        road_width_m=req.road_width_m,
        road_direction=req.road_direction,
        stories=req.stories,
        floor_plan_type=floor_plan,
        latitude=lat,
        longitude=lon,
        title=title,
        address=address,
    )

    floor_svgs: Dict[int, str] = {}
    for fl in model["building"]["floors"]:
        fnum = fl["floor_number"]
        svg_str = export_property_svg(model, floor_number=fnum)
        floor_svgs[fnum] = svg_str
        (EXPORT_DIR / f"property_floor_{fnum}.svg").write_text(svg_str, encoding="utf-8")

    export_dxf_file(model, "property_plan.dxf")
    export_obj_file(model, "property_model.obj")
    export_gltf_file(model, "property_model.gltf")

    if req.agent_name:
        record_agent_activity(
            agent_name=req.agent_name,
            action="平面図・立体建築モデル生成",
            summary=f"「{title}」（敷地{land_area}平米・地上{model['building']['stories']}階建）の平面図と立体モデルを生成しました。",
            highlighted_property_ids=[req.property_id] if req.property_id else [],
        )
        await ws_manager.broadcast({
            "type": "CAD3D_GENERATED",
            "mode": "property",
            "ui_sync_state": get_ui_sync_state(),
        })

    return {
        "model": model,
        "floor_svgs": floor_svgs,
        "downloads": {
            "svg_floor_1": "/api/cad3d/download/property_floor_1.svg",
            "dxf": "/api/cad3d/download/property_plan.dxf",
            "obj": "/api/cad3d/download/property_model.obj",
            "gltf": "/api/cad3d/download/property_model.gltf",
        },
    }


@app.post("/api/cad3d/urban")
async def generate_urban_cad3d_endpoint(req: UrbanCad3DRequest) -> Dict[str, Any]:
    lat = req.latitude
    lon = req.longitude
    if req.property_id is not None:
        prop = get_property_by_id(req.property_id)
        if prop and prop.get("latitude") and prop.get("longitude"):
            lat = prop["latitude"]
            lon = prop["longitude"]

    urban = await fetch_urban_3d_scene(
        lat=lat,
        lon=lon,
        radius_m=req.radius_m,
        zoom=req.zoom,
    )
    svg_str = export_urban_svg(urban)
    (EXPORT_DIR / "urban_plan.svg").write_text(svg_str, encoding="utf-8")
    export_dxf_file(urban, "urban_plan.dxf")
    export_obj_file(urban, "urban_model.obj")
    export_gltf_file(urban, "urban_model.gltf")

    if req.agent_name:
        record_agent_activity(
            agent_name=req.agent_name,
            action="都市・地形・等高線立体モデル生成",
            summary=(
                f"北緯{lat:.4f}度・東経{lon:.4f}度周辺（実在建物{urban['buildings_count']}棟）の"
                f"街区等高線図および都市地形立体モデルを生成しました。"
            ),
            highlighted_property_ids=[req.property_id] if req.property_id else [],
        )
        await ws_manager.broadcast({
            "type": "CAD3D_GENERATED",
            "mode": "urban",
            "ui_sync_state": get_ui_sync_state(),
        })

    return {
        "urban": urban,
        "svg": svg_str,
        "downloads": {
            "svg": "/api/cad3d/download/urban_plan.svg",
            "dxf": "/api/cad3d/download/urban_plan.dxf",
            "obj": "/api/cad3d/download/urban_model.obj",
            "gltf": "/api/cad3d/download/urban_model.gltf",
        },
    }


@app.get("/api/cad3d/download/{filename}")
async def download_cad3d_file(filename: str):
    safe_name = Path(filename).name
    file_path = EXPORT_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="指定された出力ファイルが見つかりません。")
    return FileResponse(str(file_path), filename=safe_name)


@app.get("/api/ui-sync")
async def get_ui_sync_endpoint() -> Dict[str, Any]:
    return get_ui_sync_state()


@app.post("/api/ui-sync")
async def post_ui_sync_endpoint(req: UISyncUpdateRequest) -> Dict[str, Any]:
    state = update_ui_sync_state(req.key, req.value)
    if req.notify_agents:
        await ws_manager.broadcast({
            "type": "UI_SYNC_UPDATED",
            "key": req.key,
            "ui_sync_state": state,
        })
    return state


@app.get("/api/agent-integration-guide")
async def get_agent_integration_guide() -> Dict[str, Any]:
    root_dir = str(Path(__file__).resolve().parent.parent)
    mcp_script = str(Path(__file__).resolve().parent.parent / "mcp_server.py")
    cli_script = str(Path(__file__).resolve().parent.parent / "cli.py")
    return {
        "workspace_path": root_dir,
        "mcp_server_config": {
            "mcpServers": {
                "jp-property-finder": {
                    "command": "uv",
                    "args": ["--directory", root_dir, "run", "python", mcp_script],
                }
            }
        },
        "cli_examples": [
            f'uv --directory "{root_dir}" run python "{cli_script}" search --prefecture 東京都 --municipality 港区 --category 売地 --live',
            f'uv --directory "{root_dir}" run python "{cli_script}" query --category 売地 --only-undervalued --format json',
            f'uv --directory "{root_dir}" run python "{cli_script}" analyze --property-id 1',
            f'uv --directory "{root_dir}" run python "{cli_script}" evaluate --property-id 1 --agent claude --score 88 --recommendation 強く推奨 --pros "駅徒歩5分・整形地" --cons "前面道路幅員要確認"',
            f'uv --directory "{root_dir}" run python "{cli_script}" ui-context',
        ],
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await ws_manager.connect(websocket)
    try:
        await websocket.send_json({
            "type": "INITIAL_SYNC",
            "ui_sync_state": get_ui_sync_state(),
        })
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                if msg.get("type") == "UPDATE_UI_CONTEXT":
                    for k, v in msg.get("payload", {}).items():
                        update_ui_sync_state(k, v)
                    await ws_manager.broadcast({
                        "type": "UI_SYNC_UPDATED",
                        "ui_sync_state": get_ui_sync_state(),
                    })
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# フロントエンドビルド成果物（frontend/dist）が存在する場合は静的配信
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("ws"):
            raise HTTPException(status_code=404)
        target = FRONTEND_DIST / full_path
        if target.is_file():
            return FileResponse(str(target))
        return FileResponse(str(FRONTEND_DIST / "index.html"))

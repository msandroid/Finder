import json
from typing import Any, Dict, List, Optional
try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP

from backend.municipalities import (
    PREFECTURES,
    get_municipalities_by_prefecture,
    resolve_location,
)
from backend.connectors.aggregator import live_search_and_sync
from backend.research import ResearchRequirements, render_markdown, run_research
from backend.connectors.gsi_gov import (
    analyze_hazard_and_topography,
    fetch_surrounding_facilities,
    geocode_address,
)
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

mcp = FastMCP(
    "jp-property-finder",
    instructions=(
        "日本全国の不動産・土地・戸建て・マンション・空き家・収益物件・賃貸を網羅的に検索・空間分析する"
        "ネイティブMCPサーバーです。Claude、Codex、Antigravityから直接呼び出し可能で、"
        "Web UIおよびローカルSQLiteデータベースとリアルタイムに状態を共有します。"
        "物件探しの依頼では、まず research_properties で調査し、その報告をもとに個別ツールで深掘りしてください。"
        "賃貸物件では price が月額家賃（円）です。"
        "検索対象はポータルサイトに掲載された物件だけです。日本には不動産事業者（宅地建物取引業者）しか"
        "閲覧できない非公開物件（レインズなど業者向けシステムのみに登録された物件や、広告されない物件）があるため、"
        "結果を伝えるときは、不動産会社に問い合わせれば非公開物件も紹介してもらえることを利用者に伝えてください。"
    ),
)


@mcp.tool()
async def live_search_japan_properties(
    prefecture: str = "東京都",
    municipality: Optional[str] = None,
    category: str = "売地",
    keyword: Optional[str] = None,
    agent_name: str = "antigravity",
) -> str:
    """
    日本全国の指定エリア（都道府県・市区町村）および物件種別（売地 / 戸建て / マンション / 空き家・古民家 / 収益物件 / 賃貸 / すべて）について、
    スーモ・ヤフー不動産・ニフティ不動産からリアルタイムで実物件データを横断取得し、
    国土地理院APIで緯度経度・標高を付与した上でローカルDBへ名寄せ保存して結果を返します。
    """
    init_db()
    res = await live_search_and_sync(
        prefecture=prefecture,
        municipality=municipality,
        category=category,
        keyword=keyword,
        agent_name=agent_name,
    )
    summary = {
        "location": res["location"],
        "fetched_raw_count": res["fetched_raw_count"],
        "source_breakdown": res["source_breakdown"],
        "upsert_summary": res["upsert_summary"],
        "returned_count": len(res["properties"]),
        "top_properties": res["properties"][:25],
    }
    return json.dumps(summary, ensure_ascii=False, indent=2)


@mcp.tool()
def query_local_properties(
    prefecture: Optional[str] = None,
    municipality: Optional[str] = None,
    category: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    min_land_area_sqm: Optional[float] = None,
    max_walk_min: Optional[int] = None,
    keyword: Optional[str] = None,
    only_undervalued: bool = False,
    sort_by: str = "price_asc",
    limit: int = 30,
    agent_name: str = "antigravity",
) -> str:
    """
    ローカルデータベースに蓄積・名寄せされた物件データを高速検索します。
    sort_by には price_asc, price_desc, tsubo_asc, area_desc, deviation_asc, yield_desc, walk_asc, newest が指定可能です。
    """
    init_db()
    pref_code = None
    muni_name = municipality
    if prefecture:
        loc = resolve_location(prefecture, municipality)
        if loc["prefecture"]:
            pref_code = loc["prefecture"]["code"]
        if loc["municipality"]:
            muni_name = loc["municipality"]["muni_name"]

    items = query_properties(
        prefecture_code=pref_code,
        municipality_name=muni_name,
        category=category,
        min_price=min_price,
        max_price=max_price,
        min_land_area_sqm=min_land_area_sqm,
        max_walk_min=max_walk_min,
        keyword=keyword,
        only_undervalued=only_undervalued,
        sort_by=sort_by,
        limit=limit,
    )
    if items:
        record_agent_activity(
            agent_name=agent_name,
            action="データベース検索",
            summary=f"条件検索により {len(items)} 件の物件を抽出しました。",
            highlighted_property_ids=[it["id"] for it in items[:10]],
        )
    return json.dumps({"count": len(items), "properties": items}, ensure_ascii=False, indent=2)


@mcp.tool()
async def research_properties(
    prefecture: str = "東京都",
    municipalities: Optional[List[str]] = None,
    category: str = "賃貸",
    use_case: str = "residence",
    headcount_min: Optional[int] = None,
    headcount_max: Optional[int] = None,
    min_area_sqm: Optional[float] = None,
    max_area_sqm: Optional[float] = None,
    max_monthly_cost: Optional[int] = None,
    max_walk_min: Optional[int] = None,
    must_have: Optional[List[str]] = None,
    exclude: Optional[List[str]] = None,
    shortlist_size: int = 10,
    live: bool = True,
    output_format: str = "markdown",
) -> str:
    """
    物件探しの調査を一括で行うハーネスです。物件を探す依頼では、個別の検索ツールより先にこれを使ってください。
    条件の正規化（人数→面積など）、エリアごとのライブ検索（失敗時はDBの過去データで継続）、必須条件での絞り込み、
    採点、掲載データの欠落・矛盾の検出をまとめて行い、前提・収集状況・データの鮮度・候補・足りない範囲・
    確認事項・非公開物件の注意・次にやることを含む報告を返します。
    use_case は residence（住まい）/ office（一般オフィス）/ software_dev（ソフトウェア開発オフィス）。
    max_monthly_cost は月額（賃貸は家賃、オフィスは共益費込み）の上限（円）。
    must_have / exclude は掲載文に含まれるべき語・含まれてはいけない語（例: ["24時間"]）。
    output_format は markdown か json。
    """
    req = ResearchRequirements(
        prefecture=prefecture,
        municipalities=municipalities or [],
        category=category,
        use_case=use_case,
        headcount_min=headcount_min,
        headcount_max=headcount_max,
        min_area_sqm=min_area_sqm,
        max_area_sqm=max_area_sqm,
        max_monthly_cost=max_monthly_cost,
        max_walk_min=max_walk_min,
        must_have=must_have or [],
        exclude=exclude or [],
        shortlist_size=shortlist_size,
    )
    report = await run_research(req, live=live)
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)
    return render_markdown(report)


@mcp.tool()
def get_property_detail(property_id: int) -> str:
    """物件番号（ID）を指定して、物件の全詳細・複数ポータル掲載URL・価格推移履歴・各AIエージェントの評価一覧を取得します。"""
    init_db()
    prop = get_property_by_id(property_id)
    if not prop:
        return json.dumps({"error": f"物件番号 {property_id} が見つかりません。"}, ensure_ascii=False)
    return json.dumps(prop, ensure_ascii=False, indent=2)


@mcp.tool()
async def analyze_property_hazard_and_surroundings(
    property_id: int,
    agent_name: str = "antigravity",
) -> str:
    """
    指定した物件番号（ID）の所在地について、
    1. 国土地理院の精密標高データ（1m/5mレーザDEM）
    2. 国土交通省ハザードマップポータルサイト（洪水浸水・津波浸水・土砂災害警戒区域・土石流）の指定有無判定
    3. OpenStreetMap Overpass API による半径800m圏内の生活利便施設（駅・スーパー・病院・学校・公園）の実距離一覧
    をリアルタイム解析して返します。
    """
    init_db()
    prop = get_property_by_id(property_id)
    if not prop:
        return json.dumps({"error": f"物件番号 {property_id} が見つかりません。"}, ensure_ascii=False)

    lat = prop.get("latitude")
    lon = prop.get("longitude")
    if lat is None or lon is None:
        geo = await geocode_address(prop["address"])
        if geo:
            lat, lon = geo["latitude"], geo["longitude"]
        else:
            p_info = PREFECTURES.get(prop["prefecture_code"], PREFECTURES["13"])
            lat, lon = p_info["lat"], p_info["lon"]

    hazard = await analyze_hazard_and_topography(lat, lon)
    facilities = await fetch_surrounding_facilities(lat, lon, radius_m=800)
    record_agent_activity(
        agent_name=agent_name,
        action="空間・ハザード解析",
        summary=f"物件番号 {property_id}（{prop['title']}）の標高・ハザード・周辺施設を解析しました。",
        highlighted_property_ids=[property_id],
        map_center=(lat, lon, 16),
    )
    return json.dumps(
        {
            "property_id": property_id,
            "title": prop["title"],
            "address": prop["address"],
            "hazard_and_topography": hazard,
            "surrounding_facilities": facilities,
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def simulate_land_building_volume_tool(
    land_area_sqm: float,
    building_coverage_ratio: float = 60.0,
    floor_area_ratio: float = 200.0,
    road_width_m: float = 6.0,
    zoning_category: str = "住居系",
    corner_lot_relaxation: bool = False,
    structure_type: str = "木造",
) -> str:
    """
    土地面積（平米）・建蔽率（%）・容積率（%）・前面道路幅員（m）・用途地域区分（住居系/商業系）・角地緩和有無・構造種別から、
    建築基準法に基づく実効容積率・最大建築面積・最大延床面積・推奨階数・概算建築費を試算します。
    """
    res = simulate_land_building_volume(
        land_area_sqm=land_area_sqm,
        building_coverage_ratio=building_coverage_ratio,
        floor_area_ratio=floor_area_ratio,
        road_width_m=road_width_m,
        zoning_category=zoning_category,
        corner_lot_relaxation=corner_lot_relaxation,
        structure_type=structure_type,
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def simulate_investment_yield_tool(
    price_yen: int,
    expected_monthly_rent_yen: int,
    renovation_or_build_cost_yen: int = 0,
    down_payment_yen: int = 0,
    loan_interest_rate_pct: float = 1.8,
    loan_years: int = 30,
) -> str:
    """
    物件価格・想定月額賃料・建築/改修費・自己資金・融資金利・融資年数から、
    表面利回り・実質利回り・NOI（純営業収益）・月次ローン返済額・税引前キャッシュフロー・CCR（自己資金配当率）を試算します。
    """
    res = simulate_investment_yield(
        price_yen=price_yen,
        expected_monthly_rent_yen=expected_monthly_rent_yen,
        renovation_or_build_cost_yen=renovation_or_build_cost_yen,
        down_payment_yen=down_payment_yen,
        loan_interest_rate_pct=loan_interest_rate_pct,
        loan_years=loan_years,
    )
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def record_agent_property_evaluation(
    property_id: int,
    agent_name: str = "antigravity",
    score: int = 85,
    recommendation: str = "推奨",
    pros: str = "",
    cons: str = "",
    notes: str = "",
) -> str:
    """
    Claude / Codex / Antigravity による物件評価スコア（0〜100点）、推奨度（強く推奨 / 推奨 / 要検討 / 見送り）、
    メリット・デメリット・調査所見をデータベースに保存し、Web UIへリアルタイム共有します。
    """
    init_db()
    ev = add_agent_evaluation(
        property_id=property_id,
        agent_name=agent_name,
        score=score,
        recommendation=recommendation,
        pros=pros,
        cons=cons,
        notes=notes,
    )
    return json.dumps(ev, ensure_ascii=False, indent=2)


@mcp.tool()
def manage_watchlist_tool(
    action: str = "list",
    property_id: Optional[int] = None,
    watchlist_name: str = "重点調査案件リスト",
    status: str = "調査中",
    memo: str = "",
    agent_name: str = "antigravity",
) -> str:
    """
    案件ウォッチリストの参照（action='list'）または物件の追加・更新（action='add'）を行います。
    """
    init_db()
    if action == "add" and property_id is not None:
        added = add_to_watchlist(
            property_id=property_id,
            watchlist_name=watchlist_name,
            status=status,
            memo=memo,
            added_by=agent_name,
        )
        return json.dumps(added, ensure_ascii=False, indent=2)
    return json.dumps(get_watchlists(), ensure_ascii=False, indent=2)


@mcp.tool()
def compare_properties_tool(property_ids: List[int]) -> str:
    """複数の物件番号（IDリスト）を横断比較し、単価・面積・相場乖離率・エージェント評価をまとめた比較レポートを生成します。"""
    init_db()
    rep = generate_comparison_report(property_ids)
    return json.dumps(rep, ensure_ascii=False, indent=2)


@mcp.tool()
def sync_with_web_ui(
    action: str = "read",
    highlight_property_ids: Optional[List[int]] = None,
    center_lat: Optional[float] = None,
    center_lon: Optional[float] = None,
    zoom: int = 14,
    agent_name: str = "antigravity",
    message: str = "",
) -> str:
    """
    Web UIとの双方向状態同期ツールです。
    - action='read': 人間がWeb UI上で現在表示している地図範囲・描画ポリゴン・選択中の物件IDリストを読み取ります。
    - action='focus': エージェントが注目した物件IDリストのハイライトや地図カメラの移動をWeb UIへ反映します。
    """
    init_db()
    if action == "focus":
        center_tuple = (center_lat, center_lon, zoom) if (center_lat is not None and center_lon is not None) else None
        state = record_agent_activity(
            agent_name=agent_name,
            action="地図フォーカス連動",
            summary=message or "エージェントが地図表示位置と注目物件を更新しました。",
            highlighted_property_ids=highlight_property_ids,
            map_center=center_tuple,
        )
        return json.dumps(state, ensure_ascii=False, indent=2)

    state = get_ui_sync_state()
    selected_ids = state.get("selected_property_ids", [])
    selected_props = [get_property_by_id(pid) for pid in selected_ids if get_property_by_id(pid)]
    return json.dumps(
        {
            "ui_sync_state": state,
            "selected_properties_detail": selected_props,
            "database_stats": get_database_stats(),
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def generate_property_floorplan_and_3d_tool(
    property_id: Optional[int] = None,
    land_area_sqm: float = 110.0,
    building_coverage_ratio: float = 60.0,
    floor_area_ratio: float = 200.0,
    road_width_m: float = 5.4,
    road_direction: str = "南",
    stories: Optional[int] = None,
    agent_name: str = "antigravity",
) -> str:
    """
    指定した物件番号（ID）または敷地条件から、
    1. 2D詳細敷地配置図・各階間取り平面図（SVGファイルおよびAutoCAD DXFファイル）
    2. 3D建物モデル・斜線制限エンクロージャ・冬至/夏至日影シミュレーション（Wavefront OBJおよびglTF 2.0ファイル）
    を一括生成し、出力ファイルパスと構造データを返します。
    """
    init_db()
    title = "標準敷地・建築計画モデル"
    address = "東京都港区"
    lat, lon = 35.6586, 139.7454
    if property_id is not None:
        prop = get_property_by_id(property_id)
        if prop:
            land_area_sqm = prop.get("land_area_sqm") or prop.get("building_area_sqm") or land_area_sqm
            building_coverage_ratio = prop.get("building_coverage_ratio") or building_coverage_ratio
            floor_area_ratio = prop.get("floor_area_ratio") or floor_area_ratio
            lat = prop.get("latitude") or lat
            lon = prop.get("longitude") or lon
            title = prop.get("title") or title
            address = prop.get("address") or address

    model = generate_architectural_model(
        land_area_sqm=land_area_sqm,
        building_coverage_ratio=building_coverage_ratio,
        floor_area_ratio=floor_area_ratio,
        road_width_m=road_width_m,
        road_direction=road_direction,
        stories=stories,
        latitude=lat,
        longitude=lon,
        title=title,
        address=address,
    )
    svg_paths = []
    for fl in model["building"]["floors"]:
        fnum = fl["floor_number"]
        p = EXPORT_DIR / f"property_floor_{fnum}.svg"
        p.write_text(export_property_svg(model, floor_number=fnum), encoding="utf-8")
        svg_paths.append(str(p))

    dxf_path = export_dxf_file(model, "property_plan.dxf")
    obj_path = export_obj_file(model, "property_model.obj")
    gltf_path = export_gltf_file(model, "property_model.gltf")

    record_agent_activity(
        agent_name=agent_name,
        action="2D建築平面図・3Dモデル出力",
        summary=f"「{title}」の2D平面図（SVG/DXF）と3D建築モデル（OBJ/glTF）を生成しました。",
        highlighted_property_ids=[property_id] if property_id else [],
    )
    return json.dumps(
        {
            "title": title,
            "address": address,
            "site": model["site"],
            "building_summary": {
                "stories": model["building"]["stories"],
                "total_height_m": model["building"]["total_height_m"],
                "footprint_area_sqm": model["building"]["footprint_area_sqm"],
                "total_floor_area_sqm": model["building"]["total_floor_area_sqm"],
            },
            "exported_files": {
                "svg_files": svg_paths,
                "dxf_file": str(dxf_path),
                "obj_file": str(obj_path),
                "gltf_file": str(gltf_path),
            },
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
async def generate_japan_urban_and_terrain_3d_tool(
    property_id: Optional[int] = None,
    latitude: float = 35.6586,
    longitude: float = 139.7454,
    radius_m: int = 220,
    zoom: int = 15,
    agent_name: str = "antigravity",
) -> str:
    """
    日本全国の任意座標または物件番号（ID）周辺について、
    国土地理院の実測標高タイル（DEM）とOpenStreetMapの実在建物群・道路網から
    2D街区・等高線CAD平面図（SVG/DXF）および3D都市・地形モデル（OBJ/glTF 2.0）を生成します。
    """
    init_db()
    if property_id is not None:
        prop = get_property_by_id(property_id)
        if prop and prop.get("latitude") and prop.get("longitude"):
            latitude = prop["latitude"]
            longitude = prop["longitude"]

    urban = await fetch_urban_3d_scene(lat=latitude, lon=longitude, radius_m=radius_m, zoom=zoom)
    svg_path = EXPORT_DIR / "urban_plan.svg"
    svg_path.write_text(export_urban_svg(urban), encoding="utf-8")
    dxf_path = export_dxf_file(urban, "urban_plan.dxf")
    obj_path = export_obj_file(urban, "urban_model.obj")
    gltf_path = export_gltf_file(urban, "urban_model.gltf")

    record_agent_activity(
        agent_name=agent_name,
        action="3D都市・地形・2D等高線図生成",
        summary=f"北緯{latitude:.4f}度・東経{longitude:.4f}度周辺（建物{urban['buildings_count']}棟）の2D平面図と3D都市地形モデルを生成しました。",
        highlighted_property_ids=[property_id] if property_id else [],
    )
    return json.dumps(
        {
            "coordinates": urban["coordinates"],
            "terrain_source": urban["terrain"]["source"],
            "min_elevation_m": urban["terrain"]["min_elevation_m"],
            "max_elevation_m": urban["terrain"]["max_elevation_m"],
            "buildings_count": urban["buildings_count"],
            "roads_count": urban["roads_count"],
            "exported_files": {
                "svg_file": str(svg_path),
                "dxf_file": str(dxf_path),
                "obj_file": str(obj_path),
                "gltf_file": str(gltf_path),
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def main() -> None:
    init_db()
    mcp.run()


if __name__ == "__main__":
    main()

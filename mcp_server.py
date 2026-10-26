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

mcp = FastMCP(
    "jp-property-finder",
    instructions=(
        "日本全国の不動産・土地・戸建て・マンション・空き家・収益物件・賃貸を網羅的に検索・空間分析する"
        "ネイティブMCPサーバーです。Claude、Codex、Antigravityから直接呼び出し可能で、"
        "Web UIおよびローカルSQLiteデータベースとリアルタイムに状態を共有します。"
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


def main() -> None:
    init_db()
    mcp.run()


if __name__ == "__main__":
    main()

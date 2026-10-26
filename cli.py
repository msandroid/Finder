import argparse
import asyncio
import json
import sys
from typing import List, Optional

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
)
from backend.analyzer import (
    format_yen_jp,
    generate_comparison_report,
    simulate_investment_yield,
    simulate_land_building_volume,
)


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def _print_output(data: object, fmt: str = "json") -> None:
    if fmt == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if isinstance(data, dict) and "markdown_report" in data:
        print(data["markdown_report"])
        return

    if isinstance(data, dict) and "properties" in data:
        props = data["properties"]
        print(f"[情報] 該当物件数: {len(props)}件")
        print("-" * 96)
        for p in props[:40]:
            price_str = format_yen_jp(p["price"])
            tsubo_str = f"{format_yen_jp(p.get('price_per_tsubo'))}/坪" if p.get("price_per_tsubo") else "-"
            area_str = (
                f"土地{p['land_area_sqm']}平米"
                if p.get("land_area_sqm")
                else (f"建物{p['building_area_sqm']}平米" if p.get("building_area_sqm") else "-")
            )
            dev = p.get("market_deviation_pct")
            dev_str = f"相場比 {dev:+.1f}%" if dev is not None else ""
            sites = "・".join(p.get("source_sites", [p.get("primary_source_site", "")]))
            print(
                f"[物件番号 {p['id']:>4}] [{p['category']}] {price_str} ({tsubo_str}) | {area_str} | "
                f"{p['address']} | {p['title'][:28]} | {dev_str} [{sites}]"
            )
        return

    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> None:
    _configure_stdout()
    init_db()

    parser = argparse.ArgumentParser(
        prog="propfinder",
        description="日本全国不動産・土地網羅検索プラットフォーム CLI（Claude / Codex / Antigravity ネイティブ対応）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. search (ライブ取得またはDB検索)
    p_search = subparsers.add_parser("search", help="指定エリア・条件でリアルタイム横断検索およびDB名寄せを実行")
    p_search.add_argument("--prefecture", "-p", default="東京都", help="都道府県名またはJISコード（01〜47）")
    p_search.add_argument("--municipality", "-m", default=None, help="市区町村名または5桁JISコード")
    p_search.add_argument("--category", "-c", default="売地", help="売地 / 戸建て / マンション / 空き家・古民家 / 収益物件 / 賃貸 / すべて")
    p_search.add_argument("--keyword", "-k", default=None, help="絞り込みキーワード")
    p_search.add_argument("--agent", default="antigravity", help="呼び出し元エージェント名（claude / codex / antigravity）")
    p_search.add_argument("--format", "-f", choices=["json", "text"], default="text")

    # 2. query (ローカルDB高速検索)
    p_query = subparsers.add_parser("query", help="ローカルDB内の蓄積データを高速フィルタ検索")
    p_query.add_argument("--prefecture", "-p", default=None)
    p_query.add_argument("--municipality", "-m", default=None)
    p_query.add_argument("--category", "-c", default=None)
    p_query.add_argument("--min-price", type=int, default=None)
    p_query.add_argument("--max-price", type=int, default=None)
    p_query.add_argument("--min-land-area", type=float, default=None)
    p_query.add_argument("--max-walk", type=int, default=None)
    p_query.add_argument("--keyword", "-k", default=None)
    p_query.add_argument("--only-undervalued", action="store_true", help="周辺相場より割安な物件のみ抽出")
    p_query.add_argument("--sort-by", default="price_asc")
    p_query.add_argument("--limit", type=int, default=30)
    p_query.add_argument("--format", "-f", choices=["json", "text"], default="text")

    # 3. analyze (ハザード・標高・周辺施設解析)
    p_analyze = subparsers.add_parser("analyze", help="物件の標高・ハザードマップ・周辺施設をリアルタイム解析")
    p_analyze.add_argument("--property-id", "-i", type=int, required=True)
    p_analyze.add_argument("--agent", default="antigravity")
    p_analyze.add_argument("--format", "-f", choices=["json", "text"], default="json")

    # 4. evaluate (エージェント評価登録)
    p_eval = subparsers.add_parser("evaluate", help="物件に対するAIエージェントの評価スコアと所見を記録")
    p_eval.add_argument("--property-id", "-i", type=int, required=True)
    p_eval.add_argument("--agent", default="antigravity", help="claude / codex / antigravity")
    p_eval.add_argument("--score", type=int, required=True, help="0〜100の評価点")
    p_eval.add_argument("--recommendation", default="推奨", help="強く推奨 / 推奨 / 要検討 / 見送り")
    p_eval.add_argument("--pros", default="")
    p_eval.add_argument("--cons", default="")
    p_eval.add_argument("--notes", default="")

    # 5. compare (複数物件比較レポート)
    p_comp = subparsers.add_parser("compare", help="複数物件の比較レポートを生成")
    p_comp.add_argument("--ids", nargs="+", type=int, required=True, help="比較する物件番号リスト")
    p_comp.add_argument("--format", "-f", choices=["json", "markdown"], default="markdown")

    # 6. simulate-volume (建築ボリューム試算)
    p_vol = subparsers.add_parser("simulate-volume", help="土地の最大建築面積・延床面積・概算建築費を試算")
    p_vol.add_argument("--land-area", type=float, required=True, help="土地面積（平米）")
    p_vol.add_argument("--coverage", type=float, default=60.0, help="指定建蔽率（パーセント）")
    p_vol.add_argument("--far", type=float, default=200.0, help="指定容積率（パーセント）")
    p_vol.add_argument("--road-width", type=float, default=6.0, help="前面道路幅員（メートル）")
    p_vol.add_argument("--structure", default="木造", help="木造 / 重量鉄骨造 / 鉄筋コンクリート造")

    # 7. simulate-yield (投資利回り・収支試算)
    p_yld = subparsers.add_parser("simulate-yield", help="不動産投資の表面/実質利回り・キャッシュフローを試算")
    p_yld.add_argument("--price", type=int, required=True, help="物件価格（円）")
    p_yld.add_argument("--monthly-rent", type=int, required=True, help="想定月額賃料（円）")
    p_yld.add_argument("--build-cost", type=int, default=0, help="建築または改装費（円）")
    p_yld.add_argument("--down-payment", type=int, default=0, help="自己資金（円）")
    p_yld.add_argument("--interest", type=float, default=1.8, help="融資金利（パーセント）")
    p_yld.add_argument("--years", type=int, default=30, help="融資期間（年）")

    # 8. ui-context (Web UIとの同期状態確認・操作)
    p_ui = subparsers.add_parser("ui-context", help="Web UIの現在の選択物件・地図範囲・統計を取得または更新")
    p_ui.add_argument("--highlight-ids", nargs="*", type=int, default=None, help="Web UI上でハイライトする物件番号")
    p_ui.add_argument("--agent", default="antigravity")
    p_ui.add_argument("--message", default="")

    args = parser.parse_args()

    if args.command == "search":
        res = asyncio.run(
            live_search_and_sync(
                prefecture=args.prefecture,
                municipality=args.municipality,
                category=args.category,
                keyword=args.keyword,
                agent_name=args.agent,
            )
        )
        _print_output(res, fmt=args.format)

    elif args.command == "query":
        pref_code = None
        muni_name = args.municipality
        if args.prefecture:
            loc = resolve_location(args.prefecture, args.municipality)
            if loc["prefecture"]:
                pref_code = loc["prefecture"]["code"]
            if loc["municipality"]:
                muni_name = loc["municipality"]["muni_name"]
        items = query_properties(
            prefecture_code=pref_code,
            municipality_name=muni_name,
            category=args.category,
            min_price=args.min_price,
            max_price=args.max_price,
            min_land_area_sqm=args.min_land_area,
            max_walk_min=args.max_walk,
            keyword=args.keyword,
            only_undervalued=args.only_undervalued,
            sort_by=args.sort_by,
            limit=args.limit,
        )
        _print_output({"count": len(items), "properties": items}, fmt=args.format)

    elif args.command == "analyze":
        prop = get_property_by_id(args.property_id)
        if not prop:
            print(json.dumps({"error": f"物件番号 {args.property_id} が見つかりません。"}, ensure_ascii=False))
            sys.exit(1)

        async def _run_analysis():
            lat, lon = prop.get("latitude"), prop.get("longitude")
            if lat is None or lon is None:
                geo = await geocode_address(prop["address"])
                lat, lon = (geo["latitude"], geo["longitude"]) if geo else (35.6894, 139.6917)
            hz = await analyze_hazard_and_topography(lat, lon)
            fac = await fetch_surrounding_facilities(lat, lon, radius_m=800)
            record_agent_activity(
                agent_name=args.agent,
                action="空間・ハザード解析",
                summary=f"CLIより物件番号 {args.property_id}（{prop['title']}）の空間解析を実行しました。",
                highlighted_property_ids=[args.property_id],
                map_center=(lat, lon, 16),
            )
            return {
                "property": prop,
                "hazard_and_topography": hz,
                "surrounding_facilities": fac,
            }

        out = asyncio.run(_run_analysis())
        _print_output(out, fmt="json")

    elif args.command == "evaluate":
        ev = add_agent_evaluation(
            property_id=args.property_id,
            agent_name=args.agent,
            score=args.score,
            recommendation=args.recommendation,
            pros=args.pros,
            cons=args.cons,
            notes=args.notes,
        )
        _print_output(ev, fmt="json")

    elif args.command == "compare":
        rep = generate_comparison_report(args.ids)
        _print_output(rep, fmt="markdown" if args.format == "markdown" else "json")

    elif args.command == "simulate-volume":
        res = simulate_land_building_volume(
            land_area_sqm=args.land_area,
            building_coverage_ratio=args.coverage,
            floor_area_ratio=args.far,
            road_width_m=args.road_width,
            structure_type=args.structure,
        )
        _print_output(res, fmt="json")

    elif args.command == "simulate-yield":
        res = simulate_investment_yield(
            price_yen=args.price,
            expected_monthly_rent_yen=args.monthly_rent,
            renovation_or_build_cost_yen=args.build_cost,
            down_payment_yen=args.down_payment,
            loan_interest_rate_pct=args.interest,
            loan_years=args.years,
        )
        _print_output(res, fmt="json")

    elif args.command == "ui-context":
        if args.highlight_ids:
            record_agent_activity(
                agent_name=args.agent,
                action="注目物件ハイライト",
                summary=args.message or f"物件番号 {args.highlight_ids} をハイライトしました。",
                highlighted_property_ids=args.highlight_ids,
            )
        state = get_ui_sync_state()
        stats = get_database_stats()
        _print_output({"ui_sync_state": state, "database_stats": stats}, fmt="json")


if __name__ == "__main__":
    main()

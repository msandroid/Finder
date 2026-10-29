import math
from typing import Any, Dict, List, Optional

from backend.connectors.parser_utils import TSUBO_PER_SQM
from backend.database import get_property_by_id


def simulate_land_building_volume(
    land_area_sqm: float,
    building_coverage_ratio: float = 60.0,
    floor_area_ratio: float = 200.0,
    road_width_m: float = 6.0,
    zoning_category: str = "住居系",
    corner_lot_relaxation: bool = False,
    structure_type: str = "木造",
) -> Dict[str, Any]:
    """
    土地面積・建蔽率・容積率・前面道路幅員・角地緩和・構造種別から、
    建築基準法に基づく実効建蔽率・実効容積率・最大建築面積・最大延床面積・概算建築費を試算する。
    """
    eff_coverage = building_coverage_ratio + (10.0 if corner_lot_relaxation else 0.0)
    eff_coverage = min(eff_coverage, 80.0 if building_coverage_ratio < 80.0 else 100.0)

    # 前面道路幅員（12m未満）による容積率制限（住居系: 幅員×40%、商業・工業系: 幅員×60%）
    if road_width_m < 12.0:
        factor = 40.0 if "住居" in zoning_category else 60.0
        road_limited_far = road_width_m * factor
        eff_far = min(floor_area_ratio, road_limited_far)
    else:
        eff_far = floor_area_ratio

    max_footprint_sqm = round(land_area_sqm * (eff_coverage / 100.0), 2)
    max_total_floor_sqm = round(land_area_sqm * (eff_far / 100.0), 2)
    recommended_floors = max(1, math.ceil(max_total_floor_sqm / max_footprint_sqm)) if max_footprint_sqm > 0 else 1
    rentable_floor_sqm = round(max_total_floor_sqm * 0.85, 2)

    # 構造別の実勢建築坪単価目安（円/坪）
    unit_cost_per_tsubo_map = {
        "木造": 950_000,
        "重量鉄骨造": 1_250_000,
        "鉄筋コンクリート造": 1_450_000,
    }
    cost_per_tsubo = unit_cost_per_tsubo_map.get(structure_type, 1_050_000)
    total_floor_tsubo = round(max_total_floor_sqm * TSUBO_PER_SQM, 2)
    estimated_build_cost_yen = int(round(total_floor_tsubo * cost_per_tsubo))

    return {
        "input": {
            "land_area_sqm": land_area_sqm,
            "land_area_tsubo": round(land_area_sqm * TSUBO_PER_SQM, 2),
            "specified_coverage_ratio_pct": building_coverage_ratio,
            "specified_floor_area_ratio_pct": floor_area_ratio,
            "road_width_m": road_width_m,
            "zoning_category": zoning_category,
            "corner_lot_relaxation": corner_lot_relaxation,
            "structure_type": structure_type,
        },
        "result": {
            "effective_coverage_ratio_pct": round(eff_coverage, 1),
            "effective_floor_area_ratio_pct": round(eff_far, 1),
            "max_building_footprint_sqm": max_footprint_sqm,
            "max_building_footprint_tsubo": round(max_footprint_sqm * TSUBO_PER_SQM, 2),
            "max_total_floor_area_sqm": max_total_floor_sqm,
            "max_total_floor_area_tsubo": total_floor_tsubo,
            "estimated_rentable_area_sqm": rentable_floor_sqm,
            "recommended_stories": recommended_floors,
            "estimated_construction_cost_yen": estimated_build_cost_yen,
            "construction_cost_per_tsubo_yen": cost_per_tsubo,
        },
    }


def simulate_investment_yield(
    price_yen: int,
    expected_monthly_rent_yen: int,
    renovation_or_build_cost_yen: int = 0,
    acquisition_expense_rate_pct: float = 7.0,
    vacancy_rate_pct: float = 5.0,
    opex_rate_pct: float = 15.0,
    down_payment_yen: int = 0,
    loan_interest_rate_pct: float = 1.8,
    loan_years: int = 30,
) -> Dict[str, Any]:
    """
    物件取得価格・想定月額賃料・建築/改修費・諸費用率・空室率・運営経費率・自己資金・借入金利・借入期間から、
    表面利回り・実質利回り・NOI（純営業収益）・ローン返済額・税引前キャッシュフロー・自己資金配当率（CCR）を算出する。
    """
    acquisition_expenses = int(round(price_yen * (acquisition_expense_rate_pct / 100.0)))
    total_project_cost = price_yen + renovation_or_build_cost_yen + acquisition_expenses
    loan_principal = max(0, total_project_cost - down_payment_yen)

    annual_full_rent = expected_monthly_rent_yen * 12
    effective_annual_rent = int(round(annual_full_rent * (1.0 - vacancy_rate_pct / 100.0)))
    annual_opex = int(round(annual_full_rent * (opex_rate_pct / 100.0)))
    noi = effective_annual_rent - annual_opex

    base_cost = price_yen + renovation_or_build_cost_yen
    gross_yield_pct = round((annual_full_rent / base_cost) * 100.0, 2) if base_cost > 0 else 0.0
    net_yield_pct = round((noi / total_project_cost) * 100.0, 2) if total_project_cost > 0 else 0.0

    # 元利均等月次返済額
    monthly_payment = 0
    if loan_principal > 0 and loan_years > 0:
        months = loan_years * 12
        r = (loan_interest_rate_pct / 100.0) / 12.0
        if r > 0:
            monthly_payment = int(
                round(loan_principal * (r * (1.0 + r) ** months) / ((1.0 + r) ** months - 1.0))
            )
        else:
            monthly_payment = int(round(loan_principal / months))

    annual_debt_service = monthly_payment * 12
    annual_cash_flow = noi - annual_debt_service
    monthly_cash_flow = int(round(annual_cash_flow / 12.0))

    ccr_pct = (
        round((annual_cash_flow / down_payment_yen) * 100.0, 2)
        if down_payment_yen > 0
        else None
    )
    payback_years = (
        round(total_project_cost / noi, 1)
        if noi > 0
        else None
    )

    return {
        "summary": {
            "property_price_yen": price_yen,
            "renovation_or_build_cost_yen": renovation_or_build_cost_yen,
            "acquisition_expenses_yen": acquisition_expenses,
            "total_investment_yen": total_project_cost,
            "down_payment_yen": down_payment_yen,
            "loan_principal_yen": loan_principal,
            "annual_gross_income_yen": annual_full_rent,
            "annual_effective_income_yen": effective_annual_rent,
            "annual_operating_expenses_yen": annual_opex,
            "annual_noi_yen": noi,
            "monthly_loan_payment_yen": monthly_payment,
            "annual_debt_service_yen": annual_debt_service,
            "annual_cash_flow_yen": annual_cash_flow,
            "monthly_cash_flow_yen": monthly_cash_flow,
            "gross_yield_pct": gross_yield_pct,
            "net_yield_pct": net_yield_pct,
            "equity_ccr_pct": ccr_pct,
            "payback_years": payback_years,
        }
    }


def format_yen_jp(yen: Optional[int]) -> str:
    if yen is None:
        return "未定"
    if yen >= 100_000_000:
        oku = yen // 100_000_000
        man = (yen % 100_000_000) // 10_000
        return f"{oku}億{man:,}万円" if man > 0 else f"{oku}億円"
    if yen >= 10_000:
        return f"{yen // 10_000:,}万円"
    return f"{yen:,}円"


def generate_comparison_report(property_ids: List[int]) -> Dict[str, Any]:
    """指定された複数物件の詳細・単価・相場乖離・エージェント評価を比較する構造化データおよび純日本語レポートを生成する。"""
    props = []
    for pid in property_ids:
        p = get_property_by_id(pid)
        if p:
            props.append(p)

    if not props:
        return {
            "count": 0,
            "properties": [],
            "markdown_report": "比較対象の物件が見つかりませんでした。",
        }

    lines = [
        "# 不動産・土地 横断比較分析レポート",
        "",
        f"対象物件数：{len(props)}件",
        "",
        "| 物件番号 | 種別 | 物件名 | 所在地 | 価格 | 坪単価 | 土地面積 | 建物/専有面積 | 最寄駅・徒歩 | 周辺相場比 | 想定利回り | 掲載元 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for p in props:
        price_str = format_yen_jp(p["price"])
        tsubo_str = format_yen_jp(p.get("price_per_tsubo")) + "/坪" if p.get("price_per_tsubo") else "-"
        land_str = f"{p['land_area_sqm']}平米（{p['land_area_tsubo']}坪）" if p.get("land_area_sqm") else "-"
        bld_str = f"{p['building_area_sqm']}平米" if p.get("building_area_sqm") else "-"
        st_str = (
            f"{p['nearest_station']}駅 徒歩{p['station_walk_min']}分"
            if p.get("nearest_station") and p.get("station_walk_min")
            else (p.get("nearest_station") or "-")
        )
        dev = p.get("market_deviation_pct")
        dev_str = f"{dev:+.1f}%" if dev is not None else "-"
        yld = p.get("estimated_gross_yield_pct")
        yld_str = f"{yld:.2f}%" if yld is not None else "-"
        sites_str = "・".join(p.get("source_sites", [p.get("primary_source_site", "")]))

        lines.append(
            f"| {p['id']} | {p['category']} | {p['title']} | {p['address']} | {price_str} | "
            f"{tsubo_str} | {land_str} | {bld_str} | {st_str} | {dev_str} | {yld_str} | {sites_str} |"
        )

    lines.append("")
    lines.append("## 各物件のエージェント評価・調査所見")
    for p in props:
        lines.append(f"### 物件番号 {p['id']}：{p['title']}（{p['address']}）")
        evals = p.get("agent_evaluations", [])
        if not evals:
            lines.append("- エージェント評価：未登録")
        else:
            for ev in evals:
                lines.append(
                    f"- **{ev['agent_name']}**（評価点：{ev['score']}点 / 判定：{ev['recommendation']}）"
                )
                if ev.get("pros"):
                    lines.append(f"  - 強み・利点：{ev['pros']}")
                if ev.get("cons"):
                    lines.append(f"  - 懸念点・注意点：{ev['cons']}")
                if ev.get("notes"):
                    lines.append(f"  - 詳細所見：{ev['notes']}")
        lines.append("")

    return {
        "count": len(props),
        "properties": props,
        "markdown_report": "\n".join(lines),
    }

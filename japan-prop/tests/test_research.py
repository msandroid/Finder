"""調査ハーネス（backend/research.py）のテスト。ネットワークを使わずに動く。"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.research import (
    NON_PUBLIC_NOTICE,
    ResearchRequirements,
    apply_hard_filters,
    build_report,
    capacity_people,
    detect_issues,
    normalize_requirements,
    render_markdown,
    run_research,
    score_candidate,
)

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def _item(id_, **kw):
    base = {
        "id": id_,
        "title": f"物件{id_}",
        "category": "オフィス・事務所",
        "monthly_rent": 60000,
        "price": 60000,
        "building_area_sqm": 40.0,
        "station_walk_min": 3,
        "nearest_station": "大通",
        "station_raw": "大通駅 徒歩3分",
        "build_year": 2005,
        "market_deviation_pct": -10.0,
        "structure": "鉄筋コンクリート造・オフィス仕様",
        "features": ["事業用貸事務所"],
        "last_seen_at": (NOW - timedelta(days=1)).isoformat(),
        "primary_source_site": "テスト",
        "primary_source_url": f"https://example.com/{id_}",
    }
    base.update(kw)
    return base


def test_headcount_becomes_area_range_and_is_recorded_as_assumption():
    req, assumptions = normalize_requirements(
        ResearchRequirements(category="事務所", headcount_min=2, headcount_max=10)
    )
    assert req.category == "オフィス・事務所"
    assert req.use_case == "office"
    assert req.min_area_sqm == pytest.approx(19.8, abs=0.1)  # 2人 × 3坪
    assert req.max_area_sqm == pytest.approx(165.3, abs=0.1)  # 10人 × 5坪
    assert any("2〜10人" in a for a in assumptions)


def test_hard_filters_count_why_items_were_dropped():
    req, _ = normalize_requirements(
        ResearchRequirements(category="オフィス", headcount_min=2, headcount_max=4, max_monthly_cost=70000,
                             max_walk_min=5, must_have=["24時間"])
    )
    items = [
        _item(1, features=["24時間利用可能"]),
        _item(2, building_area_sqm=10.0),
        _item(3, monthly_rent=90000, price=90000),
        _item(4, station_walk_min=12),
        _item(5),
    ]
    kept, dropped = apply_hard_filters(items, req)
    assert [it["id"] for it in kept] == [1]
    assert dropped == {
        "面積が下限未満または不明": 1,
        "予算超えまたは賃料不明": 1,
        "駅徒歩が上限超えまたは不明": 1,
        "必須条件の記載なし": 1,
    }


def test_scoring_prefers_cheaper_closer_dedicated_offices():
    req, _ = normalize_requirements(
        ResearchRequirements(category="オフィス", use_case="software_dev", headcount_min=2, headcount_max=4,
                             max_monthly_cost=100000)
    )
    good = score_candidate(_item(1, market_deviation_pct=-40, station_walk_min=2,
                                 features=["事業用貸事務所", "24時間利用可能"]), req, NOW)
    poor = score_candidate(_item(2, market_deviation_pct=40, station_walk_min=14, monthly_rent=95000,
                                 price=95000, structure="鉄筋コンクリート造（事務所利用可）",
                                 features=["事務所利用可"]), req, NOW)
    assert good["score"] > poor["score"]
    assert 0 <= poor["score"] <= good["score"] <= 100
    assert "記載あり: 24時間" in good["reasons"]


def test_detect_issues_finds_title_area_mismatch_stale_data_and_outliers():
    issues = detect_issues(
        _item(1, title="貸事務所（14.34坪）", building_area_sqm=40.8, build_year=None,
              market_deviation_pct=200.0, last_seen_at=(NOW - timedelta(days=20)).isoformat()),
        NOW,
    )
    text = " ".join(issues)
    assert "14.34坪" in text and "食い違って" in text
    assert "築年" in text
    assert "相場より200%高く" in text
    assert "20日前" in text
    assert detect_issues(_item(2, title="貸事務所（12.1坪）"), NOW) == []


def test_capacity_estimate():
    assert capacity_people(_item(1, building_area_sqm=48.2)) == "2〜4人"  # 14.6坪


def test_report_flags_missing_large_band_and_suggests_brokers():
    req, assumptions = normalize_requirements(
        ResearchRequirements(category="オフィス", use_case="software_dev", headcount_min=2, headcount_max=10)
    )
    coverage = [{"area": "テスト区", "live": "ok", "in_database": 2, "newest_age_days": 1, "oldest_age_days": 1}]
    report = build_report(req, assumptions, [_item(1), _item(2, building_area_sqm=30.0)], coverage, NOW)
    assert {r["id"] for r in report["shortlist"]} == {1, 2}
    assert any("10人規模" in g for g in report["gaps"])
    assert report["non_public_listings"] == NON_PUBLIC_NOTICE
    assert any("非公開物件" in a for a in report["next_actions"])
    assert any("光回線" in c for c in report["checklist"])
    md = render_markdown(report)
    for heading in ("## 前提", "## 収集状況", "## 候補", "## 足りない範囲", "## 非公開物件について", "## 次にやること"):
        assert heading in md


def test_report_suggests_relaxing_the_condition_that_dropped_most():
    req, assumptions = normalize_requirements(
        ResearchRequirements(category="オフィス", max_monthly_cost=10000)
    )
    report = build_report(req, assumptions, [_item(1), _item(2)], [], NOW)
    assert report["shortlist"] == []
    assert any("予算超え" in a and "緩める" in a for a in report["next_actions"])


async def test_run_research_falls_back_to_database_when_live_search_fails():
    async def failing_fetch(prefecture, municipality, category):
        raise ConnectionError("proxy refused")

    report = await run_research(
        ResearchRequirements(prefecture="北海道", municipalities=["札幌市中央区"], category="オフィス",
                             use_case="software_dev", headcount_min=2, headcount_max=10),
        live_fetch=failing_fetch,
    )
    cov = report["coverage"][0]
    assert cov["live"].startswith("失敗: ConnectionError")
    assert cov["in_database"] > 0, "同梱DBの札幌オフィスデータで調査を続けられること"
    assert report["shortlist"], "DBの過去データから候補が出ること"
    assert "ネットワークのある環境で再調査" in report["next_actions"][0]


async def test_run_research_records_live_results():
    calls = []

    async def ok_fetch(prefecture, municipality, category):
        calls.append((prefecture, municipality, category))
        return {"fetched_raw_count": 5, "source_breakdown": {"テスト": 5}, "source_errors": []}

    report = await run_research(
        ResearchRequirements(prefecture="北海道", municipalities=["札幌市中央区", "札幌市北区"], category="事務所"),
        live_fetch=ok_fetch,
    )
    assert calls == [("北海道", "札幌市中央区", "オフィス・事務所"), ("北海道", "札幌市北区", "オフィス・事務所")]
    assert [c["live"] for c in report["coverage"]] == ["ok", "ok"]

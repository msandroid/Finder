"""
物件の調査・探索ハーネス。

条件の正規化 → 収集（ライブ検索、失敗時はローカルDBのキャッシュ）→ 必須条件での絞り込み
→ 採点 → データの欠落・矛盾の検出 → 報告（鮮度・確認事項・非公開物件の注意つき）
を毎回同じ手順で行い、エージェントが調査の手順や前提を取りこぼさないようにする。

収集以外はネットワークを使わない純粋関数なので、単体でテストできる。
"""

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from backend.connectors.parser_utils import TSUBO_PER_SQM
from backend.database import query_properties
from backend.municipalities import resolve_location

SQM_PER_TSUBO = 1 / TSUBO_PER_SQM

# オフィス1人あたりの面積の目安（坪）。ゆとりのある配置〜詰めた配置
TSUBO_PER_PERSON_IDEAL = 4.0
TSUBO_PER_PERSON_MIN = 3.0
TSUBO_PER_PERSON_MAX = 5.0

# キャッシュがこの日数より古ければ「古い」と警告する
STALE_AFTER_DAYS = 7

NON_PUBLIC_NOTICE = (
    "検索対象はポータルサイトに掲載された物件だけです。日本には不動産事業者（宅地建物取引業者）しか"
    "閲覧できない非公開物件（レインズなど業者向けシステムにだけ登録された物件、貸主・売主の意向で広告"
    "されない物件、管理会社や地元の不動産会社が直接扱う物件）があります。候補が少ないとき、条件が厳しい"
    "ときは、希望エリアの不動産会社に条件を伝えて非公開物件も紹介してもらってください。"
)

OFFICE_CATEGORIES = ("オフィス・事務所", "オフィス", "事務所", "貸事務所")

# 用途ごとの採点の重みと、内見・問い合わせで確認すべき事項
USE_CASE_PROFILES: Dict[str, Dict[str, Any]] = {
    "software_dev": {
        "label": "ソフトウェア開発オフィス",
        "weights": {"cost": 0.30, "access": 0.25, "fit": 0.20, "suitability": 0.15, "freshness": 0.10},
        "prefer_keywords": ["24時間", "光回線", "インターネット", "個別空調", "エレベーター", "駐車場"],
        "checklist": [
            "光回線を自社で個別に引けるか（建物の共用回線だけではないか）",
            "電気容量（PC・モニター・サーバーを人数分つないで足りるか）",
            "24時間・土日の入館（リリース作業や障害対応）",
            "空調・暖房の方式と、光熱費が共益費に含まれるか",
            "入館セキュリティと鍵の管理",
            "住居用の部屋なら、法人登記・看板掲示の可否（管理規約）",
        ],
    },
    "office": {
        "label": "一般オフィス",
        "weights": {"cost": 0.30, "access": 0.30, "fit": 0.20, "suitability": 0.10, "freshness": 0.10},
        "prefer_keywords": ["24時間", "駐車場", "エレベーター"],
        "checklist": [
            "法人登記・看板掲示の可否",
            "空調・暖房の方式と、光熱費が共益費に含まれるか",
            "敷金（保証金）・礼金・仲介手数料などの初期費用",
            "来客用の動線と駐車場",
        ],
    },
    "residence": {
        "label": "住まい（賃貸）",
        "weights": {"cost": 0.35, "access": 0.25, "fit": 0.20, "suitability": 0.0, "freshness": 0.20},
        "prefer_keywords": ["バス・トイレ別", "オートロック", "宅配ボックス", "ペット", "南向き"],
        "checklist": [
            "敷金・礼金・仲介手数料・保証会社・更新料などの初期費用",
            "浸水・土砂災害のリスク（analyze_property_hazard_and_surroundings で確認）",
            "日当たり・騒音・周辺の生活施設（内見で確認）",
            "ペット・楽器・在宅勤務など、自分の使い方が契約上できるか",
        ],
    },
}


@dataclass
class ResearchRequirements:
    """調査条件。指定のないものは制約なしとして扱う。"""

    prefecture: str = "東京都"
    municipalities: List[str] = field(default_factory=list)
    category: str = "賃貸"
    use_case: str = "residence"
    headcount_min: Optional[int] = None
    headcount_max: Optional[int] = None
    min_area_sqm: Optional[float] = None
    max_area_sqm: Optional[float] = None
    max_monthly_cost: Optional[int] = None
    max_walk_min: Optional[int] = None
    must_have: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    shortlist_size: int = 10


def is_office_category(category: Optional[str]) -> bool:
    return (category or "") in OFFICE_CATEGORIES


def normalize_requirements(req: ResearchRequirements) -> Tuple[ResearchRequirements, List[str]]:
    """
    人数から面積の範囲を決めるなど、条件を検索できる形に直す。
    置いた前提はすべて assumptions として返し、報告に載せる。
    """
    assumptions: List[str] = []
    if is_office_category(req.category):
        req.category = "オフィス・事務所"
        if req.use_case == "residence":
            req.use_case = "office"
    if req.use_case not in USE_CASE_PROFILES:
        assumptions.append(f"用途 {req.use_case!r} は未定義のため office として採点しました。")
        req.use_case = "office"

    if req.headcount_min or req.headcount_max:
        lo = req.headcount_min or req.headcount_max
        hi = req.headcount_max or req.headcount_min
        if lo > hi:
            lo, hi = hi, lo
        req.headcount_min, req.headcount_max = lo, hi
        if req.min_area_sqm is None:
            req.min_area_sqm = round(lo * TSUBO_PER_PERSON_MIN * SQM_PER_TSUBO, 1)
        if req.max_area_sqm is None:
            req.max_area_sqm = round(hi * TSUBO_PER_PERSON_MAX * SQM_PER_TSUBO, 1)
        assumptions.append(
            f"{lo}〜{hi}人の面積を、1人あたり{TSUBO_PER_PERSON_MIN:g}〜{TSUBO_PER_PERSON_MAX:g}坪"
            f"（目安{TSUBO_PER_PERSON_IDEAL:g}坪）として {req.min_area_sqm}〜{req.max_area_sqm}㎡ と置きました。"
        )
    if req.max_monthly_cost is not None and is_office_category(req.category):
        assumptions.append("月額は掲載の共益費（管理費）込みの金額で比べています。")
    if not req.municipalities:
        assumptions.append(f"市区町村の指定がないため {req.prefecture} 全体を対象にしました。")
    return req, assumptions


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def age_days(item: Dict[str, Any], now: Optional[datetime] = None) -> Optional[float]:
    seen = _parse_iso(item.get("last_seen_at"))
    if not seen:
        return None
    now = now or datetime.now(timezone.utc)
    return round((now - seen).total_seconds() / 86400, 1)


def _text_blob(item: Dict[str, Any]) -> str:
    feats = item.get("features") or []
    return " ".join(
        str(x)
        for x in [item.get("title"), item.get("headline"), item.get("structure"), item.get("floor_plan"), *feats]
        if x
    )


def _area(item: Dict[str, Any]) -> Optional[float]:
    return item.get("building_area_sqm") or item.get("land_area_sqm")


def _monthly_cost(item: Dict[str, Any]) -> Optional[int]:
    return item.get("monthly_rent") or item.get("price")


def apply_hard_filters(
    items: List[Dict[str, Any]], req: ResearchRequirements
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """必須条件で絞り込み、条件ごとに何件落ちたかも返す（条件が厳しすぎるかの判断に使う）。"""
    kept: List[Dict[str, Any]] = []
    dropped: Dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    for it in items:
        area = _area(it)
        cost = _monthly_cost(it)
        blob = _text_blob(it)
        if req.min_area_sqm is not None and (area is None or area < req.min_area_sqm):
            drop("面積が下限未満または不明")
        elif req.max_area_sqm is not None and area is not None and area > req.max_area_sqm:
            drop("面積が上限超え")
        elif req.max_monthly_cost is not None and (cost is None or cost > req.max_monthly_cost):
            drop("予算超えまたは賃料不明")
        elif req.max_walk_min is not None and (
            it.get("station_walk_min") is None or it["station_walk_min"] > req.max_walk_min
        ):
            drop("駅徒歩が上限超えまたは不明")
        elif any(k not in blob for k in req.must_have):
            drop("必須条件の記載なし")
        elif any(k in blob for k in req.exclude):
            drop("除外条件に該当")
        else:
            kept.append(it)
    return kept, dropped


_TITLE_TSUBO = re.compile(r"([\d.]+)坪")


def detect_issues(item: Dict[str, Any], now: Optional[datetime] = None) -> List[str]:
    """掲載データの欠落や矛盾を見つける。報告では「要確認」として出す。"""
    issues: List[str] = []
    area = item.get("building_area_sqm")
    m = _TITLE_TSUBO.search(item.get("title") or "")
    if m and area:
        title_tsubo = float(m.group(1))
        actual_tsubo = area * TSUBO_PER_SQM
        if abs(title_tsubo - actual_tsubo) > max(0.5, actual_tsubo * 0.05):
            issues.append(
                f"タイトルの面積（{title_tsubo:g}坪）と面積欄（{actual_tsubo:.2f}坪）が食い違っています"
            )
    if not item.get("station_walk_min"):
        issues.append("最寄り駅・徒歩分数の記載がありません")
    if not item.get("build_year"):
        issues.append("築年の記載がありません")
    dev = item.get("market_deviation_pct")
    if dev is not None and dev > 100:
        issues.append(f"周辺相場より{dev:.0f}%高く、金額の誤記や特殊な物件の可能性があります")
    if dev is not None and dev < -50:
        issues.append(f"周辺相場より{-dev:.0f}%安く、条件（定期借家・事故物件など）を確認してください")
    age = age_days(item, now)
    if age is not None and age > STALE_AFTER_DAYS:
        issues.append(f"{age:.0f}日前の取得データです。成約済みの可能性があります")
    return issues


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def score_candidate(
    item: Dict[str, Any], req: ResearchRequirements, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """0〜100点で採点し、観点ごとの内訳と理由を返す。"""
    profile = USE_CASE_PROFILES[req.use_case]
    weights = profile["weights"]
    parts: Dict[str, float] = {}
    reasons: List[str] = []

    # 費用: 予算に対する余裕と、周辺相場との乖離
    cost = _monthly_cost(item)
    dev = item.get("market_deviation_pct")
    budget_part = 0.5
    if cost and req.max_monthly_cost:
        budget_part = _clamp(1 - cost / req.max_monthly_cost + 0.3)
    market_part = 0.5 if dev is None else _clamp(0.5 - dev / 100)
    parts["cost"] = 0.5 * budget_part + 0.5 * market_part
    if dev is not None and dev <= -15:
        reasons.append(f"周辺相場より{-dev:.0f}%安い")

    # 交通: 駅徒歩（2分以内で満点、15分で0点）
    walk = item.get("station_walk_min")
    parts["access"] = 0.3 if walk is None else _clamp(1 - (walk - 2) / 13)
    if walk is not None and walk <= 5:
        reasons.append(f"{item.get('nearest_station') or '駅'} 徒歩{walk}分")

    # 広さ: 人数の目安に対する当てはまり
    area = _area(item)
    if area and req.headcount_max:
        tsubo = area * TSUBO_PER_SQM
        ideal = req.headcount_max * TSUBO_PER_PERSON_IDEAL
        parts["fit"] = _clamp(tsubo / ideal) if tsubo < ideal else _clamp(ideal / tsubo + 0.2)
    elif area and req.min_area_sqm:
        parts["fit"] = _clamp(area / (req.min_area_sqm * 1.5))
    else:
        parts["fit"] = 0.5

    # 用途への適性: 事務所専用か、望ましい設備の記載があるか
    blob = _text_blob(item)
    suit = 0.5
    if is_office_category(req.category):
        if "オフィス仕様" in blob or "事業用" in blob:
            suit = 0.8
            reasons.append("事務所専用の物件")
        elif "事務所利用可" in blob:
            suit = 0.5
    hits = [k for k in profile["prefer_keywords"] if k in blob]
    suit = _clamp(suit + 0.1 * len(hits))
    if hits:
        reasons.append("記載あり: " + "・".join(hits))
    parts["suitability"] = suit

    # 鮮度: 取得からの日数（当日で満点、30日で0点）
    age = age_days(item, now)
    parts["freshness"] = 0.3 if age is None else _clamp(1 - age / 30)

    total = sum(weights[k] * parts[k] for k in weights)
    return {
        "score": round(100 * total, 1),
        "breakdown": {k: round(v, 2) for k, v in parts.items()},
        "reasons": reasons,
    }


def capacity_people(item: Dict[str, Any]) -> Optional[str]:
    area = item.get("building_area_sqm")
    if not area:
        return None
    tsubo = area * TSUBO_PER_SQM
    lo = max(1, math.floor(tsubo / TSUBO_PER_PERSON_MAX))
    hi = max(lo, math.floor(tsubo / TSUBO_PER_PERSON_MIN))
    return f"{lo}〜{hi}人"


def _coverage_gaps(
    req: ResearchRequirements, pool: List[Dict[str, Any]], kept: List[Dict[str, Any]]
) -> List[str]:
    """条件の範囲のうち、候補が1件もない部分を見つける。"""
    gaps: List[str] = []
    if not kept:
        gaps.append("条件をすべて満たす物件はありませんでした。")
    if req.headcount_max and req.max_area_sqm:
        upper_band = req.headcount_max * TSUBO_PER_PERSON_MIN * SQM_PER_TSUBO
        if not any((_area(it) or 0) >= upper_band for it in kept):
            largest = max((_area(it) or 0 for it in pool), default=0)
            gaps.append(
                f"{req.headcount_max}人規模（{upper_band:.0f}㎡以上）の候補がありません。"
                f"収集できた物件で一番広いものは{largest:.0f}㎡です。"
            )
    return gaps


async def _default_live_fetch(prefecture: str, municipality: Optional[str], category: str) -> Dict[str, Any]:
    from backend.connectors.aggregator import live_search_and_sync

    return await live_search_and_sync(prefecture=prefecture, municipality=municipality, category=category)


LiveFetch = Callable[[str, Optional[str], str], Awaitable[Dict[str, Any]]]


async def collect(
    req: ResearchRequirements, live: bool = True, live_fetch: Optional[LiveFetch] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    エリアごとにライブ検索し、結果をDBへ保存したうえでDBから候補を集める。
    ライブ検索が失敗しても、DBに残っている過去の取得分で調査を続ける（鮮度は報告に出す）。
    """
    fetch = live_fetch or _default_live_fetch
    areas: List[Optional[str]] = list(req.municipalities) or [None]
    coverage: List[Dict[str, Any]] = []
    pool: Dict[int, Dict[str, Any]] = {}

    for muni in areas:
        entry: Dict[str, Any] = {"area": f"{req.prefecture}{muni or ''}", "live": "未実行（オフライン）"}
        if live:
            try:
                res = await fetch(req.prefecture, muni, req.category)
                entry["fetched"] = res.get("fetched_raw_count", 0)
                entry["sources"] = res.get("source_breakdown", {})
                if res.get("source_errors"):
                    entry["errors"] = res["source_errors"]
                entry["live"] = "ok" if entry["fetched"] else "取得0件（接続失敗または該当なし）"
            except Exception as exc:  # ネットワーク不通などでも調査は止めない
                entry["live"] = f"失敗: {type(exc).__name__}: {exc}"[:200]

        loc = resolve_location(req.prefecture, muni)
        pref_code = loc["prefecture"]["code"] if loc["prefecture"] else None
        muni_name = loc["municipality"]["muni_name"] if loc["municipality"] else muni
        cached = query_properties(
            prefecture_code=pref_code,
            municipality_name=muni_name,
            category=req.category,
            sort_by="newest",
            limit=500,
        )
        entry["in_database"] = len(cached)
        ages = [a for a in (age_days(it) for it in cached) if a is not None]
        entry["newest_age_days"] = min(ages) if ages else None
        entry["oldest_age_days"] = max(ages) if ages else None
        coverage.append(entry)
        for it in cached:
            pool[it["id"]] = it
    return list(pool.values()), coverage


def build_report(
    req: ResearchRequirements,
    assumptions: List[str],
    pool: List[Dict[str, Any]],
    coverage: List[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """絞り込み・採点・検証を行い、報告用の構造にまとめる。"""
    kept, dropped = apply_hard_filters(pool, req)
    ranked = []
    for it in kept:
        sc = score_candidate(it, req, now)
        ranked.append(
            {
                "id": it["id"],
                "title": it.get("title"),
                "address": it.get("address"),
                "monthly_cost": _monthly_cost(it),
                "area_sqm": _area(it),
                "area_tsubo": round(_area(it) * TSUBO_PER_SQM, 2) if _area(it) else None,
                "capacity": capacity_people(it),
                "station": it.get("station_raw") or it.get("nearest_station"),
                "market_deviation_pct": it.get("market_deviation_pct"),
                "source": it.get("primary_source_site"),
                "url": it.get("primary_source_url"),
                "last_seen_at": it.get("last_seen_at"),
                "score": sc["score"],
                "score_breakdown": sc["breakdown"],
                "reasons": sc["reasons"],
                "issues": detect_issues(it, now),
            }
        )
    ranked.sort(key=lambda r: r["score"], reverse=True)

    stale = any(
        (c.get("newest_age_days") or 0) > STALE_AFTER_DAYS or c["live"] != "ok" for c in coverage
    )
    next_actions = [
        "候補の掲載元 URL を開き、空室と最新の条件を確認する",
        "気になる物件は analyze_property_hazard_and_surroundings で災害リスクと周辺施設を調べる",
    ]
    if stale:
        next_actions.insert(0, "ライブ検索が使えなかった、またはデータが古いので、ネットワークのある環境で再調査する")
    gaps = _coverage_gaps(req, pool, kept)
    if len(kept) < 3 and dropped:
        reason, n = max(dropped.items(), key=lambda kv: kv[1])
        next_actions.append(f"いちばん多く除外した条件は「{reason}」（{n}件）です。この条件を緩めると候補が増えます")
    if gaps or len(kept) < 3:
        next_actions.append("候補が足りない範囲は、希望エリアの不動産会社に非公開物件の紹介を頼む")

    return {
        "requirements": asdict(req),
        "assumptions": assumptions,
        "coverage": coverage,
        "funnel": {"collected": len(pool), "passed_filters": len(kept), "dropped": dropped},
        "shortlist": ranked[: req.shortlist_size],
        "gaps": gaps,
        "checklist": USE_CASE_PROFILES[req.use_case]["checklist"],
        "non_public_listings": NON_PUBLIC_NOTICE,
        "next_actions": next_actions,
    }


async def run_research(
    req: ResearchRequirements, live: bool = True, live_fetch: Optional[LiveFetch] = None
) -> Dict[str, Any]:
    req, assumptions = normalize_requirements(req)
    pool, coverage = await collect(req, live=live, live_fetch=live_fetch)
    return build_report(req, assumptions, pool, coverage)


def _yen(v: Optional[int]) -> str:
    return f"{v:,}円" if v else "不明"


def render_markdown(report: Dict[str, Any]) -> str:
    """報告を Markdown にする。エージェントがそのまま利用者に見せられる形。"""
    req = report["requirements"]
    label = USE_CASE_PROFILES[req["use_case"]]["label"]
    lines = [f"# 物件調査: {req['prefecture']}{'・'.join(req['municipalities'])}（{label}）", ""]

    lines += ["## 前提", *[f"- {a}" for a in report["assumptions"]], ""]

    lines += ["## 収集状況", "| エリア | ライブ検索 | DB件数 | 最新データ |", "|---|---|---|---|"]
    for c in report["coverage"]:
        newest = "なし" if c["newest_age_days"] is None else f"{c['newest_age_days']:g}日前"
        lines.append(f"| {c['area']} | {c['live']} | {c['in_database']} | {newest} |")
    f = report["funnel"]
    dropped = "、".join(f"{k} {v}件" for k, v in f["dropped"].items()) or "なし"
    lines += ["", f"収集 {f['collected']}件 → 条件に合う {f['passed_filters']}件（除外: {dropped}）", ""]

    lines += ["## 候補", "| # | 物件 | 月額 | 広さ | 目安人数 | 駅 | 点数 | 理由 | 要確認 |", "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(report["shortlist"], 1):
        title = f"[{r['title']}]({r['url']})" if r.get("url") else r["title"]
        area = f"{r['area_tsubo']}坪" if r.get("area_tsubo") else "不明"
        lines.append(
            f"| {i} | {title} | {_yen(r['monthly_cost'])} | {area} | {r['capacity'] or '-'} | "
            f"{r['station'] or '-'} | {r['score']} | {'、'.join(r['reasons']) or '-'} | "
            f"{'<br>'.join(r['issues']) or '-'} |"
        )
    if not report["shortlist"]:
        lines.append("| - | 該当なし | | | | | | | |")
    lines.append("")

    if report["gaps"]:
        lines += ["## 足りない範囲", *[f"- {g}" for g in report["gaps"]], ""]
    lines += ["## 内見・問い合わせで確認すること", *[f"- {c}" for c in report["checklist"]], ""]
    lines += ["## 非公開物件について", report["non_public_listings"], ""]
    lines += ["## 次にやること", *[f"- {a}" for a in report["next_actions"]]]
    return "\n".join(lines)

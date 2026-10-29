import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.connectors.parser_utils import TSUBO_PER_SQM

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "property_finder.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS properties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_hash TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                headline TEXT,
                category TEXT NOT NULL,
                transaction_type TEXT NOT NULL,
                price INTEGER NOT NULL,
                monthly_rent INTEGER,
                price_per_sqm INTEGER,
                price_per_tsubo INTEGER,
                prefecture_code TEXT NOT NULL,
                prefecture_name TEXT NOT NULL,
                municipality_name TEXT,
                address TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                elevation_m REAL,
                nearest_station TEXT,
                station_walk_min INTEGER,
                station_raw TEXT,
                land_area_sqm REAL,
                building_area_sqm REAL,
                floor_plan TEXT,
                structure TEXT,
                build_year INTEGER,
                building_coverage_ratio REAL,
                floor_area_ratio REAL,
                max_buildable_area_sqm REAL,
                market_deviation_pct REAL,
                estimated_gross_yield_pct REAL,
                primary_source_site TEXT NOT NULL,
                primary_source_url TEXT NOT NULL,
                source_sites_json TEXT NOT NULL DEFAULT '[]',
                source_urls_json TEXT NOT NULL DEFAULT '{}',
                image_urls_json TEXT NOT NULL DEFAULT '[]',
                features_json TEXT NOT NULL DEFAULT '[]',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_properties_pref ON properties(prefecture_code);
            CREATE INDEX IF NOT EXISTS idx_properties_muni ON properties(municipality_name);
            CREATE INDEX IF NOT EXISTS idx_properties_cat ON properties(category);
            CREATE INDEX IF NOT EXISTS idx_properties_price ON properties(price);
            CREATE INDEX IF NOT EXISTS idx_properties_coords ON properties(latitude, longitude);

            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id INTEGER NOT NULL,
                price INTEGER NOT NULL,
                source_site TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                FOREIGN KEY(property_id) REFERENCES properties(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS agent_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id INTEGER NOT NULL,
                agent_name TEXT NOT NULL,
                score INTEGER NOT NULL,
                recommendation TEXT NOT NULL,
                pros TEXT NOT NULL DEFAULT '',
                cons TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(property_id) REFERENCES properties(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_evals_prop ON agent_evaluations(property_id);

            CREATE TABLE IF NOT EXISTS watchlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS watchlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watchlist_id INTEGER NOT NULL,
                property_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT '調査中',
                memo TEXT NOT NULL DEFAULT '',
                added_by TEXT NOT NULL DEFAULT '人間',
                added_at TEXT NOT NULL,
                UNIQUE(watchlist_id, property_id),
                FOREIGN KEY(watchlist_id) REFERENCES watchlists(id) ON DELETE CASCADE,
                FOREIGN KEY(property_id) REFERENCES properties(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS ui_sync_state (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        # 標準ウォッチリストの初期化
        ts = now_iso()
        conn.execute(
            """
            INSERT OR IGNORE INTO watchlists (name, description, created_at)
            VALUES ('重点調査案件リスト', '人間およびAIエージェント（クロード・コーデックス・アンチグラビティ）で共有する候補物件リスト', ?)
            """,
            (ts,),
        )
        conn.commit()


def _calculate_derived_metrics(item: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """最大建築可能延床面積（平米）と想定表面利回り（%）を算出する。"""
    land_area = item.get("land_area_sqm")
    far = item.get("floor_area_ratio")
    max_buildable = None
    if land_area and far and land_area > 0 and far > 0:
        max_buildable = round(land_area * (far / 100.0), 2)
    elif land_area and land_area > 0:
        # 容積率未記載時は一般的な住居系容積率160%を参考基準値として試算可能にするがDB列は実数値がある時のみ記録
        max_buildable = None

    # 想定表面利回り算出
    gross_yield = None
    if item.get("transaction_type") == "売買" and item.get("price") and item["price"] > 0:
        price = item["price"]
        psm = item.get("price_per_sqm") or 500_000
        ref_area = item.get("building_area_sqm") or item.get("land_area_sqm") or 60.0
        # 地域の平米単価水準に連動した実勢想定賃料月額（平米あたり賃料）から想定年間賃料を試算
        est_rent_per_sqm = max(1200.0, min(5200.0, psm * 0.0042))
        if item.get("category") == "売地" and land_area:
            eff_area = max_buildable if max_buildable else (land_area * 1.5)
            annual_rent = eff_area * 0.82 * est_rent_per_sqm * 12.0
            est_total_cost = price + (eff_area * 280_000.0)
            gross_yield = round((annual_rent / est_total_cost) * 100.0, 2)
        else:
            annual_rent = ref_area * est_rent_per_sqm * 12.0
            gross_yield = round((annual_rent / price) * 100.0, 2)

    return max_buildable, gross_yield


def upsert_properties(items: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    取得した物件リストをDBへ保存・名寄せ統合する。
    同一 canonical_hash が既に存在する場合は掲載ポータル一覧・URL・欠損属性をマージし、
    価格変動があれば price_history に記録する。
    """
    init_db()
    inserted = 0
    updated = 0
    merged_multi_portal = 0
    ts = now_iso()

    with get_connection() as conn:
        for item in items:
            c_hash = item["canonical_hash"]
            existing = conn.execute(
                "SELECT * FROM properties WHERE canonical_hash = ?", (c_hash,)
            ).fetchone()

            max_buildable, est_yield = _calculate_derived_metrics(item)
            source_site = item.get("source_site", "スーモ")
            source_url = item.get("source_url", "")

            if existing is None:
                source_sites = [source_site]
                source_urls = {source_site: source_url}
                cur = conn.execute(
                    """
                    INSERT INTO properties (
                        canonical_hash, title, headline, category, transaction_type,
                        price, monthly_rent, price_per_sqm, price_per_tsubo,
                        prefecture_code, prefecture_name, municipality_name, address,
                        latitude, longitude, elevation_m,
                        nearest_station, station_walk_min, station_raw,
                        land_area_sqm, building_area_sqm, floor_plan, structure, build_year,
                        building_coverage_ratio, floor_area_ratio, max_buildable_area_sqm,
                        estimated_gross_yield_pct,
                        primary_source_site, primary_source_url,
                        source_sites_json, source_urls_json, image_urls_json, features_json,
                        first_seen_at, last_seen_at
                    ) VALUES (
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?,
                        ?,
                        ?, ?,
                        ?, ?, ?, ?,
                        ?, ?
                    )
                    """,
                    (
                        c_hash,
                        item["title"],
                        item.get("headline", ""),
                        item["category"],
                        item["transaction_type"],
                        item["price"],
                        item.get("monthly_rent"),
                        item.get("price_per_sqm"),
                        item.get("price_per_tsubo"),
                        item["prefecture_code"],
                        item["prefecture_name"],
                        item.get("municipality_name", ""),
                        item["address"],
                        item.get("latitude"),
                        item.get("longitude"),
                        item.get("elevation_m"),
                        item.get("nearest_station"),
                        item.get("station_walk_min"),
                        item.get("station_raw", ""),
                        item.get("land_area_sqm"),
                        item.get("building_area_sqm"),
                        item.get("floor_plan"),
                        item.get("structure"),
                        item.get("build_year"),
                        item.get("building_coverage_ratio"),
                        item.get("floor_area_ratio"),
                        max_buildable,
                        est_yield,
                        source_site,
                        source_url,
                        json.dumps(source_sites, ensure_ascii=False),
                        json.dumps(source_urls, ensure_ascii=False),
                        json.dumps(item.get("image_urls", []), ensure_ascii=False),
                        json.dumps(item.get("features", []), ensure_ascii=False),
                        ts,
                        ts,
                    ),
                )
                prop_id = cur.lastrowid
                conn.execute(
                    """
                    INSERT INTO price_history (property_id, price, source_site, recorded_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (prop_id, item["price"], source_site, ts),
                )
                inserted += 1
            else:
                prop_id = existing["id"]
                sites: List[str] = json.loads(existing["source_sites_json"] or "[]")
                urls: Dict[str, str] = json.loads(existing["source_urls_json"] or "{}")
                if source_site not in sites:
                    sites.append(source_site)
                    merged_multi_portal += 1
                if source_url:
                    urls[source_site] = source_url

                imgs: List[str] = json.loads(existing["image_urls_json"] or "[]")
                for img in item.get("image_urls", []):
                    if img not in imgs:
                        imgs.append(img)

                feats: List[str] = json.loads(existing["features_json"] or "[]")
                for f in item.get("features", []):
                    if f not in feats:
                        feats.append(f)

                # 価格変動時は履歴を追加
                if existing["price"] != item["price"]:
                    conn.execute(
                        """
                        INSERT INTO price_history (property_id, price, source_site, recorded_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (prop_id, item["price"], source_site, ts),
                    )

                better_address = (
                    item["address"]
                    if len(item.get("address", "")) > len(existing["address"] or "")
                    else existing["address"]
                )
                conn.execute(
                    """
                    UPDATE properties SET
                        price = ?,
                        address = ?,
                        latitude = COALESCE(?, latitude),
                        longitude = COALESCE(?, longitude),
                        elevation_m = COALESCE(?, elevation_m),
                        building_coverage_ratio = COALESCE(?, building_coverage_ratio),
                        floor_area_ratio = COALESCE(?, floor_area_ratio),
                        max_buildable_area_sqm = COALESCE(?, max_buildable_area_sqm),
                        floor_plan = COALESCE(?, floor_plan),
                        build_year = COALESCE(?, build_year),
                        source_sites_json = ?,
                        source_urls_json = ?,
                        image_urls_json = ?,
                        features_json = ?,
                        last_seen_at = ?
                    WHERE id = ?
                    """,
                    (
                        item["price"],
                        better_address,
                        item.get("latitude"),
                        item.get("longitude"),
                        item.get("elevation_m"),
                        item.get("building_coverage_ratio"),
                        item.get("floor_area_ratio"),
                        max_buildable,
                        item.get("floor_plan"),
                        item.get("build_year"),
                        json.dumps(sites, ensure_ascii=False),
                        json.dumps(urls, ensure_ascii=False),
                        json.dumps(imgs[:8], ensure_ascii=False),
                        json.dumps(feats[:15], ensure_ascii=False),
                        ts,
                        prop_id,
                    ),
                )
                updated += 1

        # 市区町村×種別ごとの平均平米単価を算出し、各物件の周辺相場乖離率（market_deviation_pct）を更新
        conn.execute(
            """
            WITH area_avg AS (
                SELECT
                    prefecture_code,
                    municipality_name,
                    category,
                    AVG(price_per_sqm) AS avg_psm
                FROM properties
                WHERE price_per_sqm IS NOT NULL AND price_per_sqm > 0
                GROUP BY prefecture_code, municipality_name, category
            )
            UPDATE properties
            SET market_deviation_pct = ROUND(
                (
                    (
                        price_per_sqm - (
                            SELECT avg_psm FROM area_avg
                            WHERE area_avg.prefecture_code = properties.prefecture_code
                              AND area_avg.municipality_name = properties.municipality_name
                              AND area_avg.category = properties.category
                        )
                    ) * 100.0
                ) / (
                    SELECT avg_psm FROM area_avg
                    WHERE area_avg.prefecture_code = properties.prefecture_code
                      AND area_avg.municipality_name = properties.municipality_name
                      AND area_avg.category = properties.category
                ),
                1
            )
            WHERE price_per_sqm IS NOT NULL AND price_per_sqm > 0;
            """
        )
        conn.commit()

    return {
        "inserted": inserted,
        "updated": updated,
        "merged_multi_portal": merged_multi_portal,
    }


def point_in_polygon(lat: float, lon: float, polygon: Sequence[Sequence[float]]) -> bool:
    """Ray-casting アルゴリズムにより座標 (lat, lon) がポリゴン [[lat, lon], ...] 内に含まれるか判定する。"""
    n = len(polygon)
    if n < 3:
        return True
    inside = False
    p1_lat, p1_lon = polygon[0][0], polygon[0][1]
    for i in range(1, n + 1):
        p2_lat, p2_lon = polygon[i % n][0], polygon[i % n][1]
        if lon > min(p1_lon, p2_lon):
            if lon <= max(p1_lon, p2_lon):
                if lat <= max(p1_lat, p2_lat):
                    if p1_lon != p2_lon:
                        xinters = (lon - p1_lon) * (p2_lat - p1_lat) / (p2_lon - p1_lon) + p1_lat
                    if p1_lat == p2_lat or lat <= xinters:
                        inside = not inside
        p1_lat, p1_lon = p2_lat, p2_lon
    return inside


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["source_sites"] = json.loads(d.pop("source_sites_json", "[]") or "[]")
    d["source_urls"] = json.loads(d.pop("source_urls_json", "{}") or "{}")
    d["image_urls"] = json.loads(d.pop("image_urls_json", "[]") or "[]")
    d["features"] = json.loads(d.pop("features_json", "[]") or "[]")
    if d.get("land_area_sqm"):
        d["land_area_tsubo"] = round(d["land_area_sqm"] * TSUBO_PER_SQM, 2)
    else:
        d["land_area_tsubo"] = None
    if d.get("building_area_sqm"):
        d["building_area_tsubo"] = round(d["building_area_sqm"] * TSUBO_PER_SQM, 2)
    else:
        d["building_area_tsubo"] = None
    return d


def query_properties(
    prefecture_code: Optional[str] = None,
    municipality_name: Optional[str] = None,
    category: Optional[str] = None,
    transaction_type: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    min_land_area_sqm: Optional[float] = None,
    max_land_area_sqm: Optional[float] = None,
    min_building_area_sqm: Optional[float] = None,
    max_walk_min: Optional[int] = None,
    min_coverage_ratio: Optional[float] = None,
    min_floor_area_ratio: Optional[float] = None,
    keyword: Optional[str] = None,
    bbox: Optional[Tuple[float, float, float, float]] = None,
    polygon: Optional[List[List[float]]] = None,
    only_undervalued: bool = False,
    sort_by: str = "price_asc",
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """複合条件・空間範囲（矩形BBox / ポリゴン）・割安度による高速物件クエリを実行する。"""
    init_db()
    clauses: List[str] = ["1=1"]
    params: List[Any] = []

    if prefecture_code:
        clauses.append("p.prefecture_code = ?")
        params.append(prefecture_code.zfill(2))

    if municipality_name:
        clauses.append("(p.municipality_name LIKE ? OR p.address LIKE ?)")
        params.extend([f"%{municipality_name}%", f"%{municipality_name}%"])

    if category and category != "すべて":
        clauses.append("p.category = ?")
        params.append(category)

    if transaction_type and transaction_type != "すべて":
        clauses.append("p.transaction_type = ?")
        params.append(transaction_type)

    if min_price is not None:
        clauses.append("p.price >= ?")
        params.append(min_price)

    if max_price is not None:
        clauses.append("p.price <= ?")
        params.append(max_price)

    if min_land_area_sqm is not None:
        clauses.append("p.land_area_sqm >= ?")
        params.append(min_land_area_sqm)

    if max_land_area_sqm is not None:
        clauses.append("p.land_area_sqm <= ?")
        params.append(max_land_area_sqm)

    if min_building_area_sqm is not None:
        clauses.append("p.building_area_sqm >= ?")
        params.append(min_building_area_sqm)

    if max_walk_min is not None:
        clauses.append("p.station_walk_min IS NOT NULL AND p.station_walk_min <= ?")
        params.append(max_walk_min)

    if min_coverage_ratio is not None:
        clauses.append("p.building_coverage_ratio >= ?")
        params.append(min_coverage_ratio)

    if min_floor_area_ratio is not None:
        clauses.append("p.floor_area_ratio >= ?")
        params.append(min_floor_area_ratio)

    if keyword:
        clauses.append(
            "(p.title LIKE ? OR p.headline LIKE ? OR p.address LIKE ? OR p.nearest_station LIKE ? OR p.features_json LIKE ?)"
        )
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw, kw, kw])

    if bbox:
        min_lat, min_lon, max_lat, max_lon = bbox
        clauses.append(
            "p.latitude IS NOT NULL AND p.longitude IS NOT NULL "
            "AND p.latitude BETWEEN ? AND ? AND p.longitude BETWEEN ? AND ?"
        )
        params.extend([min_lat, max_lat, min_lon, max_lon])

    if only_undervalued:
        clauses.append("p.market_deviation_pct IS NOT NULL AND p.market_deviation_pct < -5.0")

    order_map = {
        "price_asc": "p.price ASC",
        "price_desc": "p.price DESC",
        "tsubo_asc": "COALESCE(p.price_per_tsubo, 999999999) ASC",
        "area_desc": "COALESCE(p.land_area_sqm, p.building_area_sqm, 0) DESC",
        "deviation_asc": "COALESCE(p.market_deviation_pct, 999) ASC",
        "yield_desc": "COALESCE(p.estimated_gross_yield_pct, 0) DESC",
        "walk_asc": "COALESCE(p.station_walk_min, 999) ASC",
        "newest": "p.last_seen_at DESC, p.id DESC",
    }
    order_sql = order_map.get(sort_by, "p.price ASC")

    where_sql = " AND ".join(clauses)
    sql = f"""
        SELECT
            p.*,
            (SELECT COUNT(*) FROM agent_evaluations e WHERE e.property_id = p.id) AS evaluation_count,
            (SELECT ROUND(AVG(e.score), 1) FROM agent_evaluations e WHERE e.property_id = p.id) AS avg_agent_score
        FROM properties p
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT ?
    """
    params.append(limit * 2 if polygon else limit)

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        results = [_row_to_dict(r) for r in rows]

    if polygon and len(polygon) >= 3:
        results = [
            r for r in results
            if r.get("latitude") is not None
            and r.get("longitude") is not None
            and point_in_polygon(r["latitude"], r["longitude"], polygon)
        ][:limit]

    return results


def get_property_by_id(property_id: int) -> Optional[Dict[str, Any]]:
    init_db()
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        if not row:
            return None
        prop = _row_to_dict(row)
        history = conn.execute(
            "SELECT * FROM price_history WHERE property_id = ? ORDER BY recorded_at ASC",
            (property_id,),
        ).fetchall()
        evals = conn.execute(
            "SELECT * FROM agent_evaluations WHERE property_id = ? ORDER BY created_at DESC",
            (property_id,),
        ).fetchall()
        prop["price_history"] = [dict(h) for h in history]
        prop["agent_evaluations"] = [dict(e) for e in evals]
        return prop


def add_agent_evaluation(
    property_id: int,
    agent_name: str,
    score: int,
    recommendation: str,
    pros: str = "",
    cons: str = "",
    notes: str = "",
) -> Dict[str, Any]:
    """Claude / Codex / Antigravity または人間による物件評価スコア・コメントを記録する。"""
    init_db()
    ts = now_iso()
    norm_agent = agent_name.strip().lower()
    agent_display_map = {
        "claude": "クロード",
        "codex": "コーデックス",
        "antigravity": "アンチグラビティ",
        "human": "人間",
    }
    stored_agent = agent_display_map.get(norm_agent, agent_name.strip())
    clamped_score = max(0, min(100, int(score)))

    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO agent_evaluations (
                property_id, agent_name, score, recommendation, pros, cons, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (property_id, stored_agent, clamped_score, recommendation, pros, cons, notes, ts),
        )
        conn.commit()
        eval_id = cur.lastrowid

    record_agent_activity(
        agent_name=stored_agent,
        action="物件評価登録",
        summary=f"物件番号 {property_id} に評価点 {clamped_score}点（{recommendation}）を記録しました。",
        highlighted_property_ids=[property_id],
    )
    return {
        "id": eval_id,
        "property_id": property_id,
        "agent_name": stored_agent,
        "score": clamped_score,
        "recommendation": recommendation,
        "pros": pros,
        "cons": cons,
        "notes": notes,
        "created_at": ts,
    }


def get_watchlists() -> List[Dict[str, Any]]:
    init_db()
    with get_connection() as conn:
        w_rows = conn.execute("SELECT * FROM watchlists ORDER BY id ASC").fetchall()
        result: List[Dict[str, Any]] = []
        for w in w_rows:
            wd = dict(w)
            items = conn.execute(
                """
                SELECT
                    wi.id AS item_id,
                    wi.status,
                    wi.memo,
                    wi.added_by,
                    wi.added_at,
                    p.*
                FROM watchlist_items wi
                JOIN properties p ON p.id = wi.property_id
                WHERE wi.watchlist_id = ?
                ORDER BY wi.added_at DESC
                """,
                (w["id"],),
            ).fetchall()
            parsed_items = []
            for it in items:
                d = _row_to_dict(it)
                parsed_items.append(d)
            wd["items"] = parsed_items
            result.append(wd)
        return result


def add_to_watchlist(
    property_id: int,
    watchlist_name: str = "重点調査案件リスト",
    status: str = "調査中",
    memo: str = "",
    added_by: str = "アンチグラビティ",
) -> Dict[str, Any]:
    init_db()
    ts = now_iso()
    agent_display_map = {
        "claude": "クロード",
        "codex": "コーデックス",
        "antigravity": "アンチグラビティ",
        "human": "人間",
    }
    norm_by = agent_display_map.get(added_by.strip().lower(), added_by.strip())
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO watchlists (name, description, created_at)
            VALUES (?, '案件管理リスト', ?)
            """,
            (watchlist_name, ts),
        )
        w = conn.execute("SELECT id FROM watchlists WHERE name = ?", (watchlist_name,)).fetchone()
        wid = w["id"]
        conn.execute(
            """
            INSERT INTO watchlist_items (watchlist_id, property_id, status, memo, added_by, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(watchlist_id, property_id) DO UPDATE SET
                status = excluded.status,
                memo = excluded.memo,
                added_by = excluded.added_by,
                added_at = excluded.added_at
            """,
            (wid, property_id, status, memo, norm_by, ts),
        )
        conn.commit()

    record_agent_activity(
        agent_name=norm_by,
        action="案件リスト追加",
        summary=f"物件番号 {property_id} を「{watchlist_name}」（状態：{status}）へ登録しました。",
        highlighted_property_ids=[property_id],
    )
    return {
        "watchlist_id": wid,
        "watchlist_name": watchlist_name,
        "property_id": property_id,
        "status": status,
        "memo": memo,
        "added_by": norm_by,
        "added_at": ts,
    }


def remove_from_watchlist(watchlist_id: int, property_id: int) -> bool:
    init_db()
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM watchlist_items WHERE watchlist_id = ? AND property_id = ?",
            (watchlist_id, property_id),
        )
        conn.commit()
        return cur.rowcount > 0


def get_ui_sync_state() -> Dict[str, Any]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute("SELECT key, value_json, updated_at FROM ui_sync_state").fetchall()
        state: Dict[str, Any] = {
            "map_view": {
                "center_lat": 35.6586,
                "center_lon": 139.7454,
                "zoom": 13,
                "polygon": [],
            },
            "selected_property_ids": [],
            "highlighted_property_ids": [],
            "active_filters": {},
            "agent_activity_log": [],
        }
        for r in rows:
            try:
                state[r["key"]] = json.loads(r["value_json"])
            except Exception:
                pass
        return state


def update_ui_sync_state(key: str, value: Any) -> Dict[str, Any]:
    init_db()
    ts = now_iso()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO ui_sync_state (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (key, json.dumps(value, ensure_ascii=False), ts),
        )
        conn.commit()
    return get_ui_sync_state()


def record_agent_activity(
    agent_name: str,
    action: str,
    summary: str,
    highlighted_property_ids: Optional[List[int]] = None,
    map_center: Optional[Tuple[float, float, int]] = None,
) -> Dict[str, Any]:
    """エージェントの操作履歴を保存し、Web UIの画面フォーカス・ハイライト状態を更新する。"""
    state = get_ui_sync_state()
    logs: List[Dict[str, Any]] = state.get("agent_activity_log", [])
    agent_display_map = {
        "claude": "クロード",
        "codex": "コーデックス",
        "antigravity": "アンチグラビティ",
        "human": "人間",
    }
    disp_agent = agent_display_map.get(agent_name.strip().lower(), agent_name.strip())
    entry = {
        "agent_name": disp_agent,
        "action": action,
        "summary": summary,
        "highlighted_property_ids": highlighted_property_ids or [],
        "timestamp": now_iso(),
    }
    logs.insert(0, entry)
    update_ui_sync_state("agent_activity_log", logs[:30])
    if highlighted_property_ids is not None:
        update_ui_sync_state("highlighted_property_ids", highlighted_property_ids)
    if map_center is not None:
        lat, lon, zoom = map_center
        mv = state.get("map_view", {})
        mv.update({"center_lat": lat, "center_lon": lon, "zoom": zoom})
        update_ui_sync_state("map_view", mv)
    return get_ui_sync_state()


def get_database_stats() -> Dict[str, Any]:
    init_db()
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
        by_cat = conn.execute(
            """
            SELECT
                category,
                COUNT(*) AS count,
                ROUND(AVG(price)) AS avg_price,
                ROUND(AVG(price_per_tsubo)) AS avg_tsubo_price
            FROM properties
            GROUP BY category
            ORDER BY count DESC
            """
        ).fetchall()
        by_pref = conn.execute(
            """
            SELECT
                prefecture_code,
                prefecture_name,
                COUNT(*) AS count
            FROM properties
            GROUP BY prefecture_code, prefecture_name
            ORDER BY count DESC
            """
        ).fetchall()
        by_source = conn.execute(
            """
            SELECT
                primary_source_site AS source_site,
                COUNT(*) AS count
            FROM properties
            GROUP BY primary_source_site
            ORDER BY count DESC
            """
        ).fetchall()
        multi_portal_count = conn.execute(
            "SELECT COUNT(*) FROM properties WHERE source_sites_json LIKE '%,%'"
        ).fetchone()[0]
        eval_count = conn.execute("SELECT COUNT(*) FROM agent_evaluations").fetchone()[0]

    return {
        "total_properties": total,
        "multi_portal_merged_count": multi_portal_count,
        "total_agent_evaluations": eval_count,
        "by_category": [dict(r) for r in by_cat],
        "by_prefecture": [dict(r) for r in by_pref],
        "by_source": [dict(r) for r in by_source],
    }

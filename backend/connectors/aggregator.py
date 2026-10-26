import asyncio
from typing import Any, Dict, List, Optional
import httpx

from backend.municipalities import PREFECTURES, resolve_location
from backend.connectors.gsi_gov import (
    apply_jitter_for_display,
    fetch_elevation,
    geocode_address,
)
from backend.connectors.suumo import fetch_suumo_rent_listings, fetch_suumo_sale_listings
from backend.connectors.yahoo import fetch_yahoo_listings
from backend.connectors.nifty import fetch_nifty_listings
from backend.database import query_properties, record_agent_activity, upsert_properties


async def _enrich_coordinates_and_elevation(
    items: List[Dict[str, Any]],
    default_pref_code: str = "13",
) -> List[Dict[str, Any]]:
    """
    取得した物件一覧に対し、国土地理院 AddressSearch API で並列ジオコーディングを実行し、
    代表座標の標高も取得して付与する。
    """
    if not items:
        return []

    sem = asyncio.Semaphore(10)
    pref_info = PREFECTURES.get(default_pref_code.zfill(2), PREFECTURES["13"])

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 同一住所の重複APIコールを避けるためユニーク住所リストを先に解決
        unique_addrs = list({it["address"] for it in items if it.get("address")})
        addr_map: Dict[str, Dict[str, float]] = {}

        async def _resolve_one(addr: str) -> None:
            async with sem:
                geo = await geocode_address(addr, client=client)
                if geo and geo.get("latitude") and geo.get("longitude"):
                    addr_map[addr] = {
                        "latitude": geo["latitude"],
                        "longitude": geo["longitude"],
                    }

        await asyncio.gather(*[_resolve_one(a) for a in unique_addrs])

        # 代表1点の標高を取得（高速化のためエリア代表座標および最初の数件に実測標高を付与）
        sample_coords = list(addr_map.values())[:6]
        elev_cache: Dict[str, Optional[float]] = {}
        for sc in sample_coords:
            key = f"{sc['latitude']:.3f},{sc['longitude']:.3f}"
            if key not in elev_cache:
                ev = await fetch_elevation(sc["latitude"], sc["longitude"], client=client)
                elev_cache[key] = ev.get("elevation_m")

        default_elev = next((v for v in elev_cache.values() if v is not None), None)

        for it in items:
            addr = it.get("address", "")
            base_coord = addr_map.get(addr)
            if base_coord:
                lat, lon = apply_jitter_for_display(
                    base_coord["latitude"],
                    base_coord["longitude"],
                    f"{it['canonical_hash']}:{it.get('title', '')}",
                )
                it["latitude"] = lat
                it["longitude"] = lon
                ckey = f"{base_coord['latitude']:.3f},{base_coord['longitude']:.3f}"
                it["elevation_m"] = elev_cache.get(ckey, default_elev)
            else:
                lat, lon = apply_jitter_for_display(
                    pref_info["lat"],
                    pref_info["lon"],
                    f"{it['canonical_hash']}:{it.get('title', '')}",
                )
                it["latitude"] = lat
                it["longitude"] = lon
                it["elevation_m"] = default_elev

    return items


async def live_search_and_sync(
    prefecture: Optional[str] = "13",
    municipality: Optional[str] = None,
    category: Optional[str] = "売地",
    keyword: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    指定した都道府県・市区町村・種別・キーワードについて、
    1. スーモ（売買・賃貸）
    2. ヤフー不動産
    3. ニフティ不動産（複数ポータル統合）
    へ並列でリアルタイム検索を実行し、
    4. 国土地理院APIによるジオコーディング・標高付与
    5. ローカルSQLite DBへの自動永続化・名寄せ（重複排除）・周辺相場乖離率の再計算
    を一気通貫で行う。
    """
    loc = resolve_location(prefecture, municipality)
    pref_info = loc["prefecture"] or PREFECTURES["13"]
    muni_info = loc["municipality"]
    pref_code = pref_info["code"]
    muni_code = muni_info["muni_code"] if muni_info else None
    muni_name = muni_info["muni_name"] if muni_info else (municipality or None)

    target_categories = (
        ["売地", "戸建て", "マンション"]
        if (not category or category == "すべて")
        else [category]
    )

    tasks = []
    async with httpx.AsyncClient(
        timeout=15.0,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ja,en-US;q=0.9",
        },
        follow_redirects=True,
    ) as client:
        for cat in target_categories:
            if cat == "賃貸":
                tasks.append(
                    fetch_suumo_rent_listings(
                        pref_code=pref_code,
                        muni_code=muni_code,
                        keyword=keyword,
                        client=client,
                    )
                )
            else:
                tasks.append(
                    fetch_suumo_sale_listings(
                        pref_code=pref_code,
                        muni_code=muni_code,
                        category=cat,
                        keyword=keyword,
                        client=client,
                    )
                )
                tasks.append(
                    fetch_yahoo_listings(
                        pref_code=pref_code,
                        muni_code=muni_code,
                        category=cat,
                        keyword=keyword,
                        client=client,
                    )
                )
                tasks.append(
                    fetch_nifty_listings(
                        pref_code=pref_code,
                        muni_code=muni_code,
                        category=cat,
                        keyword=keyword,
                        client=client,
                    )
                )

        batch_lists = await asyncio.gather(*tasks, return_exceptions=True)

    raw_items: List[Dict[str, Any]] = []
    source_counts: Dict[str, int] = {}
    for res in batch_lists:
        if isinstance(res, list):
            for it in res:
                raw_items.append(it)
                s_name = it.get("source_site", "不明")
                source_counts[s_name] = source_counts.get(s_name, 0) + 1

    enriched = await _enrich_coordinates_and_elevation(raw_items, default_pref_code=pref_code)
    upsert_summary = upsert_properties(enriched)

    queried = query_properties(
        prefecture_code=pref_code,
        municipality_name=muni_name,
        category=category if category != "すべて" else None,
        keyword=keyword,
        limit=120,
    )

    if agent_name:
        first_coord = None
        for q in queried:
            if q.get("latitude") and q.get("longitude"):
                first_coord = (q["latitude"], q["longitude"], 14 if muni_name else 11)
                break
        record_agent_activity(
            agent_name=agent_name,
            action="リアルタイム横断検索",
            summary=(
                f"{pref_info['name']}{muni_name or ''}（種別：{category or 'すべて'}）を横断検索し、"
                f"実データ {len(raw_items)} 件を取得・名寄せ統合しました。"
            ),
            highlighted_property_ids=[q["id"] for q in queried[:10]],
            map_center=first_coord,
        )

    return {
        "location": {
            "prefecture_code": pref_code,
            "prefecture_name": pref_info["name"],
            "municipality_code": muni_code,
            "municipality_name": muni_name,
        },
        "fetched_raw_count": len(raw_items),
        "source_breakdown": source_counts,
        "upsert_summary": upsert_summary,
        "properties": queried,
    }

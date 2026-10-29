import re
from typing import Any, Dict, List, Optional
import httpx
from bs4 import BeautifulSoup

from backend.municipalities import PREFECTURES, load_municipalities
from backend.connectors.parser_utils import (
    TSUBO_PER_SQM,
    compute_canonical_hash,
    normalize_address,
    parse_area_sqm,
    parse_build_year,
    parse_japanese_price,
    parse_station_info,
    to_halfwidth,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
}

YAHOO_PATH_MAP = {
    "売地": [("land", "売地")],
    "戸建て": [("used/house", "戸建て"), ("new/house", "戸建て")],
    "マンション": [("used/mansion", "マンション")],
    "空き家・古民家": [("used/house", "空き家・古民家")],
    "収益物件": [("used/house", "収益物件"), ("used/mansion", "収益物件")],
}


def _resolve_pref_muni(address: str, default_pref_code: str) -> Dict[str, str]:
    pref_info = PREFECTURES.get(default_pref_code.zfill(2), PREFECTURES["13"])
    pref_name = pref_info["name"]
    for p in PREFECTURES.values():
        if address.startswith(p["name"]):
            pref_info = p
            pref_name = p["name"]
            break

    muni_name = ""
    munis = load_municipalities()
    for m in munis.values():
        if m["pref_code"] == pref_info["code"] and m["muni_name"] in address:
            if len(m["muni_name"]) > len(muni_name):
                muni_name = m["muni_name"]

    if not muni_name:
        rest = address.replace(pref_name, "", 1)
        m = re.match(r"^([^0-9０-９\-－]+?[市区町村郡])", rest)
        if m:
            muni_name = m.group(1)

    return {
        "prefecture_code": pref_info["code"],
        "prefecture_name": pref_name,
        "municipality_name": muni_name,
    }


async def fetch_yahoo_listings(
    pref_code: str = "13",
    muni_code: Optional[str] = None,
    category: str = "売地",
    keyword: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """
    Yahoo!不動産（realestate.yahoo.co.jp）の売地・中古戸建・新築戸建・中古マンションから
    リアルタイムで実物件データを取得・構造化する。
    """
    if category == "賃貸":
        return []

    pc = pref_code.zfill(2)
    pref_info = PREFECTURES.get(pc, PREFECTURES["13"])
    reg = pref_info["yahoo_reg"]
    targets = YAHOO_PATH_MAP.get(category, [("land", "売地")])

    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0, headers=HEADERS, follow_redirects=True)
        own_client = True

    results: List[Dict[str, Any]] = []
    try:
        for path_seg, mapped_cat in targets:
            if muni_code and not muni_code.zfill(5).endswith("00"):
                mc = muni_code.zfill(5)
                url = f"https://realestate.yahoo.co.jp/{path_seg}/search/{reg}/{pc}/{mc}/"
            else:
                url = f"https://realestate.yahoo.co.jp/{path_seg}/search/{reg}/{pc}/"

            resp = await client.get(url)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.select(".ListBukken2__list__item")
            for item in items:
                full_text = to_halfwidth(item.get_text(" ", strip=True))
                if keyword and to_halfwidth(keyword) not in full_text:
                    continue

                # タイトル
                ttl_el = item.select_one(".ListCassette2__ttl__txt") or item.find(["h2", "h3"])
                title = to_halfwidth(ttl_el.get_text(" ", strip=True)) if ttl_el else ""

                # 価格
                price_el = item.select_one(".ListCassette2__info__price")
                price_str = to_halfwidth(price_el.get_text(strip=True)) if price_el else ""
                if not price_str:
                    pm = re.search(r"((?:\d+億)?(?:\d[\d,]*万円))", full_text)
                    price_str = pm.group(1) if pm else ""
                price_yen = parse_japanese_price(price_str)
                if not price_yen:
                    continue

                # 住所抽出（都道府県名から始まる箇所）
                addr_match = re.search(
                    rf"({pref_info['name']}[^\s/]+)",
                    full_text,
                )
                address_raw = addr_match.group(1) if addr_match else f"{pref_info['name']}{title}"
                norm_addr = normalize_address(address_raw)
                loc = _resolve_pref_muni(norm_addr, pc)

                # 駅・徒歩
                station_name, walk_min = parse_station_info(full_text)

                # 土地面積・建物面積
                land_match = re.search(r"土地(?:面積)?\s*(\d+(?:\.\d+)?)\s*m\s*2", full_text)
                bld_match = re.search(r"(?:建物|専有面積|/\s*東\s*/|/\s*南\s*/|/\s*西\s*/|/\s*北\s*/)\s*(\d+(?:\.\d+)?)\s*m\s*2", full_text)
                land_area = float(land_match.group(1)) if land_match else None
                building_area = float(bld_match.group(1)) if bld_match else None
                if mapped_cat == "マンション" and not building_area:
                    any_m2 = re.search(r"(\d+(?:\.\d+)?)\s*m\s*2", full_text)
                    if any_m2:
                        building_area = float(any_m2.group(1))

                # 間取り・築年
                madori_match = re.search(r"([1-9]\d?[SLDKR]+|ワンルーム)", full_text)
                floor_plan = madori_match.group(1) if madori_match else None
                build_year = parse_build_year(full_text)

                # リンク・画像
                link_el = item.select_one("a[href*='realestate.yahoo.co.jp']") or item.find("a", href=True)
                href = link_el.get("href", "") if link_el else ""
                source_url = href if href.startswith("http") else f"https://realestate.yahoo.co.jp{href}"

                images: List[str] = []
                for img in item.select("img"):
                    src = img.get("src") or ""
                    if src.startswith("http") and "noimage" not in src and "shop_image" not in src:
                        images.append(src)

                ref_area = land_area if mapped_cat == "売地" else (building_area or land_area)
                price_per_sqm = round(price_yen / ref_area) if (ref_area and ref_area > 0) else None
                price_per_tsubo = (
                    round(price_yen / (ref_area * TSUBO_PER_SQM))
                    if (ref_area and ref_area > 0)
                    else None
                )

                c_hash = compute_canonical_hash(
                    mapped_cat, norm_addr, price_yen, land_area, building_area
                )

                results.append({
                    "canonical_hash": c_hash,
                    "title": title or norm_addr,
                    "headline": f"ヤフー不動産掲載・{title or norm_addr}",
                    "category": mapped_cat,
                    "transaction_type": "売買",
                    "price": price_yen,
                    "monthly_rent": None,
                    "price_per_sqm": price_per_sqm,
                    "price_per_tsubo": price_per_tsubo,
                    "prefecture_code": loc["prefecture_code"],
                    "prefecture_name": loc["prefecture_name"],
                    "municipality_name": loc["municipality_name"],
                    "address": norm_addr,
                    "nearest_station": station_name,
                    "station_walk_min": walk_min,
                    "station_raw": f"{station_name}駅 徒歩{walk_min}分" if (station_name and walk_min) else "",
                    "land_area_sqm": land_area,
                    "building_area_sqm": building_area,
                    "floor_plan": floor_plan,
                    "structure": "鉄筋コンクリート造" if mapped_cat == "マンション" else ("木造・鉄骨等" if mapped_cat == "戸建て" else None),
                    "build_year": build_year,
                    "building_coverage_ratio": None,
                    "floor_area_ratio": None,
                    "zoning": None,
                    "road_frontage": None,
                    "source_site": "ヤフー不動産",
                    "source_url": source_url,
                    "image_urls": images[:4],
                    "features": ["ヤフー不動産掲載"],
                })
    except Exception:
        pass
    finally:
        if own_client:
            await client.aclose()

    return results

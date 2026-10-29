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

NIFTY_CAT_MAP = {
    "売地": ("tochi", "売地"),
    "戸建て": ("kodate", "戸建て"),
    "マンション": ("mansion", "マンション"),
    "空き家・古民家": ("kodate", "空き家・古民家"),
    "収益物件": ("kodate", "収益物件"),
}


async def fetch_nifty_listings(
    pref_code: str = "13",
    muni_code: Optional[str] = None,
    category: str = "売地",
    keyword: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """
    ニフティ不動産（myhome.nifty.com：アットホーム・LIFULL HOME'S・ピタットハウス等を横断掲載）から
    リアルタイムで実物件データを取得・構造化する。
    """
    if category == "賃貸":
        return []

    pc = pref_code.zfill(2)
    pref_info = PREFECTURES.get(pc, PREFECTURES["13"])
    pref_slug = pref_info["slug"]
    cat_slug, mapped_cat = NIFTY_CAT_MAP.get(category, ("tochi", "売地"))

    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0, headers=HEADERS, follow_redirects=True)
        own_client = True

    results: List[Dict[str, Any]] = []
    try:
        base_url = f"https://myhome.nifty.com/{cat_slug}/{pref_slug}/"
        resp = await client.get(base_url)
        if resp.status_code != 200:
            return []

        html_text = resp.text
        soup = BeautifulSoup(html_text, "html.parser")

        # 市区町村指定がある場合、都道府県ページ内のリンクから該当市区町村のURL（_ct/）を解決
        target_muni_name = ""
        if muni_code:
            munis = load_municipalities()
            m_info = munis.get(muni_code.zfill(5))
            if m_info:
                target_muni_name = m_info["muni_name"]
                for a in soup.find_all("a", href=re.compile(rf"^/{cat_slug}/{pref_slug}/[^/]+_ct/")):
                    link_txt = to_halfwidth(a.get_text(strip=True))
                    if target_muni_name in link_txt or link_txt.startswith(target_muni_name[-3:]):
                        sub_url = f"https://myhome.nifty.com{a['href']}"
                        sub_resp = await client.get(sub_url)
                        if sub_resp.status_code == 200:
                            soup = BeautifulSoup(sub_resp.text, "html.parser")
                        break

        seen_urls = set()
        for h2 in soup.find_all(["h2", "h3"]):
            h_text = to_halfwidth(h2.get_text(" ", strip=True))
            if pref_info["name"] not in h_text:
                continue

            card = h2.parent.parent.parent
            if not card:
                continue
            card_text = to_halfwidth(card.get_text(" | ", strip=True))
            if keyword and to_halfwidth(keyword) not in card_text:
                continue

            detail_a = card.find("a", href=re.compile(r"detail_")) or card.find("a", href=True)
            href = detail_a.get("href", "") if detail_a else ""
            source_url = f"https://myhome.nifty.com{href}" if href.startswith("/") else href
            if not source_url or source_url in seen_urls:
                continue
            seen_urls.add(source_url)

            # 住所抽出
            addr_match = re.search(rf"({pref_info['name']}[^\s|]+)", h_text)
            address_raw = addr_match.group(1) if addr_match else h_text
            norm_addr = normalize_address(address_raw)

            # 価格抽出（カンマを含む億・万円表記に対応）
            price_match = re.search(
                r"\|\s*((?:\d+億)?(?:\d[\d,]*(?:\.\d+)?万円|\d+億円))\s*\|",
                card_text,
            )
            if not price_match:
                price_match = re.search(
                    r"((?:\d+億)?(?:\d[\d,]*(?:\.\d+)?万円|\d+億円))",
                    card_text,
                )
            price_yen = parse_japanese_price(price_match.group(1)) if price_match else None
            if not price_yen:
                continue

            # 面積・建蔽率・容積率
            land_match = re.search(r"土地面積\s*\|\s*(\d+(?:,\d{3})*(?:\.\d+)?\s*(?:㎡|m2))", card_text)
            bld_match = re.search(r"(?:建物面積|専有面積)\s*\|\s*(\d+(?:,\d{3})*(?:\.\d+)?\s*(?:㎡|m2))", card_text)
            land_area = parse_area_sqm(land_match.group(1)) if land_match else None
            building_area = parse_area_sqm(bld_match.group(1)) if bld_match else None

            cov_match = re.search(r"建ぺい率\s*\|\s*(\d+(?:\.\d+)?)%", card_text)
            far_match = re.search(r"容積率\s*\|\s*(\d+(?:\.\d+)?)%", card_text)
            coverage = float(cov_match.group(1)) if cov_match else None
            floor_ratio = float(far_match.group(1)) if far_match else None

            st_match = re.search(r"([^\s|]+駅)\s*歩\s*\|?\s*(\d+)\s*\|?\s*分", card_text)
            st_name = st_match.group(1).replace("駅", "") if st_match else None
            walk_min = int(st_match.group(2)) if st_match else None

            # 市区町村名解決
            muni_name = target_muni_name
            if not muni_name:
                rest = norm_addr.replace(pref_info["name"], "", 1)
                mm = re.match(r"^([^0-9]+?[市区町村郡])", rest)
                if mm:
                    muni_name = mm.group(1)

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
                "title": f"{norm_addr}（{mapped_cat}）",
                "headline": h_text,
                "category": mapped_cat,
                "transaction_type": "売買",
                "price": price_yen,
                "monthly_rent": None,
                "price_per_sqm": price_per_sqm,
                "price_per_tsubo": price_per_tsubo,
                "prefecture_code": pc,
                "prefecture_name": pref_info["name"],
                "municipality_name": muni_name,
                "address": norm_addr,
                "nearest_station": st_name,
                "station_walk_min": walk_min,
                "station_raw": f"{st_name}駅 徒歩{walk_min}分" if (st_name and walk_min) else "",
                "land_area_sqm": land_area,
                "building_area_sqm": building_area,
                "floor_plan": None,
                "structure": None,
                "build_year": None,
                "building_coverage_ratio": coverage,
                "floor_area_ratio": floor_ratio,
                "zoning": None,
                "road_frontage": None,
                "source_site": "ニフティ不動産",
                "source_url": source_url,
                "image_urls": [],
                "features": ["複数ポータル横断掲載"],
            })
    except Exception:
        pass
    finally:
        if own_client:
            await client.aclose()

    return results

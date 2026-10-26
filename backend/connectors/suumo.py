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
    parse_ratios,
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

SUUMO_SALE_BS_MAP = {
    "売地": [("030", "売地")],
    "戸建て": [("021", "戸建て"), ("020", "戸建て")],
    "マンション": [("011", "マンション")],
    "空き家・古民家": [("021", "空き家・古民家")],
    "収益物件": [("021", "収益物件"), ("011", "収益物件")],
}


def _extract_Pref_and_muni_from_address(address: str, default_pref_code: str) -> Dict[str, str]:
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


async def fetch_suumo_sale_listings(
    pref_code: str = "13",
    muni_code: Optional[str] = None,
    category: str = "売地",
    keyword: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """
    SUUMO（スーモ）の売買物件一覧ページ（売地・中古戸建・新築戸建・中古マンション）から
    リアルタイムで実物件データを取得・構造化する。
    """
    pc = pref_code.zfill(2)
    pref_info = PREFECTURES.get(pc, PREFECTURES["13"])
    ar = pref_info["suumo_ar"]
    bs_targets = SUUMO_SALE_BS_MAP.get(category, [("030", "売地")])

    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0, headers=HEADERS, follow_redirects=True)
        own_client = True

    results: List[Dict[str, Any]] = []
    try:
        for bs_code, mapped_category in bs_targets:
            params: List[tuple] = [
                ("ar", ar),
                ("bs", bs_code),
                ("ta", pc),
                ("pc", "30"),
            ]
            if muni_code:
                mc = muni_code.zfill(5)
                # 政令指定都市の親コード（末尾00、例：01100 札幌市、14100 横浜市、27100 大阪市）の場合は
                # SUUMOが区単位のscコードを要求するため都道府県検索＋市区町村名フィルタで対応
                if not mc.endswith("00"):
                    params.append(("sc", mc))
            if keyword:
                params.append(("fw2", keyword))

            url = "https://suumo.jp/jj/bukken/ichiran/JJ010FJ001/"
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            units = soup.select(".property_unit")
            for unit in units:
                title_el = unit.select_one(".property_unit-title a")
                if not title_el:
                    continue
                headline = to_halfwidth(title_el.get_text(" ", strip=True))
                href = title_el.get("href", "")
                source_url = f"https://suumo.jp{href}" if href.startswith("/") else href

                fields: Dict[str, str] = {}
                for dl in unit.select("dl"):
                    dt = dl.find("dt")
                    dd = dl.find("dd")
                    if dt and dd:
                        fields[to_halfwidth(dt.get_text(strip=True))] = to_halfwidth(
                            dd.get_text(" ", strip=True)
                        )

                prop_name = fields.get("物件名") or headline
                price_raw = fields.get("販売価格", "")
                price_yen = parse_japanese_price(price_raw)
                address_raw = fields.get("所在地", "")
                if not address_raw or not price_yen:
                    continue

                station_raw = fields.get("沿線・駅", "")
                station_name, walk_min = parse_station_info(station_raw)

                land_area = parse_area_sqm(fields.get("土地面積", ""))
                building_area = parse_area_sqm(
                    fields.get("建物面積", "") or fields.get("専有面積", "")
                )
                coverage, floor_ratio = parse_ratios(fields.get("建ぺい率・容積率", ""))
                floor_plan = fields.get("間取り", "")
                build_year = parse_build_year(fields.get("築年月", ""))
                if bs_code == "020" and not build_year:
                    build_year = 2026

                # タグ一覧
                tags = [
                    to_halfwidth(li.get_text(strip=True))
                    for li in unit.select(".ui-pct")
                    if li.get_text(strip=True)
                ]

                # 画像URL取得
                images: List[str] = []
                for img in unit.select("img"):
                    img_src = img.get("rel") or img.get("data-src") or img.get("src") or ""
                    if img_src.startswith("http") and "spacer" not in img_src:
                        images.append(img_src)

                loc = _extract_Pref_and_muni_from_address(address_raw, pc)
                norm_addr = normalize_address(address_raw)

                # カテゴリ判定の精緻化
                final_cat = mapped_category
                if category == "空き家・古民家" or (
                    mapped_category == "戸建て"
                    and ((build_year and build_year <= 1985) or (price_yen and price_yen <= 9_800_000))
                    and ("古民家" in headline or "空き家" in headline or (price_yen and price_yen <= 8_000_000))
                ):
                    if category == "空き家・古民家":
                        final_cat = "空き家・古民家"
                elif category == "収益物件" or "オーナーチェンジ" in headline or "利回り" in headline or "収益" in headline:
                    if category == "収益物件" or "利回り" in headline or "オーナーチェンジ" in headline:
                        final_cat = "収益物件"

                ref_area = land_area if final_cat == "売地" else (building_area or land_area)
                price_per_sqm = round(price_yen / ref_area) if (price_yen and ref_area and ref_area > 0) else None
                price_per_tsubo = (
                    round(price_yen / (ref_area * TSUBO_PER_SQM))
                    if (price_yen and ref_area and ref_area > 0)
                    else None
                )

                c_hash = compute_canonical_hash(
                    final_cat, norm_addr, price_yen, land_area, building_area
                )

                results.append({
                    "canonical_hash": c_hash,
                    "title": prop_name if prop_name != "-" else headline,
                    "headline": headline,
                    "category": final_cat,
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
                    "station_raw": station_raw,
                    "land_area_sqm": land_area,
                    "building_area_sqm": building_area,
                    "floor_plan": floor_plan or None,
                    "structure": "木造・鉄骨等" if final_cat in ("戸建て", "空き家・古民家") else ("鉄筋コンクリート造" if final_cat == "マンション" else None),
                    "build_year": build_year,
                    "building_coverage_ratio": coverage,
                    "floor_area_ratio": floor_ratio,
                    "zoning": None,
                    "road_frontage": None,
                    "source_site": "スーモ",
                    "source_url": source_url,
                    "image_urls": images[:5],
                    "features": tags,
                })
    except Exception:
        pass
    finally:
        if own_client:
            await client.aclose()

    return results


async def fetch_suumo_rent_listings(
    pref_code: str = "13",
    muni_code: Optional[str] = None,
    keyword: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """SUUMO（スーモ）の賃貸物件一覧ページからリアルタイムで実賃貸物件データを取得・構造化する。"""
    pc = pref_code.zfill(2)
    pref_info = PREFECTURES.get(pc, PREFECTURES["13"])
    ar = pref_info["suumo_ar"]

    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0, headers=HEADERS, follow_redirects=True)
        own_client = True

    results: List[Dict[str, Any]] = []
    try:
        params: List[tuple] = [
            ("ar", ar),
            ("bs", "040"),
            ("ta", pc),
            ("pc", "20"),
        ]
        if muni_code and not muni_code.zfill(5).endswith("00"):
            params.append(("sc", muni_code.zfill(5)))
        if keyword:
            params.append(("fw2", keyword))

        url = "https://suumo.jp/jj/chintai/ichiran/FR301FC001/"
        resp = await client.get(url, params=params)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        cassettes = soup.select(".cassetteitem")
        for cas in cassettes:
            title_el = cas.select_one(".cassetteitem_content-title")
            addr_el = cas.select_one(".cassetteitem_detail-col1")
            if not title_el or not addr_el:
                continue
            b_title = to_halfwidth(title_el.get_text(strip=True))
            addr_raw = to_halfwidth(addr_el.get_text(strip=True))
            norm_addr = normalize_address(addr_raw)
            loc = _extract_Pref_and_muni_from_address(norm_addr, pc)

            st_el = cas.select_one(".cassetteitem_detail-col2 .cassetteitem_detail-text")
            st_raw = to_halfwidth(st_el.get_text(strip=True)) if st_el else ""
            st_name, walk_min = parse_station_info(st_raw)

            col3_text = to_halfwidth(
                cas.select_one(".cassetteitem_detail-col3").get_text(" ", strip=True)
            ) if cas.select_one(".cassetteitem_detail-col3") else ""
            build_year = parse_build_year(col3_text)

            label_el = cas.select_one(".cassetteitem_content-label")
            b_label = to_halfwidth(label_el.get_text(strip=True)) if label_el else "賃貸物件"

            images: List[str] = []
            for img in cas.select(".cassetteitem_object-item img"):
                src = img.get("rel") or img.get("src") or ""
                if src.startswith("http"):
                    images.append(src)

            # 1棟内の最初の部屋行を取得
            first_row = cas.select_one("tbody tr.js-cassette_link")
            if not first_row:
                continue

            rent_el = first_row.select_one(".cassetteitem_price--rent")
            rent_yen = parse_japanese_price(rent_el.get_text(strip=True)) if rent_el else None
            if not rent_yen:
                continue

            madori_el = first_row.select_one(".cassetteitem_madori")
            menseki_el = first_row.select_one(".cassetteitem_menseki")
            floor_plan = to_halfwidth(madori_el.get_text(strip=True)) if madori_el else None
            area_sqm = parse_area_sqm(menseki_el.get_text(strip=True)) if menseki_el else None

            detail_a = first_row.select_one("a[href*='/chintai/']")
            href = detail_a.get("href", "") if detail_a else ""
            source_url = f"https://suumo.jp{href}" if href.startswith("/") else (href or "https://suumo.jp/chintai/")

            c_hash = compute_canonical_hash("賃貸", norm_addr, rent_yen, None, area_sqm)
            results.append({
                "canonical_hash": c_hash,
                "title": b_title,
                "headline": f"{b_label}・{b_title}",
                "category": "賃貸",
                "transaction_type": "賃貸",
                "price": rent_yen,
                "monthly_rent": rent_yen,
                "price_per_sqm": round(rent_yen / area_sqm) if (area_sqm and area_sqm > 0) else None,
                "price_per_tsubo": round(rent_yen / (area_sqm * TSUBO_PER_SQM)) if (area_sqm and area_sqm > 0) else None,
                "prefecture_code": loc["prefecture_code"],
                "prefecture_name": loc["prefecture_name"],
                "municipality_name": loc["municipality_name"],
                "address": norm_addr,
                "nearest_station": st_name,
                "station_walk_min": walk_min,
                "station_raw": st_raw,
                "land_area_sqm": None,
                "building_area_sqm": area_sqm,
                "floor_plan": floor_plan,
                "structure": b_label,
                "build_year": build_year,
                "building_coverage_ratio": None,
                "floor_area_ratio": None,
                "zoning": None,
                "road_frontage": None,
                "source_site": "スーモ",
                "source_url": source_url,
                "image_urls": images[:3],
                "features": [b_label],
            })
    except Exception:
        pass
    finally:
        if own_client:
            await client.aclose()

    return results

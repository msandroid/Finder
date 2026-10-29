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

SAPPORO_WARD_CODE_MAP = {
    "01101": ("a", "札幌市中央区"),
    "01102": ("i", "札幌市北区"),
    "01103": ("u", "札幌市東区"),
    "01104": ("e", "札幌市白石区"),
    "01105": ("o", "札幌市豊平区"),
    "01106": ("ka", "札幌市南区"),
    "01107": ("ki", "札幌市西区"),
    "01108": ("ku", "札幌市厚別区"),
    "01109": ("ke", "札幌市手稲区"),
    "01110": ("ko", "札幌市清田区"),
}


async def fetch_sapporo_tenant_offices(
    muni_code: Optional[str] = None,
    keyword: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """
    札幌テナント情報（sapporo-tenant.com）から札幌市内（中央区・北区ほか全10区）の
    少人数向け貸事務所・コンパクトオフィス（約5坪〜15坪 / 約16平米〜50平米中心）の
    実在募集物件をリアルタイム取得・構造化する。
    """
    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0, headers=HEADERS, follow_redirects=True)
        own_client = True

    results: List[Dict[str, Any]] = []
    try:
        # 対象区の決定（札幌市全体または指定区）
        ward_codes: List[str] = []
        if muni_code and muni_code.zfill(5) in SAPPORO_WARD_CODE_MAP:
            ward_codes = [SAPPORO_WARD_CODE_MAP[muni_code.zfill(5)][0]]
        else:
            # デフォルトはオフィス需要が高い中央区(a)と北区(i)を優先走査
            ward_codes = ["a", "i"]

        urls: List[str] = []
        for wc in ward_codes:
            for page in (1, 2, 3):
                urls.append(
                    f"https://www.sapporo-tenant.com/list.php?syubetsu=1&e[]={wc}&menseki_kagen=5&menseki_jyogen=15&page={page}"
                )

        seen_hrefs = set()
        for url in urls:
            resp = await client.get(url)
            if resp.status_code != 200:
                continue
            resp.encoding = "cp932"
            soup = BeautifulSoup(resp.text, "html.parser")

            for tbl in soup.find_all("table"):
                txt = to_halfwidth(tbl.get_text(" | ", strip=True))
                if "テナントを詳しく見る" not in txt or "住所:" not in txt or len(txt) > 1400:
                    continue

                if keyword and to_halfwidth(keyword) not in txt:
                    continue

                a_det = tbl.find("a", href=re.compile(r"Details\d+\.html"))
                href = a_det.get("href", "") if a_det else ""
                if not href or href in seen_hrefs:
                    continue
                seen_hrefs.add(href)
                source_url = f"https://www.sapporo-tenant.com/{href.lstrip('/')}"

                # 住所抽出
                addr_m = re.search(r"住所:\s*\|\s*([^\s|]+)", txt) or re.search(
                    r"住所:\s*([^\s|]+)", txt
                )
                addr_raw = addr_m.group(1).strip() if addr_m else "札幌市中央区"
                if not addr_raw.startswith("北海道"):
                    addr_raw = f"北海道{addr_raw}"
                norm_addr = normalize_address(addr_raw)

                # 市区町村名抽出
                muni_m = re.search(r"(札幌市[^\d\s|]+?区)", norm_addr)
                muni_name = muni_m.group(1) if muni_m else "札幌市中央区"

                # 賃料・共益費・管理費抽出
                rent_m = re.search(r"賃料:\s*\|\s*([\d,]+)\s*\|\s*円", txt)
                if not rent_m:
                    continue
                rent_yen = int(rent_m.group(1).replace(",", ""))
                if rent_yen < 20_000:
                    # 曜日貸し等の特殊物件を除外し常設オフィスに絞る
                    continue

                fee_m = re.search(r"(?:管理費|共益費):([\d,]+)円", txt)
                common_fee_yen = int(fee_m.group(1).replace(",", "")) if fee_m else 0
                total_monthly_yen = rent_yen + common_fee_yen

                # 面積抽出
                area_m = re.search(r"面積:\s*\|\s*(\d+(?:\.\d+)?)\s*(?:㎡|m2)?\s+(\d+(?:\.\d+)?)坪", txt)
                if area_m:
                    area_sqm = round(float(area_m.group(1)), 2)
                    area_tsubo = round(float(area_m.group(2)), 2)
                else:
                    tsubo_only = re.search(r"(\d+(?:\.\d+)?)坪", txt)
                    area_tsubo = float(tsubo_only.group(1)) if tsubo_only else 10.0
                    area_sqm = round(area_tsubo / TSUBO_PER_SQM, 2)

                # 交通・最寄駅・徒歩分数
                st_m = re.search(r"「([^」]+)」\s*徒歩\s*(\d+)\s*分", txt)
                if st_m:
                    st_name = st_m.group(1).replace("駅", "")
                    walk_min = int(st_m.group(2))
                else:
                    st_name, walk_min = parse_station_info(txt)

                # コメント・特徴抽出（外部サイト由来の記号・絵文字・文字化けを完全除去）
                comment_m = re.search(r"コメント:([^|]+)", txt)
                comment = comment_m.group(1).strip() if comment_m else "少人数向け貸事務所・テナント"
                clean_comment = re.sub(
                    r"[★☆♪◆◇◎≪≫【】<>＜＞\u2600-\u27bf\U00010000-\U0010ffff]",
                    " ",
                    comment,
                )
                clean_comment = re.sub(r"\s+", " ", clean_comment).strip()

                price_per_sqm = round(total_monthly_yen / area_sqm) if area_sqm > 0 else None
                price_per_tsubo = round(total_monthly_yen / area_tsubo) if area_tsubo > 0 else None

                short_place = norm_addr.replace("北海道札幌市", "")
                title = f"{short_place} 貸事務所（{area_tsubo}坪・共益費込月額{total_monthly_yen:,}円）"

                c_hash = compute_canonical_hash(
                    "オフィス・事務所", norm_addr, total_monthly_yen, None, area_sqm
                )

                features = ["事業用貸事務所", f"専有{area_tsubo}坪（{area_sqm}平米）"]
                if common_fee_yen > 0:
                    features.append(f"賃料{rent_yen:,}円＋共益費{common_fee_yen:,}円")
                if "24時間" in comment:
                    features.append("24時間利用可能")
                if "駐車場" in comment:
                    features.append("駐車場相談可")
                if "ロードヒーティング" in comment:
                    features.append("ロードヒーティング完備")

                results.append({
                    "canonical_hash": c_hash,
                    "title": title,
                    "headline": clean_comment[:100],
                    "category": "オフィス・事務所",
                    "transaction_type": "賃貸",
                    "price": total_monthly_yen,
                    "monthly_rent": total_monthly_yen,
                    "price_per_sqm": price_per_sqm,
                    "price_per_tsubo": price_per_tsubo,
                    "prefecture_code": "01",
                    "prefecture_name": "北海道",
                    "municipality_name": muni_name,
                    "address": norm_addr,
                    "nearest_station": st_name,
                    "station_walk_min": walk_min,
                    "station_raw": f"{st_name}駅 徒歩{walk_min}分" if (st_name and walk_min) else "",
                    "land_area_sqm": None,
                    "building_area_sqm": area_sqm,
                    "floor_plan": "少人数事務所",
                    "structure": "鉄筋コンクリート造・オフィス仕様",
                    "build_year": None,
                    "building_coverage_ratio": None,
                    "floor_area_ratio": None,
                    "zoning": "商業・近隣商業地域",
                    "road_frontage": clean_comment[:60],
                    "source_site": "札幌テナント情報",
                    "source_url": source_url,
                    "image_urls": [],
                    "features": features,
                })
    except Exception:
        pass
    finally:
        if own_client:
            await client.aclose()

    return results


async def fetch_suumo_soho_office_listings(
    pref_code: str = "01",
    muni_code: Optional[str] = None,
    keyword: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """
    SUUMO（スーモ）の「事務所利用可（tc=0401301）」賃貸物件をリアルタイム取得し、
    2〜5名の少人数オフィス・兼業事務所（20平米〜65平米）に適した実物件を構造化する。
    """
    pc = pref_code.zfill(2)
    pref_info = PREFECTURES.get(pc, PREFECTURES["01"])
    ar = pref_info["suumo_ar"]

    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0, headers=HEADERS, follow_redirects=True)
        own_client = True

    results: List[Dict[str, Any]] = []
    try:
        # 政令市親コード（01100 札幌市など）の場合は中央区・北区を優先検索
        target_sc_list: List[Optional[str]] = []
        if muni_code:
            mc = muni_code.zfill(5)
            if mc == "01100":
                target_sc_list = ["01101", "01102"]
            elif not mc.endswith("00"):
                target_sc_list = [mc]
            else:
                target_sc_list = [None]
        elif pc == "01":
            target_sc_list = ["01101", "01102"]
        else:
            target_sc_list = [None]

        munis = load_municipalities()

        for sc_code in target_sc_list:
            params: List[tuple] = [
                ("ar", ar),
                ("bs", "040"),
                ("ta", pc),
                ("tc", "0401301"),  # 事務所利用可
                ("pc", "30"),
            ]
            if sc_code:
                params.append(("sc", sc_code))
            if keyword:
                params.append(("fw2", keyword))

            url = "https://suumo.jp/jj/chintai/ichiran/FR301FC001/"
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                continue

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

                # 市区町村解決
                muni_name = ""
                for m in munis.values():
                    if m["pref_code"] == pc and m["muni_name"] in norm_addr:
                        if len(m["muni_name"]) > len(muni_name):
                            muni_name = m["muni_name"]

                st_el = cas.select_one(".cassetteitem_detail-col2 .cassetteitem_detail-text")
                st_raw = to_halfwidth(st_el.get_text(strip=True)) if st_el else ""
                st_name, walk_min = parse_station_info(st_raw)

                col3_text = (
                    to_halfwidth(
                        cas.select_one(".cassetteitem_detail-col3").get_text(" ", strip=True)
                    )
                    if cas.select_one(".cassetteitem_detail-col3")
                    else ""
                )
                build_year = parse_build_year(col3_text)

                first_row = cas.select_one("tbody tr.js-cassette_link")
                if not first_row:
                    continue

                rent_el = first_row.select_one(".cassetteitem_price--rent")
                admin_el = first_row.select_one(".cassetteitem_price--administration")
                rent_yen = parse_japanese_price(rent_el.get_text(strip=True)) if rent_el else None
                admin_yen = (
                    parse_japanese_price(admin_el.get_text(strip=True)) if admin_el else 0
                ) or 0
                if not rent_yen:
                    continue
                total_rent_yen = rent_yen + admin_yen

                madori_el = first_row.select_one(".cassetteitem_madori")
                menseki_el = first_row.select_one(".cassetteitem_menseki")
                floor_plan = to_halfwidth(madori_el.get_text(strip=True)) if madori_el else "事務所可"
                area_sqm = parse_area_sqm(menseki_el.get_text(strip=True)) if menseki_el else None

                # 少人数オフィス（18平米〜85平米）として使えるサイズを抽出
                if area_sqm and (area_sqm < 18.0 or area_sqm > 85.0):
                    continue

                detail_a = first_row.select_one("a[href*='/chintai/']")
                href = detail_a.get("href", "") if detail_a else ""
                source_url = (
                    f"https://suumo.jp{href}"
                    if href.startswith("/")
                    else (href or "https://suumo.jp/chintai/")
                )

                area_tsubo = round(area_sqm * TSUBO_PER_SQM, 2) if area_sqm else None
                price_per_sqm = (
                    round(total_rent_yen / area_sqm) if (area_sqm and area_sqm > 0) else None
                )
                price_per_tsubo = (
                    round(total_rent_yen / area_tsubo)
                    if (area_tsubo and area_tsubo > 0)
                    else None
                )

                c_hash = compute_canonical_hash(
                    "オフィス・事務所", norm_addr, total_rent_yen, None, area_sqm
                )

                results.append({
                    "canonical_hash": c_hash,
                    "title": f"{b_title}（事務所利用可・{area_tsubo or '-'}坪）",
                    "headline": f"事務所・兼業利用可マンション｜{st_raw}",
                    "category": "オフィス・事務所",
                    "transaction_type": "賃貸",
                    "price": total_rent_yen,
                    "monthly_rent": total_rent_yen,
                    "price_per_sqm": price_per_sqm,
                    "price_per_tsubo": price_per_tsubo,
                    "prefecture_code": pc,
                    "prefecture_name": pref_info["name"],
                    "municipality_name": muni_name,
                    "address": norm_addr,
                    "nearest_station": st_name,
                    "station_walk_min": walk_min,
                    "station_raw": st_raw,
                    "land_area_sqm": None,
                    "building_area_sqm": area_sqm,
                    "floor_plan": floor_plan,
                    "structure": "鉄筋コンクリート造（事務所利用可）",
                    "build_year": build_year,
                    "building_coverage_ratio": None,
                    "floor_area_ratio": None,
                    "zoning": None,
                    "road_frontage": None,
                    "source_site": "スーモ",
                    "source_url": source_url,
                    "image_urls": [],
                    "features": ["事務所利用可", f"管理費込月額{total_rent_yen:,}円"],
                })
    except Exception:
        pass
    finally:
        if own_client:
            await client.aclose()

    return results

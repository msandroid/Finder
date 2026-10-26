import json
import re
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
MUNI_CACHE_FILE = DATA_DIR / "municipalities_cache.json"

# 47都道府県の公式JISコード・名称・地域コード・中心座標・ポータルスラッグ定義
PREFECTURES: Dict[str, Dict[str, Any]] = {
    "01": {"code": "01", "name": "北海道", "region": "北海道", "suumo_ar": "010", "yahoo_reg": "01", "slug": "hokkaido", "lat": 43.06417, "lon": 141.34694},
    "02": {"code": "02", "name": "青森県", "region": "東北", "suumo_ar": "020", "yahoo_reg": "02", "slug": "aomori", "lat": 40.82444, "lon": 140.74000},
    "03": {"code": "03", "name": "岩手県", "region": "東北", "suumo_ar": "020", "yahoo_reg": "02", "slug": "iwate", "lat": 39.70361, "lon": 141.15250},
    "04": {"code": "04", "name": "宮城県", "region": "東北", "suumo_ar": "020", "yahoo_reg": "02", "slug": "miyagi", "lat": 38.26889, "lon": 140.87194},
    "05": {"code": "05", "name": "秋田県", "region": "東北", "suumo_ar": "020", "yahoo_reg": "02", "slug": "akita", "lat": 39.71861, "lon": 140.10250},
    "06": {"code": "06", "name": "山形県", "region": "東北", "suumo_ar": "020", "yahoo_reg": "02", "slug": "yamagata", "lat": 38.24056, "lon": 140.36333},
    "07": {"code": "07", "name": "福島県", "region": "東北", "suumo_ar": "020", "yahoo_reg": "02", "slug": "fukushima", "lat": 37.75000, "lon": 140.46778},
    "08": {"code": "08", "name": "茨城県", "region": "関東", "suumo_ar": "030", "yahoo_reg": "03", "slug": "ibaraki", "lat": 36.34139, "lon": 140.44667},
    "09": {"code": "09", "name": "栃木県", "region": "関東", "suumo_ar": "030", "yahoo_reg": "03", "slug": "tochigi", "lat": 36.56583, "lon": 139.88361},
    "10": {"code": "10", "name": "群馬県", "region": "関東", "suumo_ar": "030", "yahoo_reg": "03", "slug": "gunma", "lat": 36.39111, "lon": 139.06083},
    "11": {"code": "11", "name": "埼玉県", "region": "関東", "suumo_ar": "030", "yahoo_reg": "03", "slug": "saitama", "lat": 35.85694, "lon": 139.64889},
    "12": {"code": "12", "name": "千葉県", "region": "関東", "suumo_ar": "030", "yahoo_reg": "03", "slug": "chiba", "lat": 35.60472, "lon": 140.12333},
    "13": {"code": "13", "name": "東京都", "region": "関東", "suumo_ar": "030", "yahoo_reg": "03", "slug": "tokyo", "lat": 35.68944, "lon": 139.69167},
    "14": {"code": "14", "name": "神奈川県", "region": "関東", "suumo_ar": "030", "yahoo_reg": "03", "slug": "kanagawa", "lat": 35.44778, "lon": 139.64250},
    "15": {"code": "15", "name": "新潟県", "region": "甲信越・北陸", "suumo_ar": "040", "yahoo_reg": "04", "slug": "niigata", "lat": 37.90222, "lon": 139.02361},
    "16": {"code": "16", "name": "富山県", "region": "甲信越・北陸", "suumo_ar": "040", "yahoo_reg": "04", "slug": "toyama", "lat": 36.69528, "lon": 137.21139},
    "17": {"code": "17", "name": "石川県", "region": "甲信越・北陸", "suumo_ar": "040", "yahoo_reg": "04", "slug": "ishikawa", "lat": 36.59444, "lon": 136.62556},
    "18": {"code": "18", "name": "福井県", "region": "甲信越・北陸", "suumo_ar": "040", "yahoo_reg": "04", "slug": "fukui", "lat": 36.06528, "lon": 136.22194},
    "19": {"code": "19", "name": "山梨県", "region": "甲信越・北陸", "suumo_ar": "040", "yahoo_reg": "04", "slug": "yamanashi", "lat": 35.66389, "lon": 138.56833},
    "20": {"code": "20", "name": "長野県", "region": "甲信越・北陸", "suumo_ar": "040", "yahoo_reg": "04", "slug": "nagano", "lat": 36.65139, "lon": 138.18111},
    "21": {"code": "21", "name": "岐阜県", "region": "東海", "suumo_ar": "050", "yahoo_reg": "05", "slug": "gifu", "lat": 35.39111, "lon": 136.72222},
    "22": {"code": "22", "name": "静岡県", "region": "東海", "suumo_ar": "050", "yahoo_reg": "05", "slug": "shizuoka", "lat": 34.97694, "lon": 138.38306},
    "23": {"code": "23", "name": "愛知県", "region": "東海", "suumo_ar": "050", "yahoo_reg": "05", "slug": "aichi", "lat": 35.18028, "lon": 136.90667},
    "24": {"code": "24", "name": "三重県", "region": "東海", "suumo_ar": "050", "yahoo_reg": "05", "slug": "mie", "lat": 34.73028, "lon": 136.50861},
    "25": {"code": "25", "name": "滋賀県", "region": "関西", "suumo_ar": "060", "yahoo_reg": "06", "slug": "shiga", "lat": 35.00444, "lon": 135.86833},
    "26": {"code": "26", "name": "京都府", "region": "関西", "suumo_ar": "060", "yahoo_reg": "06", "slug": "kyoto", "lat": 35.02139, "lon": 135.75556},
    "27": {"code": "27", "name": "大阪府", "region": "関西", "suumo_ar": "060", "yahoo_reg": "06", "slug": "osaka", "lat": 34.68639, "lon": 135.52000},
    "28": {"code": "28", "name": "兵庫県", "region": "関西", "suumo_ar": "060", "yahoo_reg": "06", "slug": "hyogo", "lat": 34.69139, "lon": 135.18306},
    "29": {"code": "29", "name": "奈良県", "region": "関西", "suumo_ar": "060", "yahoo_reg": "06", "slug": "nara", "lat": 34.68528, "lon": 135.83278},
    "30": {"code": "30", "name": "和歌山県", "region": "関西", "suumo_ar": "060", "yahoo_reg": "06", "slug": "wakayama", "lat": 34.22611, "lon": 135.16750},
    "31": {"code": "31", "name": "鳥取県", "region": "中国", "suumo_ar": "080", "yahoo_reg": "07", "slug": "tottori", "lat": 35.50361, "lon": 134.23833},
    "32": {"code": "32", "name": "島根県", "region": "中国", "suumo_ar": "080", "yahoo_reg": "07", "slug": "shimane", "lat": 35.47222, "lon": 133.05056},
    "33": {"code": "33", "name": "岡山県", "region": "中国", "suumo_ar": "080", "yahoo_reg": "07", "slug": "okayama", "lat": 34.66167, "lon": 133.93500},
    "34": {"code": "34", "name": "広島県", "region": "中国", "suumo_ar": "080", "yahoo_reg": "07", "slug": "hiroshima", "lat": 34.39639, "lon": 132.45944},
    "35": {"code": "35", "name": "山口県", "region": "中国", "suumo_ar": "080", "yahoo_reg": "07", "slug": "yamaguchi", "lat": 34.18583, "lon": 131.47139},
    "36": {"code": "36", "name": "徳島県", "region": "四国", "suumo_ar": "070", "yahoo_reg": "08", "slug": "tokushima", "lat": 34.06583, "lon": 134.55944},
    "37": {"code": "37", "name": "香川県", "region": "四国", "suumo_ar": "070", "yahoo_reg": "08", "slug": "kagawa", "lat": 34.34028, "lon": 134.04333},
    "38": {"code": "38", "name": "愛媛県", "region": "四国", "suumo_ar": "070", "yahoo_reg": "08", "slug": "ehime", "lat": 33.84167, "lon": 132.76611},
    "39": {"code": "39", "name": "高知県", "region": "四国", "suumo_ar": "070", "yahoo_reg": "08", "slug": "kochi", "lat": 33.55972, "lon": 133.53111},
    "40": {"code": "40", "name": "福岡県", "region": "九州・沖縄", "suumo_ar": "090", "yahoo_reg": "09", "slug": "fukuoka", "lat": 33.60639, "lon": 130.41806},
    "41": {"code": "41", "name": "佐賀県", "region": "九州・沖縄", "suumo_ar": "090", "yahoo_reg": "09", "slug": "saga", "lat": 33.24944, "lon": 130.29889},
    "42": {"code": "42", "name": "長崎県", "region": "九州・沖縄", "suumo_ar": "090", "yahoo_reg": "09", "slug": "nagasaki", "lat": 32.74472, "lon": 129.87361},
    "43": {"code": "43", "name": "熊本県", "region": "九州・沖縄", "suumo_ar": "090", "yahoo_reg": "09", "slug": "kumamoto", "lat": 32.78972, "lon": 130.74167},
    "44": {"code": "44", "name": "大分県", "region": "九州・沖縄", "suumo_ar": "090", "yahoo_reg": "09", "slug": "oita", "lat": 33.23806, "lon": 131.61250},
    "45": {"code": "45", "name": "宮崎県", "region": "九州・沖縄", "suumo_ar": "090", "yahoo_reg": "09", "slug": "miyazaki", "lat": 31.91111, "lon": 131.42389},
    "46": {"code": "46", "name": "鹿児島県", "region": "九州・沖縄", "suumo_ar": "090", "yahoo_reg": "09", "slug": "kagoshima", "lat": 31.56028, "lon": 130.55806},
    "47": {"code": "47", "name": "沖縄県", "region": "九州・沖縄", "suumo_ar": "090", "yahoo_reg": "09", "slug": "okinawa", "lat": 26.21250, "lon": 127.68111},
}

_MUNI_CACHE: Optional[Dict[str, Dict[str, str]]] = None


def load_municipalities() -> Dict[str, Dict[str, str]]:
    """国土地理院の公式市区町村コード辞書（muni.js）を取得しローカルにキャッシュして返す。"""
    global _MUNI_CACHE
    if _MUNI_CACHE is not None:
        return _MUNI_CACHE

    if MUNI_CACHE_FILE.exists():
        try:
            _MUNI_CACHE = json.loads(MUNI_CACHE_FILE.read_text(encoding="utf-8"))
            if _MUNI_CACHE:
                return _MUNI_CACHE
        except Exception:
            pass

    url = "https://maps.gsi.go.jp/js/muni.js"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"
    }
    muni_dict: Dict[str, Dict[str, str]] = {}
    try:
        req = urllib.request.Request(url, headers=headers)
        raw = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
        # GSI.MUNI_ARRAY["1100"] = '1,北海道,1100,札幌市';
        pattern = re.compile(r'GSI\.MUNI_ARRAY\["(\d+)"\]\s*=\s*\'(\d+),([^,]+),(\d+),([^\']+)\'')
        for match in pattern.finditer(raw):
            _, pref_num, pref_name, muni_num, muni_name = match.groups()
            pref_code = pref_num.zfill(2)
            muni_code = muni_num.zfill(5)
            clean_muni = muni_name.replace("　", "").strip()
            muni_dict[muni_code] = {
                "muni_code": muni_code,
                "pref_code": pref_code,
                "pref_name": pref_name.strip(),
                "muni_name": clean_muni,
                "raw_muni_name": muni_name.strip(),
                "full_name": f"{pref_name.strip()}{clean_muni}",
            }
        if muni_dict:
            MUNI_CACHE_FILE.write_text(
                json.dumps(muni_dict, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    except Exception:
        pass

    _MUNI_CACHE = muni_dict
    return _MUNI_CACHE


def resolve_prefecture(query: str) -> Optional[Dict[str, Any]]:
    """都道府県コード（01〜47）または都道府県名（東京、東京都、北海道など）から都道府県情報を解決する。"""
    if not query:
        return None
    q = query.strip()
    if q.isdigit() and q.zfill(2) in PREFECTURES:
        return PREFECTURES[q.zfill(2)]
    for code, info in PREFECTURES.items():
        name = info["name"]
        short_name = name.rstrip("都道府県") if name != "北海道" else "北海道"
        if q == name or q == short_name or q.lower() == info["slug"]:
            return info
    return None


def get_municipalities_by_prefecture(pref_code: str) -> List[Dict[str, str]]:
    """指定した都道府県コードに属する全市区町村リストを返す。"""
    pc = pref_code.zfill(2)
    munis = load_municipalities()
    result = [m for m in munis.values() if m["pref_code"] == pc]
    result.sort(key=lambda x: x["muni_code"])
    return result


def resolve_location(prefecture: Optional[str] = None, municipality: Optional[str] = None) -> Dict[str, Any]:
    """都道府県名/コード・市区町村名/コードから正規化されたJISコードと名称を返す。"""
    munis = load_municipalities()
    pref_info = resolve_prefecture(prefecture) if prefecture else None
    muni_info = None

    if municipality:
        mq = municipality.strip().replace("　", "").replace(" ", "")
        if mq.isdigit() and mq.zfill(5) in munis:
            muni_info = munis[mq.zfill(5)]
            if not pref_info:
                pref_info = PREFECTURES.get(muni_info["pref_code"])
        else:
            candidates = [
                m for m in munis.values()
                if (not pref_info or m["pref_code"] == pref_info["code"])
                and (
                    m["muni_name"] == mq
                    or m["full_name"] == mq
                    or mq in m["muni_name"]
                    or m["muni_name"].endswith(mq)
                )
            ]
            # 完全一致優先
            exact = [m for m in candidates if m["muni_name"] == mq or m["full_name"] == mq]
            muni_info = exact[0] if exact else (candidates[0] if candidates else None)
            if muni_info and not pref_info:
                pref_info = PREFECTURES.get(muni_info["pref_code"])

    if not pref_info and prefecture:
        # 住所文字列に都道府県が含まれる場合
        for p in PREFECTURES.values():
            if p["name"] in prefecture:
                pref_info = p
                break

    return {
        "prefecture": pref_info,
        "municipality": muni_info,
    }

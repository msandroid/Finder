import hashlib
import re
import unicodedata
from typing import Optional, Tuple

TSUBO_PER_SQM = 0.3025  # 1平米 = 0.3025坪 (1坪 ≈ 3.305785平米)


def to_halfwidth(text: str) -> str:
    """全角英数字・記号を半角に正規化する。"""
    if not text:
        return ""
    return unicodedata.normalize("NFKC", text).strip()


def parse_japanese_price(text: str) -> Optional[int]:
    """
    日本語の価格表記（例：'1億3,800万円', '5740万円～8980万円', '18.5万円', '85,000円'）を
    円単位の整数（int）に変換する。範囲表記の場合は下限値を採用する。
    """
    if not text:
        return None
    norm = to_halfwidth(text).replace(",", "").replace(" ", "")
    if "相談" in norm or "未定" in norm or norm == "-":
        return None

    # 範囲表記（～, ~, -）がある場合は先頭側をパース
    first_part = re.split(r"[~～〜\-－]", norm)[0]

    total_yen = 0
    matched = False

    oku_match = re.search(r"(\d+(?:\.\d+)?)億", first_part)
    if oku_match:
        total_yen += int(float(oku_match.group(1)) * 100_000_000)
        matched = True

    man_match = re.search(r"(\d+(?:\.\d+)?)万", first_part)
    if man_match:
        total_yen += int(float(man_match.group(1)) * 10_000)
        matched = True

    if not matched:
        yen_match = re.search(r"(\d+(?:\.\d+)?)円", first_part)
        if yen_match:
            total_yen += int(float(yen_match.group(1)))
            matched = True

    return total_yen if matched and total_yen > 0 else None


def parse_area_sqm(text: str) -> Optional[float]:
    """面積表記（例：'100.03m2～150.08m2', '51.32㎡（実測）', '83.04m 2'）から平米数（float）を抽出する。"""
    if not text:
        return None
    norm = to_halfwidth(text)
    # 3桁区切りのカンマのみ除去（'94,73.1㎡' のような複数区画列挙で結合されないようにする）
    norm = re.sub(r"(\d),(\d{3})(?!\d)", r"\1\2", norm)
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:m2|m\s*2|㎡|平米)", norm, re.IGNORECASE)
    if match:
        val = float(match.group(1))
        return round(val, 2) if val > 0 else None
    return None


def parse_ratios(text: str) -> Tuple[Optional[float], Optional[float]]:
    """建蔽率・容積率表記（例：'70％・160％', '建ぺい率：50％/容積率：100％'）から数値を抽出する。"""
    if not text:
        return None, None
    norm = to_halfwidth(text)
    nums = re.findall(r"(\d+(?:\.\d+)?)\s*%", norm)
    if len(nums) >= 2:
        return float(nums[0]), float(nums[1])
    if len(nums) == 1:
        return float(nums[0]), None
    return None, None


def parse_station_info(text: str) -> Tuple[Optional[str], Optional[int]]:
    """沿線・駅表記（例：'東京メトロ千代田線「赤坂」徒歩5分', 'JR山手線/品川駅 歩8分'）から駅名と徒歩分数を抽出する。"""
    if not text:
        return None, None
    norm = to_halfwidth(text)
    station_name = None
    bracket_match = re.search(r"[「『]([^」』]+)[」』]", norm)
    if bracket_match:
        station_name = bracket_match.group(1).strip()
    else:
        slash_match = re.search(r"[/／]([^\s歩徒]+?)(?:駅)?[\s歩徒]", norm)
        if slash_match:
            station_name = slash_match.group(1).strip()
        else:
            st_match = re.search(r"([^\s「」『』/／]+?)駅", norm)
            if st_match:
                station_name = st_match.group(1).strip()

    walk_min = None
    walk_match = re.search(r"(?:徒歩|歩)\s*(\d+)\s*分", norm)
    if walk_match:
        walk_min = int(walk_match.group(1))

    return station_name, walk_min


def parse_build_year(text: str, current_year: int = 2026) -> Optional[int]:
    """築年月表記（例：'1988年11月', '築18年', '2024年3月'）から西暦年（int）を抽出する。"""
    if not text:
        return None
    norm = to_halfwidth(text)
    year_match = re.search(r"(19\d{2}|20\d{2})\s*年", norm)
    if year_match:
        return int(year_match.group(1))
    age_match = re.search(r"築\s*(\d+)\s*年", norm)
    if age_match:
        return current_year - int(age_match.group(1))
    if "新築" in norm:
        return current_year
    return None


def normalize_address(address: str) -> str:
    """名寄せおよびジオコーディング用に日本の住所文字列を正規化する。"""
    if not address:
        return ""
    norm = to_halfwidth(address)
    norm = re.sub(r"\s+", "", norm)
    # 丁目・番地・号の表記ゆれ統一（名寄せキー用には町丁目レベルまで正規化）
    norm = re.sub(r"(\d+)丁目(\d+)番(\d+)号?", r"\1-\2-\3", norm)
    norm = re.sub(r"(\d+)丁目(\d+)番?", r"\1-\2", norm)
    norm = re.sub(r"(\d+)丁目", r"\1", norm)
    return norm


def extract_town_level_address(address: str) -> str:
    """異なるポータル間で丁目・番地以降の省略有無が異なっても名寄せできるよう、大字・町名単位の正規化住所を返す。"""
    norm = to_halfwidth(address)
    norm = re.sub(r"\s+", "", norm)
    # 丁目や番地（数字以降）を除去した町名（例：'東京都港区赤坂4-2-1' / '東京都港区赤坂' -> '東京都港区赤坂'）
    base = re.sub(r"\d+.*$", "", norm)
    return base if len(base) >= 4 else norm


def compute_canonical_hash(
    category: str,
    address: str,
    price: Optional[int],
    land_area_sqm: Optional[float],
    building_area_sqm: Optional[float],
) -> str:
    """
    複数ポータル（SUUMO・Yahoo!不動産・ニフティ不動産など）に掲載された同一物件を
    名寄せ（重複排除）するための正規化ハッシュを算出する。
    町丁目レベルの正規化住所 + 種別 + 面積（整数丸め） + 価格帯を組み合わせる。
    """
    town_addr = extract_town_level_address(address)
    area_val = land_area_sqm if land_area_sqm else (building_area_sqm or 0.0)
    area_key = f"{round(area_val, 1):.1f}"
    price_key = str((price or 0) // 100_000)  # 10万円単位
    raw_key = f"{category}|{town_addr}|{area_key}|{price_key}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24]

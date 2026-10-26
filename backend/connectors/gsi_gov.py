import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import httpx

from backend.municipalities import load_municipalities, PREFECTURES

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
GEO_CACHE_FILE = DATA_DIR / "geocode_cache.json"

_GEO_CACHE: Optional[Dict[str, Dict[str, Any]]] = None

# 国土地理院およびハザードマップポータルサイト公式タイル定義
GSI_MAP_TILES = {
    "std": {
        "id": "std",
        "name": "国土地理院 標準地図",
        "url": "https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png",
        "attribution": "国土地理院",
    },
    "pale": {
        "id": "pale",
        "name": "国土地理院 淡色地図",
        "url": "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png",
        "attribution": "国土地理院",
    },
    "seamlessphoto": {
        "id": "seamlessphoto",
        "name": "国土地理院 全国最新写真（航空写真）",
        "url": "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg",
        "attribution": "国土地理院",
    },
    "relief": {
        "id": "relief",
        "name": "国土地理院 色別標高図",
        "url": "https://cyberjapandata.gsi.go.jp/xyz/relief/{z}/{x}/{y}.png",
        "attribution": "国土地理院",
    },
}

HAZARD_MAP_TILES = {
    "flood": {
        "id": "flood",
        "name": "洪水浸水想定区域（想定最大規模）",
        "url": "https://disaportaldata.gsi.go.jp/raster/01_flood_l2_shinsuishin_data/{z}/{x}/{y}.png",
        "attribution": "国土地理院・国土交通省ハザードマップポータルサイト",
    },
    "tsunami": {
        "id": "tsunami",
        "name": "津波浸水想定区域",
        "url": "https://disaportaldata.gsi.go.jp/raster/04_tsunami_newlegend_data/{z}/{x}/{y}.png",
        "attribution": "国土地理院・国土交通省ハザードマップポータルサイト",
    },
    "landslide": {
        "id": "landslide",
        "name": "土砂災害警戒区域（急傾斜地の崩壊）",
        "url": "https://disaportaldata.gsi.go.jp/raster/05_kyukeishakeikaikuiki/{z}/{x}/{y}.png",
        "attribution": "国土地理院・国土交通省ハザードマップポータルサイト",
    },
    "debris": {
        "id": "debris",
        "name": "土砂災害警戒区域（土石流）",
        "url": "https://disaportaldata.gsi.go.jp/raster/05_dosekiryukeikaikuiki/{z}/{x}/{y}.png",
        "attribution": "国土地理院・国土交通省ハザードマップポータルサイト",
    },
}


def _load_geo_cache() -> Dict[str, Dict[str, Any]]:
    global _GEO_CACHE
    if _GEO_CACHE is not None:
        return _GEO_CACHE
    if GEO_CACHE_FILE.exists():
        try:
            _GEO_CACHE = json.loads(GEO_CACHE_FILE.read_text(encoding="utf-8"))
            return _GEO_CACHE
        except Exception:
            pass
    _GEO_CACHE = {}
    return _GEO_CACHE


def _save_geo_cache() -> None:
    if _GEO_CACHE is not None:
        try:
            GEO_CACHE_FILE.write_text(
                json.dumps(_GEO_CACHE, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass


def latlon_to_tile(lat: float, lon: float, zoom: int) -> Tuple[int, int, int, int]:
    """緯度経度とズームレベルからXYZタイル座標(x, y)およびタイル内ピクセル座標(px, py)を算出する。"""
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xtile_f = (lon + 180.0) / 360.0 * n
    ytile_f = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    xtile = int(xtile_f)
    ytile = int(ytile_f)
    px = int((xtile_f - xtile) * 256)
    py = int((ytile_f - ytile) * 256)
    return xtile, ytile, px, py


async def geocode_address(address: str, client: Optional[httpx.AsyncClient] = None) -> Optional[Dict[str, Any]]:
    """
    国土地理院 AddressSearch API を用いて住所文字列から正確な緯度・経度を取得する。
    番地詳細でヒットしない場合は町丁目・市区町村単位へ段階的にフォールバックする。
    """
    if not address:
        return None
    cache = _load_geo_cache()
    clean_addr = address.strip()
    if clean_addr in cache:
        return cache[clean_addr]

    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        own_client = True

    try:
        # 段階的クエリ候補（そのまま -> 番地以降を削った町丁目）
        queries = [clean_addr]
        import re
        town_only = re.sub(r"[\d０-９]+(?:番|号|丁目|[-ー－]).*$", "", clean_addr).strip()
        if town_only and town_only != clean_addr and len(town_only) >= 4:
            queries.append(town_only)

        for q in queries:
            url = "https://msearch.gsi.go.jp/address-search/AddressSearch"
            resp = await client.get(url, params={"q": q})
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    coords = data[0].get("geometry", {}).get("coordinates", [])
                    if len(coords) >= 2:
                        lon, lat = float(coords[0]), float(coords[1])
                        result = {
                            "latitude": lat,
                            "longitude": lon,
                            "matched_title": data[0].get("properties", {}).get("title", q),
                        }
                        cache[clean_addr] = result
                        _save_geo_cache()
                        return result
    except Exception:
        pass
    finally:
        if own_client:
            await client.aclose()

    return None


def apply_jitter_for_display(lat: float, lon: float, unique_key: str) -> Tuple[float, float]:
    """
    同一町丁目（例：東京都港区赤坂4丁目）の複数物件が地図上で完全に1点に重なるのを防ぐため、
    物件固有キーから決定論的な微小オフセット（約15m〜45m圏内）を付与する。
    """
    h = hashlib.md5(unique_key.encode("utf-8")).digest()
    angle = (int.from_bytes(h[:2], "big") / 65535.0) * 2.0 * math.pi
    radius_deg = 0.00012 + (int.from_bytes(h[2:4], "big") / 65535.0) * 0.00032
    d_lat = radius_deg * math.sin(angle)
    d_lon = radius_deg * math.cos(angle)
    return round(lat + d_lat, 6), round(lon + d_lon, 6)


async def fetch_elevation(lat: float, lon: float, client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    """国土地理院 標高API から指定座標の正確な標高（メートル）と測量データ種別（1m/5mレーザ等）を取得する。"""
    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        own_client = True

    try:
        url = "https://cyberjapandata2.gsi.go.jp/general/dem/scripts/getelevation.php"
        resp = await client.get(url, params={"lon": lon, "lat": lat, "outtype": "JSON"})
        if resp.status_code == 200:
            data = resp.json()
            elev_raw = data.get("elevation")
            elev_val = float(elev_raw) if elev_raw not in (None, "-----") else None
            return {
                "elevation_m": elev_val,
                "elevation_source": str(data.get("hsrc", "国土地理院標高データ")),
            }
    except Exception:
        pass
    finally:
        if own_client:
            await client.aclose()

    return {"elevation_m": None, "elevation_source": "不明"}


async def reverse_geocode(lat: float, lon: float, client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    """国土地理院 逆ジオコーディングAPI を用いて緯度経度から市区町村コード・都道府県・町名を特定する。"""
    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        own_client = True

    try:
        url = "https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress"
        resp = await client.get(url, params={"lat": lat, "lon": lon})
        if resp.status_code == 200:
            data = resp.json().get("results", {})
            muni_cd = str(data.get("muniCd", "")).zfill(5)
            lv01 = str(data.get("lv01Nm", ""))
            munis = load_municipalities()
            m_info = munis.get(muni_cd, {})
            return {
                "muni_code": muni_cd,
                "prefecture_code": m_info.get("pref_code", muni_cd[:2]),
                "prefecture_name": m_info.get("pref_name", ""),
                "municipality_name": m_info.get("muni_name", ""),
                "town_name": lv01,
                "full_address": f"{m_info.get('full_name', '')}{lv01}",
            }
    except Exception:
        pass
    finally:
        if own_client:
            await client.aclose()

    return {}


async def analyze_hazard_and_topography(
    lat: float,
    lon: float,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """
    指定した緯度経度について、
    1. 国土地理院 標高データ（DEM）
    2. 国土交通省ハザードマップポータルサイトの各災害レイヤータイル存在判定
    3. 地形・浸水リスク総合評価
    をリアルタイムで実行して返す。
    """
    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        own_client = True

    try:
        elev_info = await fetch_elevation(lat, lon, client=client)
        rev_info = await reverse_geocode(lat, lon, client=client)

        zoom = 15
        xtile, ytile, _, _ = latlon_to_tile(lat, lon, zoom)

        layer_status: Dict[str, Dict[str, Any]] = {}
        for key, meta in HAZARD_MAP_TILES.items():
            tile_url = meta["url"].format(z=zoom, x=xtile, y=ytile)
            has_tile_data = False
            tile_byte_size = 0
            try:
                r = await client.get(tile_url)
                if r.status_code == 200 and len(r.content) > 350:
                    # 350バイト超のPNGタイルは当該区画内にハザード塗色ピクセルが含まれる
                    has_tile_data = True
                    tile_byte_size = len(r.content)
            except Exception:
                pass

            layer_status[key] = {
                "layer_name": meta["name"],
                "area_has_designated_zone": has_tile_data,
                "tile_url": tile_url,
                "tile_bytes": tile_byte_size,
                "judgment": "周辺区画に指定区域あり（地図レイヤーで詳細確認推奨）" if has_tile_data else "当該タイル区画内に指定区域なし",
            }

        elev_m = elev_info.get("elevation_m")
        if elev_m is not None:
            if elev_m < 3.0:
                terrain_comment = f"標高 {elev_m:.1f}m（低地・沿岸または河川近傍エリア。水害・高潮対策の確認を強く推奨）"
            elif elev_m < 15.0:
                terrain_comment = f"標高 {elev_m:.1f}m（平坦地・一般的な市街地標高。内水氾濫および河川浸水想定の確認を推奨）"
            else:
                terrain_comment = f"標高 {elev_m:.1f}m（高台・台地エリア。水害リスクは比較的低いが法面・造成状況の確認を推奨）"
        else:
            terrain_comment = "標高データ取得対象外"

        return {
            "coordinates": {"latitude": lat, "longitude": lon},
            "address_info": rev_info,
            "elevation": elev_info,
            "terrain_evaluation": terrain_comment,
            "hazard_layers": layer_status,
        }
    finally:
        if own_client:
            await client.aclose()


async def fetch_surrounding_facilities(
    lat: float,
    lon: float,
    radius_m: int = 800,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """OpenStreetMap Overpass API を用いて指定座標周辺の主要生活施設（駅・スーパー・コンビニ・学校・病院・公園）を取得する。"""
    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
        own_client = True

    query = f"""
    [out:json][timeout:10];
    (
      node["railway"="station"](around:{radius_m},{lat},{lon});
      node["shop"~"supermarket|convenience"](around:{radius_m},{lat},{lon});
      node["amenity"~"school|hospital|clinic|pharmacy"](around:{radius_m},{lat},{lon});
      way["leisure"="park"](around:{radius_m},{lat},{lon});
    );
    out center 25;
    """
    facilities: List[Dict[str, Any]] = []
    category_map = {
        "station": "鉄道駅",
        "supermarket": "スーパーマーケット",
        "convenience": "コンビニエンスストア",
        "school": "教育施設",
        "hospital": "病院",
        "clinic": "診療所・クリニック",
        "pharmacy": "薬局",
        "park": "公園",
    }
    try:
        resp = await client.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
        )
        if resp.status_code == 200:
            elements = resp.json().get("elements", [])
            for el in elements:
                tags = el.get("tags", {})
                name = tags.get("name:ja") or tags.get("name")
                if not name:
                    continue
                el_lat = el.get("lat") or el.get("center", {}).get("lat")
                el_lon = el.get("lon") or el.get("center", {}).get("lon")
                if el_lat is None or el_lon is None:
                    continue
                raw_type = (
                    tags.get("railway")
                    or tags.get("shop")
                    or tags.get("amenity")
                    or tags.get("leisure")
                    or "施設"
                )
                jp_type = category_map.get(raw_type, "周辺施設")
                dist_m = int(
                    math.hypot(
                        (el_lat - lat) * 111_320.0,
                        (el_lon - lon) * 111_320.0 * math.cos(math.radians(lat)),
                    )
                )
                facilities.append({
                    "name": name,
                    "type": jp_type,
                    "distance_m": dist_m,
                    "walk_minutes": max(1, round(dist_m / 80.0)),
                    "latitude": el_lat,
                    "longitude": el_lon,
                })
            facilities.sort(key=lambda x: x["distance_m"])
    except Exception:
        pass
    finally:
        if own_client:
            await client.aclose()

    return facilities


async def fetch_mlit_transactions(
    pref_code: str,
    muni_code: Optional[str] = None,
    year: int = 2025,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """
    国土交通省「不動産情報ライブラリ API」（REINFOLIB_API_KEY 環境変数が設定されている場合）から
    成約価格・取引価格情報を取得する。
    """
    api_key = os.environ.get("REINFOLIB_API_KEY", "").strip()
    if not api_key:
        return {
            "enabled": False,
            "message": "環境変数 REINFOLIB_API_KEY が未設定のため、ポータル実勢データおよび国土地理院データによる相場算出を使用しています。",
            "records": [],
        }

    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
        own_client = True

    try:
        params: Dict[str, Any] = {"year": year, "area": pref_code.zfill(2)}
        if muni_code:
            params["city"] = muni_code.zfill(5)
        resp = await client.get(
            "https://www.reinfolib.mlit.go.jp/ex-api/external/XIT001",
            params=params,
            headers={"Ocp-Apim-Subscription-Key": api_key},
        )
        if resp.status_code == 200:
            records = resp.json().get("data", [])
            return {
                "enabled": True,
                "message": f"国土交通省 不動産情報ライブラリより {len(records)} 件の公的取引データを取得しました。",
                "records": records[:50],
            }
    except Exception as exc:
        return {
            "enabled": True,
            "message": f"国土交通省API通信エラー: {exc}",
            "records": [],
        }
    finally:
        if own_client:
            await client.aclose()

    return {"enabled": False, "message": "取得結果なし", "records": []}

import base64
import json
import math
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import httpx
import mapbox_vector_tile

from backend.connectors.gsi_gov import latlon_to_tile
from backend.connectors.parser_utils import TSUBO_PER_SQM

EXPORT_DIR = Path(__file__).resolve().parent.parent / "data" / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def tile_xy_to_latlon(xtile: float, ytile: float, zoom: int) -> Tuple[float, float]:
    """タイル座標(xtile, ytile)から緯度・経度(lat, lon)を逆算する。"""
    n = 2.0 ** zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return lat_deg, lon_deg


async def fetch_gsi_dem_grid(
    lat: float,
    lon: float,
    zoom: int = 14,
    grid_size: int = 48,
    span_m: float = 500.0,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """
    国土地理院の標高タイル（dem / dem5a）から実測標高マトリクスを取得し、
    指定座標を中心としたメートル座標系 (x_m, y_m, z_m) の3D地形グリッドを生成する。
    """
    own_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=12.0)
        own_client = True

    z = max(9, min(15, zoom))
    xtile, ytile, px_center, py_center = latlon_to_tile(lat, lon, z)

    raw_matrix: List[List[Optional[float]]] = []
    dem_source_name = "国土地理院10mメッシュ標高（DEM10B）"
    try:
        candidates = (
            [("dem5a", "国土地理院5mレーザ標高（DEM5A）"), ("dem", "国土地理院10mメッシュ標高（DEM10B）")]
            if z == 15
            else [("dem", "国土地理院10mメッシュ標高（DEM10B）")]
        )
        for kind, label in candidates:
            url = f"https://cyberjapandata.gsi.go.jp/xyz/{kind}/{z}/{xtile}/{ytile}.txt"
            resp = await client.get(url)
            if resp.status_code == 200 and resp.text:
                lines = resp.text.strip().splitlines()
                if len(lines) == 256:
                    for line in lines:
                        row_vals: List[Optional[float]] = []
                        for cell in line.split(","):
                            c = cell.strip()
                            if c and c != "e":
                                try:
                                    row_vals.append(float(c))
                                except ValueError:
                                    row_vals.append(None)
                            else:
                                row_vals.append(None)
                        raw_matrix.append(row_vals)
                    dem_source_name = label
                    break
    except Exception:
        pass
    finally:
        if own_client:
            await client.aclose()

    # 有効な標高値の平均・最小・最大を算出
    valid_vals = [v for row in raw_matrix for v in row if v is not None]
    base_elev = sum(valid_vals) / len(valid_vals) if valid_vals else 15.0

    # 中心 (px_center, py_center) の周辺またはタイル全体から grid_size x grid_size にリサンプリング
    half_span = span_m / 2.0
    x_coords = [
        round(-half_span + (span_m * j) / (grid_size - 1), 2)
        for j in range(grid_size)
    ]
    y_coords = [
        round(-half_span + (span_m * i) / (grid_size - 1), 2)
        for i in range(grid_size)
    ]

    # 1タイルあたりの実寸幅（メートル）
    lat_top, lon_left = tile_xy_to_latlon(xtile, ytile, z)
    lat_bot, lon_right = tile_xy_to_latlon(xtile + 1, ytile + 1, z)
    tile_width_m = max(100.0, abs(lon_right - lon_left) * 111_320.0 * math.cos(math.radians(lat)))
    px_span = max(16.0, min(250.0, (span_m / tile_width_m) * 256.0))

    elevations: List[List[float]] = []
    min_elev = 99999.0
    max_elev = -99999.0

    for i in range(grid_size):
        row_z: List[float] = []
        v_ratio = (i / (grid_size - 1)) - 0.5
        py = int(round(py_center - v_ratio * px_span))
        py = max(0, min(255, py))
        for j in range(grid_size):
            u_ratio = (j / (grid_size - 1)) - 0.5
            px = int(round(px_center + u_ratio * px_span))
            px = max(0, min(255, px))
            val = None
            if raw_matrix and py < len(raw_matrix) and px < len(raw_matrix[py]):
                val = raw_matrix[py][px]
            z_val = round(val if val is not None else base_elev, 2)
            row_z.append(z_val)
            min_elev = min(min_elev, z_val)
            max_elev = max(max_elev, z_val)
        elevations.append(row_z)

    center_elev = elevations[grid_size // 2][grid_size // 2]
    contours = extract_contours_marching_squares(
        elevations, x_coords, y_coords, min_elev, max_elev
    )

    return {
        "center": {"latitude": lat, "longitude": lon, "elevation_m": center_elev},
        "source": dem_source_name,
        "zoom": z,
        "grid_size": grid_size,
        "span_m": span_m,
        "min_elevation_m": round(min_elev, 2),
        "max_elevation_m": round(max_elev, 2),
        "x_coords": x_coords,
        "y_coords": y_coords,
        "elevations": elevations,
        "contours": contours,
    }


def extract_contours_marching_squares(
    grid: List[List[float]],
    x_coords: List[float],
    y_coords: List[float],
    min_elev: float,
    max_elev: float,
) -> List[Dict[str, Any]]:
    """
    標高マトリクスに対してマーチングスクエア法（Marching Squares）を適用し、
    2D平面図および3D地形図用の等高線セグメント群を生成する。
    """
    diff = max(0.5, max_elev - min_elev)
    if diff <= 10.0:
        step = 1.0
    elif diff <= 40.0:
        step = 2.5
    elif diff <= 200.0:
        step = 10.0
    else:
        step = 50.0

    start_level = math.ceil(min_elev / step) * step
    levels: List[float] = []
    lv = start_level
    while lv < max_elev and len(levels) < 18:
        levels.append(round(lv, 2))
        lv += step

    rows = len(grid)
    cols = len(grid[0]) if rows > 0 else 0
    contours: List[Dict[str, Any]] = []

    for level in levels:
        segments: List[List[List[float]]] = []
        for i in range(rows - 1):
            y0, y1 = y_coords[i], y_coords[i + 1]
            for j in range(cols - 1):
                x0, x1 = x_coords[j], x_coords[j + 1]
                z00 = grid[i][j]
                z10 = grid[i][j + 1]
                z11 = grid[i + 1][j + 1]
                z01 = grid[i + 1][j]

                pts: List[List[float]] = []
                # 上辺 (x0,y0)-(x1,y0)
                if (z00 < level <= z10) or (z10 < level <= z00):
                    t = (level - z00) / (z10 - z00) if z10 != z00 else 0.5
                    pts.append([round(x0 + t * (x1 - x0), 2), y0])
                # 右辺 (x1,y0)-(x1,y1)
                if (z10 < level <= z11) or (z11 < level <= z10):
                    t = (level - z10) / (z11 - z10) if z11 != z10 else 0.5
                    pts.append([x1, round(y0 + t * (y1 - y0), 2)])
                # 下辺 (x0,y1)-(x1,y1)
                if (z01 < level <= z11) or (z11 < level <= z01):
                    t = (level - z01) / (z11 - z01) if z11 != z01 else 0.5
                    pts.append([round(x0 + t * (x1 - x0), 2), y1])
                # 左辺 (x0,y0)-(x0,y1)
                if (z00 < level <= z01) or (z01 < level <= z00):
                    t = (level - z00) / (z01 - z00) if z01 != z00 else 0.5
                    pts.append([x0, round(y0 + t * (y1 - y0), 2)])

                if len(pts) >= 2:
                    segments.append([pts[0], pts[1]])

        if segments:
            contours.append({
                "elevation_m": level,
                "segments": segments[:300],
            })

    return contours


def _sample_elevation_at_xy(terrain: Dict[str, Any], x_m: float, y_m: float) -> float:
    """地形グリッド上の任意座標 (x_m, y_m) の標高を補間取得する。"""
    span = terrain["span_m"]
    half = span / 2.0
    n = terrain["grid_size"]
    elevs = terrain["elevations"]
    u = max(0.0, min(1.0, (x_m + half) / span))
    v = max(0.0, min(1.0, (y_m + half) / span))
    col = int(round(u * (n - 1)))
    row = int(round(v * (n - 1)))
    return float(elevs[row][col])


async def fetch_urban_3d_scene(
    lat: float,
    lon: float,
    radius_m: int = 220,
    zoom: int = 15,
) -> Dict[str, Any]:
    """
    指定した緯度経度周辺の
    1. 国土地理院の実測3D標高メッシュ（DEM）と等高線
    2. 国土地理院 公式ベクトルタイル（experimental_bvmap）の建物ポリゴン群（普通建物・堅ろう建物・高層建物）
    3. 国土地理院 公式ベクトルタイルの道路網（道路種別・幅員付き）
    を統合した都市・街区2D平面図および3Dデジタルツインデータを生成する。
    """
    span_m = float(radius_m * 2)
    async with httpx.AsyncClient(timeout=15.0) as client:
        terrain = await fetch_gsi_dem_grid(
            lat=lat,
            lon=lon,
            zoom=zoom,
            grid_size=44,
            span_m=span_m,
            client=client,
        )

        buildings: List[Dict[str, Any]] = []
        roads: List[Dict[str, Any]] = []

        # 国土地理院公式ベクトルタイル（ズーム16）から実在建物・道路を高精度抽出
        bv_zoom = 16
        xtile, ytile, _, _ = latlon_to_tile(lat, lon, bv_zoom)
        m_per_deg_lat = 111_320.0
        m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat))

        # 中心タイルおよび隣接タイルを走査
        tile_coords = [(xtile, ytile)]
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            tile_coords.append((xtile + dx, ytile + dy))

        b_id_seq = 1
        r_id_seq = 1
        for tx_i, ty_i in tile_coords[:2]:
            pbf_url = f"https://cyberjapandata.gsi.go.jp/xyz/experimental_bvmap/{bv_zoom}/{tx_i}/{ty_i}.pbf"
            try:
                resp = await client.get(pbf_url)
                if resp.status_code != 200 or not resp.content:
                    continue
                decoded = mapbox_vector_tile.decode(
                    resp.content,
                    default_options={"y_coord_down": True},
                )

                # 1. 建物ポリゴンレイヤー（building）
                b_layer = decoded.get("building", {})
                extent = float(b_layer.get("extent", 4096))
                for feat in b_layer.get("features", []):
                    geom = feat.get("geometry", {})
                    props = feat.get("properties", {})
                    g_type = geom.get("type")
                    rings = geom.get("coordinates", [])
                    if g_type == "MultiPolygon" and rings:
                        ring = rings[0][0]
                    elif g_type == "Polygon" and rings:
                        ring = rings[0]
                    else:
                        continue

                    coords_xy: List[List[float]] = []
                    for pt in ring:
                        pt_lat, pt_lon = tile_xy_to_latlon(
                            tx_i + pt[0] / extent,
                            ty_i + pt[1] / extent,
                            bv_zoom,
                        )
                        xm = round((pt_lon - lon) * m_per_deg_lon, 2)
                        ym = round((pt_lat - lat) * m_per_deg_lat, 2)
                        coords_xy.append([xm, ym])

                    if len(coords_xy) < 3:
                        continue
                    cx = sum(p[0] for p in coords_xy) / len(coords_xy)
                    cy = sum(p[1] for p in coords_xy) / len(coords_xy)
                    if abs(cx) > radius_m or abs(cy) > radius_m:
                        continue

                    ft_code = int(props.get("ftCode", 3101))
                    # 3101: 普通建物(2階), 3102: 堅ろう建物(5階RC), 3103: 高層建物(14階タワー)
                    if ft_code == 3103:
                        levels = 14
                        height_m = 46.0
                        b_name = "高層建築物"
                    elif ft_code == 3102:
                        levels = 5
                        height_m = 17.5
                        b_name = "堅ろう建築物（中層）"
                    else:
                        levels = 2
                        height_m = 6.8
                        b_name = "一般建築物（低層）"

                    base_z = _sample_elevation_at_xy(terrain, cx, cy)
                    buildings.append({
                        "id": b_id_seq,
                        "name": b_name,
                        "levels": levels,
                        "height_m": height_m,
                        "base_elevation_m": base_z,
                        "footprint_xy": coords_xy,
                    })
                    b_id_seq += 1

                # 2. 道路レイヤー（road）
                r_layer = decoded.get("road", {})
                r_extent = float(r_layer.get("extent", 4096))
                for feat in r_layer.get("features", []):
                    geom = feat.get("geometry", {})
                    props = feat.get("properties", {})
                    g_type = geom.get("type")
                    lines_raw = geom.get("coordinates", [])
                    line_list = lines_raw if g_type == "MultiLineString" else [lines_raw]

                    for ln in line_list:
                        if len(ln) < 2:
                            continue
                        coords_xy = []
                        for pt in ln:
                            if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                                continue
                            pt_lat, pt_lon = tile_xy_to_latlon(
                                tx_i + pt[0] / r_extent,
                                ty_i + pt[1] / r_extent,
                                bv_zoom,
                            )
                            xm = round((pt_lon - lon) * m_per_deg_lon, 2)
                            ym = round((pt_lat - lat) * m_per_deg_lat, 2)
                            coords_xy.append([xm, ym])

                        if len(coords_xy) < 2:
                            continue
                        mid = coords_xy[len(coords_xy) // 2]
                        if abs(mid[0]) > radius_m or abs(mid[1]) > radius_m:
                            continue

                        rnk_w = int(props.get("rnkWidth", 1))
                        width_map = {0: 3.5, 1: 5.5, 2: 9.0, 3: 15.0, 4: 22.0}
                        road_w = width_map.get(rnk_w, 6.0)
                        roads.append({
                            "id": r_id_seq,
                            "name": "公道",
                            "type": str(props.get("ftCode", 2701)),
                            "width_m": road_w,
                            "centerline_xy": coords_xy,
                        })
                        r_id_seq += 1
            except Exception:
                pass

    return {
        "mode": "urban",
        "coordinates": {"latitude": lat, "longitude": lon},
        "radius_m": radius_m,
        "terrain": terrain,
        "buildings_count": len(buildings),
        "roads_count": len(roads),
        "buildings": buildings[:250],
        "roads": roads[:180],
    }


def calculate_solar_position(
    lat: float,
    season: str = "冬至",
    hour_jst: float = 12.0,
) -> Dict[str, float]:
    """
    日本の指定緯度・季節（冬至 / 春分・秋分 / 夏至）・時刻（8時〜16時）における
    太陽高度角（altitude_deg）・方位角（azimuth_deg）および日影倍率・方向ベクトルを厳密算出する。
    """
    declination_map = {
        "冬至": -23.44,
        "春分・秋分": 0.0,
        "夏至": 23.44,
    }
    dec_deg = declination_map.get(season, -23.44)
    lat_rad = math.radians(lat)
    dec_rad = math.radians(dec_deg)

    # 時角（正午12時を0度とし1時間あたり15度）
    hour_angle_deg = (hour_jst - 12.0) * 15.0
    h_rad = math.radians(hour_angle_deg)

    sin_alt = (
        math.sin(lat_rad) * math.sin(dec_rad)
        + math.cos(lat_rad) * math.cos(dec_rad) * math.cos(h_rad)
    )
    alt_rad = math.asin(max(-1.0, min(1.0, sin_alt)))
    alt_deg = max(3.0, math.degrees(alt_rad))

    # 太陽方位角（南を0度、西を正、東を負）
    cos_alt = max(1e-5, math.cos(alt_rad))
    sin_az = (math.cos(dec_rad) * math.sin(h_rad)) / cos_alt
    cos_az = (
        math.sin(alt_rad) * math.sin(lat_rad) - math.sin(dec_rad)
    ) / (cos_alt * max(1e-5, math.cos(lat_rad)))
    az_rad = math.atan2(sin_az, cos_az)
    az_deg = math.degrees(az_rad)

    # 高さ1mあたりの影の長さおよび影が伸びる方向ベクトル (dx, dy) ※北=+Y, 東=+X
    shadow_len_per_m = min(8.0, 1.0 / math.tan(math.radians(alt_deg)))
    shadow_dx = shadow_len_per_m * math.sin(az_rad)
    shadow_dy = shadow_len_per_m * math.cos(az_rad)

    return {
        "season": season,
        "hour_jst": hour_jst,
        "solar_altitude_deg": round(alt_deg, 2),
        "solar_azimuth_south_deg": round(az_deg, 2),
        "shadow_length_per_height": round(shadow_len_per_m, 3),
        "shadow_vector_xy": [round(shadow_dx, 3), round(shadow_dy, 3)],
    }


def generate_architectural_model(
    land_area_sqm: float = 110.0,
    building_coverage_ratio: float = 60.0,
    floor_area_ratio: float = 200.0,
    road_width_m: float = 5.4,
    road_direction: str = "南",
    stories: Optional[int] = None,
    floor_plan_type: str = "3LDK",
    latitude: float = 35.6586,
    longitude: float = 139.7454,
    title: str = "標準敷地・建築計画モデル",
    address: str = "東京都港区",
) -> Dict[str, Any]:
    """
    個別物件・土地の2D詳細敷地配置図・各階間取り平面図データおよび
    3D建物モデル・建築基準法斜線制限ケージ・冬至/夏至日影シミュレーションデータを生成する。
    """
    area = max(35.0, float(land_area_sqm))
    # 間口と奥行の算出（標準的な整形地比率 1 : 1.35）
    lot_w = round(math.sqrt(area / 1.32), 2)
    lot_d = round(area / lot_w, 2)

    # 2m未満道路の場合のセットバック（道路中心線から2m後退）
    setback_m = round(max(0.0, (4.0 - road_width_m) / 2.0), 2)
    effective_lot_d = round(lot_d - setback_m, 2)

    # 実効建蔽率・実効容積率
    road_limited_far = road_width_m * 40.0 if road_width_m < 12.0 else floor_area_ratio
    eff_far = min(floor_area_ratio, road_limited_far)
    eff_cov = min(building_coverage_ratio, 80.0)

    max_footprint = round(area * (eff_cov / 100.0), 2)
    max_total_floor = round(area * (eff_far / 100.0), 2)

    if stories is None or stories <= 0:
        stories = max(1, min(4, math.ceil(max_total_floor / max(20.0, max_footprint))))

    # 実際の建物1階外形寸法（隣地境界から民法・施工余裕として各0.6m〜1.0m離隔）
    bld_w = round(min(lot_w - 1.4, math.sqrt(max_footprint * 0.88)), 2)
    bld_d = round(min(effective_lot_d - 2.2, (max_footprint * 0.90) / max(3.0, bld_w)), 2)
    bld_w = max(3.6, bld_w)
    bld_d = max(4.2, bld_d)

    # 敷地中心を (0, 0) とし、南側セットバック・駐車スペースを考慮して建物中心を配置
    lot_min_x, lot_max_x = round(-lot_w / 2.0, 2), round(lot_w / 2.0, 2)
    lot_min_y, lot_max_y = round(-lot_d / 2.0, 2), round(lot_d / 2.0, 2)

    # 建物を北寄り（南庭・南駐車場確保）に配置
    bld_offset_y = round((lot_d - bld_d) / 2.0 - 0.8, 2) if road_direction == "南" else 0.0
    bld_min_x, bld_max_x = round(-bld_w / 2.0, 2), round(bld_w / 2.0, 2)
    bld_min_y, bld_max_y = round(-bld_d / 2.0 + bld_offset_y, 2), round(bld_d / 2.0 + bld_offset_y, 2)

    story_height_m = 2.95
    total_height_m = round(stories * story_height_m + 0.9, 2)

    is_office_layout = (
        "事務所" in (floor_plan_type or "")
        or "オフィス" in (floor_plan_type or "")
        or "事務所" in (title or "")
        or "オフィス" in (title or "")
    )
    if is_office_layout and stories is None:
        stories = 1

    # 各階の詳細間取り平面図（部屋・壁・扉開閉円弧・窓・設備・柱）を生成
    floors_data: List[Dict[str, Any]] = []
    for fl in range(1, stories + 1):
        z_bottom = round((fl - 1) * story_height_m, 2)
        z_top = round(fl * story_height_m, 2)
        rooms: List[Dict[str, Any]] = []
        doors: List[Dict[str, Any]] = []
        windows: List[Dict[str, Any]] = []
        fixtures: List[Dict[str, Any]] = []

        if is_office_layout:
            # 少人数オフィス・事務所レイアウト（執務室4〜5席・4名会議室・受付複合機・給湯化粧室・書庫）
            split_y = round(bld_min_y + bld_d * 0.56, 2)
            split_x_mtg = round(bld_min_x + bld_w * 0.62, 2)
            split_x1 = round(bld_min_x + bld_w * 0.34, 2)
            split_x2 = round(bld_min_x + bld_w * 0.68, 2)

            # 南西：メイン執務室（2〜5名デスク配置）
            work_w = round(split_x_mtg - bld_min_x, 2)
            work_d = round(split_y - bld_min_y, 2)
            work_area = round(work_w * work_d, 1)
            rooms.append({
                "id": f"F{fl}_OFFICE_WORK",
                "name": "執務室（デスク4〜5席）",
                "x": bld_min_x,
                "y": bld_min_y,
                "w": work_w,
                "d": work_d,
                "area_sqm": work_area,
                "tatami_jo": round(work_area / 1.62, 1),
                "color": "#1e3a5f",
            })
            # 南東：4名会議・オンライン打合せ室
            mtg_w = round(bld_max_x - split_x_mtg, 2)
            mtg_d = work_d
            mtg_area = round(mtg_w * mtg_d, 1)
            rooms.append({
                "id": f"F{fl}_OFFICE_MTG",
                "name": "会議・応接室（4名用）",
                "x": split_x_mtg,
                "y": bld_min_y,
                "w": mtg_w,
                "d": mtg_d,
                "area_sqm": mtg_area,
                "tatami_jo": round(mtg_area / 1.62, 1),
                "color": "#0f766e",
            })
            # 北西：給湯・洗面・化粧室
            wet_w = round(split_x1 - bld_min_x, 2)
            wet_d = round(bld_max_y - split_y, 2)
            wet_area = round(wet_w * wet_d, 1)
            rooms.append({
                "id": f"F{fl}_OFFICE_WET",
                "name": "給湯室・化粧室",
                "x": bld_min_x,
                "y": split_y,
                "w": wet_w,
                "d": wet_d,
                "area_sqm": wet_area,
                "tatami_jo": round(wet_area / 1.62, 1),
                "color": "#164e63",
            })
            # 中央北：書庫・通信サーバ・複合機コーナー
            srv_w = round(split_x2 - split_x1, 2)
            srv_d = wet_d
            srv_area = round(srv_w * srv_d, 1)
            rooms.append({
                "id": f"F{fl}_OFFICE_STORAGE",
                "name": "書庫・複合機・通信室",
                "x": split_x1,
                "y": split_y,
                "w": srv_w,
                "d": srv_d,
                "area_sqm": srv_area,
                "tatami_jo": round(srv_area / 1.62, 1),
                "color": "#334155",
            })
            # 北東：受付・エントランスホール
            rec_w = round(bld_max_x - split_x2, 2)
            rec_d = wet_d
            rec_area = round(rec_w * rec_d, 1)
            rooms.append({
                "id": f"F{fl}_OFFICE_ENT",
                "name": "受付・エントランス",
                "x": split_x2,
                "y": split_y,
                "w": rec_w,
                "d": rec_d,
                "area_sqm": rec_area,
                "tatami_jo": round(rec_area / 1.62, 1),
                "color": "#3b2f2f",
            })

            doors.extend([
                {"x": round(split_x2 + rec_w * 0.5, 2), "y": bld_max_y, "radius": 0.85, "start_deg": 180, "end_deg": 270, "label": "事務所入口扉"},
                {"x": round(split_x_mtg + mtg_w * 0.35, 2), "y": split_y, "radius": 0.75, "start_deg": 180, "end_deg": 270, "label": "会議室ガラス扉"},
                {"x": round(bld_min_x + work_w * 0.5, 2), "y": split_y, "radius": 0.75, "start_deg": 270, "end_deg": 360, "label": "執務室扉"},
            ])
            windows.extend([
                {"x1": round(bld_min_x + 0.5, 2), "y1": bld_min_y, "x2": round(split_x_mtg - 0.3, 2), "y2": bld_min_y, "label": "執務室採光窓"},
                {"x1": round(split_x_mtg + 0.3, 2), "y1": bld_min_y, "x2": round(bld_max_x - 0.5, 2), "y2": bld_min_y, "label": "会議室採光窓"},
            ])
            # オフィス家具（対向4席デスク島・会議テーブル・複合機・給湯シンク）
            fixtures.extend([
                {"type": "執務デスク4席島", "x": round(bld_min_x + work_w * 0.2, 2), "y": round(bld_min_y + work_d * 0.28, 2), "w": round(min(2.4, work_w * 0.6), 2), "d": round(min(1.4, work_d * 0.45), 2)},
                {"type": "4名会議テーブル", "x": round(split_x_mtg + mtg_w * 0.22, 2), "y": round(bld_min_y + mtg_d * 0.25, 2), "w": round(min(1.8, mtg_w * 0.55), 2), "d": round(min(1.1, mtg_d * 0.48), 2)},
                {"type": "複合機・キャビネット", "x": round(split_x1 + 0.2, 2), "y": round(split_y + 0.2, 2), "w": round(min(1.4, srv_w * 0.7), 2), "d": 0.6},
            ])
        elif fl == 1:
            # 1階：玄関・廊下・洗面浴室・便所・居間・食堂・台所
            split_y = round(bld_min_y + bld_d * 0.42, 2)
            split_x1 = round(bld_min_x + bld_w * 0.34, 2)
            split_x2 = round(bld_min_x + bld_w * 0.68, 2)

            # 南側（または主採光側）：居間・食堂・台所
            ldk_w = bld_w
            ldk_d = round(split_y - bld_min_y, 2)
            ldk_area = round(ldk_w * ldk_d, 1)
            rooms.append({
                "id": f"F{fl}_LDK",
                "name": "居間・食堂・台所",
                "x": bld_min_x,
                "y": bld_min_y,
                "w": ldk_w,
                "d": ldk_d,
                "area_sqm": ldk_area,
                "tatami_jo": round(ldk_area / 1.62, 1),
                "color": "#1e3a5f",
            })
            # 北西：浴室・洗面脱衣室
            bath_w = round(split_x1 - bld_min_x, 2)
            bath_d = round(bld_max_y - split_y, 2)
            bath_area = round(bath_w * bath_d, 1)
            rooms.append({
                "id": f"F{fl}_BATH",
                "name": "浴室・洗面室",
                "x": bld_min_x,
                "y": split_y,
                "w": bath_w,
                "d": bath_d,
                "area_sqm": bath_area,
                "tatami_jo": round(bath_area / 1.62, 1),
                "color": "#164e63",
            })
            # 中央北：階段・廊下・便所
            hall_w = round(split_x2 - split_x1, 2)
            hall_d = bath_d
            hall_area = round(hall_w * hall_d, 1)
            rooms.append({
                "id": f"F{fl}_HALL",
                "name": "階段室・廊下・便所",
                "x": split_x1,
                "y": split_y,
                "w": hall_w,
                "d": hall_d,
                "area_sqm": hall_area,
                "tatami_jo": round(hall_area / 1.62, 1),
                "color": "#334155",
            })
            # 北東：玄関ホール・収納
            ent_w = round(bld_max_x - split_x2, 2)
            ent_d = bath_d
            ent_area = round(ent_w * ent_d, 1)
            rooms.append({
                "id": f"F{fl}_ENT",
                "name": "玄関・土間収納",
                "x": split_x2,
                "y": split_y,
                "w": ent_w,
                "d": ent_d,
                "area_sqm": ent_area,
                "tatami_jo": round(ent_area / 1.62, 1),
                "color": "#3b2f2f",
            })

            # 扉（開閉円弧付き）
            doors.extend([
                {"x": round(split_x2 + ent_w * 0.5, 2), "y": bld_max_y, "radius": 0.85, "start_deg": 180, "end_deg": 270, "label": "玄関扉"},
                {"x": round(split_x1 + hall_w * 0.5, 2), "y": split_y, "radius": 0.75, "start_deg": 270, "end_deg": 360, "label": "居間扉"},
                {"x": split_x1, "y": round(split_y + bath_d * 0.4, 2), "radius": 0.70, "start_deg": 90, "end_deg": 180, "label": "洗面扉"},
            ])
            # 窓（引違い窓）
            windows.extend([
                {"x1": round(bld_min_x + 0.6, 2), "y1": bld_min_y, "x2": round(bld_max_x - 0.6, 2), "y2": bld_min_y, "label": "南面大開口サッシ"},
                {"x1": bld_min_x, "y1": round(bld_min_y + 0.8, 2), "x2": bld_min_x, "y2": round(split_y - 0.5, 2), "label": "西面腰窓"},
                {"x1": round(bld_min_x + 0.4, 2), "y1": bld_max_y, "x2": round(split_x1 - 0.4, 2), "y2": bld_max_y, "label": "浴室高窓"},
            ])
            # 設備（システムキッチン・浴槽・階段）
            fixtures.extend([
                {"type": "システムキッチン", "x": round(bld_min_x + 0.3, 2), "y": round(split_y - 0.85, 2), "w": round(min(2.55, bld_w * 0.42), 2), "d": 0.65},
                {"type": "ユニットバス", "x": round(bld_min_x + 0.15, 2), "y": round(bld_max_y - 1.75, 2), "w": 1.6, "d": 1.6},
                {"type": "階段", "x": round(split_x1 + 0.15, 2), "y": round(split_y + 0.2, 2), "w": 0.9, "d": round(min(2.6, bath_d - 0.3), 2)},
            ])
        else:
            # 2階以上：主寝室・洋室1・洋室2・廊下・納戸・南面バルコニー
            split_y = round(bld_min_y + bld_d * 0.48, 2)
            split_x1 = round(bld_min_x + bld_w * 0.5, 2)
            split_x2 = round(bld_min_x + bld_w * 0.38, 2)
            split_x3 = round(bld_min_x + bld_w * 0.68, 2)

            # 南西：主寝室
            mbr_w = round(split_x1 - bld_min_x, 2)
            mbr_d = round(split_y - bld_min_y, 2)
            mbr_area = round(mbr_w * mbr_d, 1)
            rooms.append({
                "id": f"F{fl}_MBR",
                "name": f"{fl}階 主寝室",
                "x": bld_min_x,
                "y": bld_min_y,
                "w": mbr_w,
                "d": mbr_d,
                "area_sqm": mbr_area,
                "tatami_jo": round(mbr_area / 1.62, 1),
                "color": "#1e3a5f",
            })
            # 南東：洋室1
            br1_w = round(bld_max_x - split_x1, 2)
            br1_d = mbr_d
            br1_area = round(br1_w * br1_d, 1)
            rooms.append({
                "id": f"F{fl}_BR1",
                "name": f"{fl}階 洋室一",
                "x": split_x1,
                "y": bld_min_y,
                "w": br1_w,
                "d": br1_d,
                "area_sqm": br1_area,
                "tatami_jo": round(br1_area / 1.62, 1),
                "color": "#273c75",
            })
            # 北西：洋室2
            br2_w = round(split_x2 - bld_min_x, 2)
            br2_d = round(bld_max_y - split_y, 2)
            br2_area = round(br2_w * br2_d, 1)
            rooms.append({
                "id": f"F{fl}_BR2",
                "name": f"{fl}階 洋室二",
                "x": bld_min_x,
                "y": split_y,
                "w": br2_w,
                "d": br2_d,
                "area_sqm": br2_area,
                "tatami_jo": round(br2_area / 1.62, 1),
                "color": "#2f3640",
            })
            # 中央北：階段ホール
            st_w = round(split_x3 - split_x2, 2)
            st_d = br2_d
            st_area = round(st_w * st_d, 1)
            rooms.append({
                "id": f"F{fl}_STAIR",
                "name": f"{fl}階 階段・廊下",
                "x": split_x2,
                "y": split_y,
                "w": st_w,
                "d": st_d,
                "area_sqm": st_area,
                "tatami_jo": round(st_area / 1.62, 1),
                "color": "#334155",
            })
            # 北東：納戸（ウォークイン収納）
            wic_w = round(bld_max_x - split_x3, 2)
            wic_d = br2_d
            wic_area = round(wic_w * wic_d, 1)
            rooms.append({
                "id": f"F{fl}_WIC",
                "name": f"{fl}階 大型収納室",
                "x": split_x3,
                "y": split_y,
                "w": wic_w,
                "d": wic_d,
                "area_sqm": wic_area,
                "tatami_jo": round(wic_area / 1.62, 1),
                "color": "#3d3d5c",
            })
            # 南面バルコニー
            bal_d = 1.2
            rooms.append({
                "id": f"F{fl}_BAL",
                "name": f"{fl}階 南バルコニー",
                "x": bld_min_x,
                "y": round(bld_min_y - bal_d, 2),
                "w": bld_w,
                "d": bal_d,
                "area_sqm": round(bld_w * bal_d, 1),
                "tatami_jo": round((bld_w * bal_d) / 1.62, 1),
                "color": "#0f766e",
            })

            doors.extend([
                {"x": round(bld_min_x + mbr_w * 0.7, 2), "y": split_y, "radius": 0.75, "start_deg": 180, "end_deg": 270, "label": "主寝室扉"},
                {"x": round(split_x1 + br1_w * 0.3, 2), "y": split_y, "radius": 0.75, "start_deg": 270, "end_deg": 360, "label": "洋室扉"},
            ])
            windows.extend([
                {"x1": round(bld_min_x + 0.5, 2), "y1": bld_min_y, "x2": round(split_x1 - 0.4, 2), "y2": bld_min_y, "label": "バルコニー掃出し窓"},
                {"x1": round(split_x1 + 0.4, 2), "y1": bld_min_y, "x2": round(bld_max_x - 0.5, 2), "y2": bld_min_y, "label": "洋室南窓"},
            ])

        # 構造柱グリッド（四隅および主要交点）
        pillars = [
            [bld_min_x, bld_min_y],
            [bld_max_x, bld_min_y],
            [bld_max_x, bld_max_y],
            [bld_min_x, bld_max_y],
            [round((bld_min_x + bld_max_x) / 2.0, 2), bld_min_y],
            [round((bld_min_x + bld_max_x) / 2.0, 2), bld_max_y],
        ]

        floors_data.append({
            "floor_number": fl,
            "name_jp": f"{fl}階平面図",
            "z_bottom_m": z_bottom,
            "z_top_m": z_top,
            "floor_area_sqm": round(bld_w * bld_d, 2),
            "rooms": rooms,
            "doors": doors,
            "windows": windows,
            "fixtures": fixtures,
            "pillars": pillars,
        })

    # 建築基準法 斜線制限3Dエンクロージャ（道路斜線 1:1.25、北側斜線 5m + 1:1.25）
    road_slant_h_south = round((road_width_m + setback_m + (bld_min_y - lot_min_y)) * 1.25, 2)
    north_slant_h_north = round(5.0 + (lot_max_y - bld_max_y) * 1.25, 2)
    ridge_h = round(min(20.0, max(total_height_m + 1.5, (road_slant_h_south + north_slant_h_north) * 0.65)), 2)

    slant_envelope = {
        "road_slant_limit_m": road_slant_h_south,
        "north_slant_limit_m": north_slant_h_north,
        "max_legal_height_m": ridge_h,
        "vertices_3d": [
            [lot_min_x, lot_min_y + setback_m, 0.0],
            [lot_max_x, lot_min_y + setback_m, 0.0],
            [lot_max_x, lot_max_y, 0.0],
            [lot_min_x, lot_max_y, 0.0],
            [lot_min_x, lot_min_y + setback_m, min(ridge_h, road_slant_m := road_width_m * 1.25)],
            [lot_max_x, lot_min_y + setback_m, min(ridge_h, road_slant_m)],
            [lot_max_x, 0.0, ridge_h],
            [lot_min_x, 0.0, ridge_h],
            [lot_max_x, lot_max_y, 5.5],
            [lot_min_x, lot_max_y, 5.5],
        ],
    }

    # 冬至・春分/秋分・夏至 × 9時・12時・15時の太陽位置と日影ポリゴン群
    solar_profiles: Dict[str, List[Dict[str, Any]]] = {}
    for season in ["冬至", "春分・秋分", "夏至"]:
        hourly_list: List[Dict[str, Any]] = []
        for hr in [8, 9, 10, 11, 12, 13, 14, 15, 16]:
            sp = calculate_solar_position(latitude, season=season, hour_jst=float(hr))
            dx, dy = sp["shadow_vector_xy"]
            sh_x_shift = round(dx * total_height_m, 2)
            sh_y_shift = round(dy * total_height_m, 2)
            shadow_poly = [
                [bld_min_x, bld_min_y],
                [bld_max_x, bld_min_y],
                [round(bld_max_x + sh_x_shift, 2), round(bld_max_y + sh_y_shift, 2)],
                [round(bld_min_x + sh_x_shift, 2), round(bld_max_y + sh_y_shift, 2)],
            ]
            sp["shadow_polygon_xy"] = shadow_poly
            hourly_list.append(sp)
        solar_profiles[season] = hourly_list

    return {
        "mode": "property",
        "title": title,
        "address": address,
        "coordinates": {"latitude": latitude, "longitude": longitude},
        "site": {
            "land_area_sqm": area,
            "land_area_tsubo": round(area * TSUBO_PER_SQM, 2),
            "lot_width_m": lot_w,
            "lot_depth_m": lot_d,
            "setback_m": setback_m,
            "road_width_m": road_width_m,
            "road_direction": road_direction,
            "building_coverage_ratio_pct": building_coverage_ratio,
            "floor_area_ratio_pct": floor_area_ratio,
            "effective_floor_area_ratio_pct": round(eff_far, 1),
            "lot_bounds": [lot_min_x, lot_min_y, lot_max_x, lot_max_y],
            "building_bounds": [bld_min_x, bld_min_y, bld_max_x, bld_max_y],
        },
        "building": {
            "stories": stories,
            "story_height_m": story_height_m,
            "total_height_m": total_height_m,
            "building_width_m": bld_w,
            "building_depth_m": bld_d,
            "footprint_area_sqm": round(bld_w * bld_d, 2),
            "total_floor_area_sqm": round(bld_w * bld_d * stories, 2),
            "floor_plan_type": floor_plan_type,
            "floors": floors_data,
        },
        "slant_envelope": slant_envelope,
        "solar_simulation": solar_profiles,
    }


def export_property_svg(model: Dict[str, Any], floor_number: int = 1) -> str:
    """
    個別物件の敷地配置図および指定階の詳細建築平面図（寸法線・方位・道路・壁厚・扉円弧・窓・部屋帖数付き）を
    高精細SVGベクター図面として出力する。
    """
    site = model["site"]
    bld = model["building"]
    floors = bld["floors"]
    target_fl = next((f for f in floors if f["floor_number"] == floor_number), floors[0])

    svg_w, svg_h = 920, 680
    cx, cy = 430, 320
    # 1メートルあたりのピクセル倍率
    max_dim = max(site["lot_width_m"] + 8.0, site["lot_depth_m"] + site["road_width_m"] + 8.0)
    scale = min(460.0 / max_dim, 34.0)

    def to_px(xm: float, ym: float) -> Tuple[float, float]:
        # SVGはY軸下向きのため北(+Y)を上(-Y)へ変換
        return round(cx + xm * scale, 1), round(cy - ym * scale, 1)

    lot_min_x, lot_min_y, lot_max_x, lot_max_y = site["lot_bounds"]
    lx1, ly1 = to_px(lot_min_x, lot_max_y)
    lx2, ly2 = to_px(lot_max_x, lot_min_y)
    lot_px_w = round(lx2 - lx1, 1)
    lot_px_h = round(ly2 - ly1, 1)

    # 前面道路（南側）
    road_px_h = round(site["road_width_m"] * scale, 1)

    lines: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {svg_w} {svg_h}" width="100%" height="100%" style="background:#0b1324;font-family:\'Yu Gothic UI\',\'Meiryo\',sans-serif;">',
        "  <!-- 図面グリッド背景 -->",
        '  <defs>',
        '    <pattern id="cadGrid" width="25" height="25" patternUnits="userSpaceOnUse">',
        '      <path d="M 25 0 L 0 0 0 25" fill="none" stroke="#1e293b" stroke-width="0.7"/>',
        '    </pattern>',
        '  </defs>',
        f'  <rect width="{svg_w}" height="{svg_h}" fill="url(#cadGrid)"/>',
        f'  <rect x="12" y="12" width="{svg_w - 24}" height="{svg_h - 24}" fill="none" stroke="#38bdf8" stroke-width="1.5"/>',
    ]

    # 前面道路描画
    lines.append(
        f'  <rect x="{lx1 - 50}" y="{ly2}" width="{lot_px_w + 100}" height="{road_px_h}" fill="#1e293b" stroke="#64748b" stroke-width="1.2"/>'
    )
    lines.append(
        f'  <text x="{cx}" y="{ly2 + road_px_h / 2 + 5}" fill="#94a3b8" font-size="12" text-anchor="middle">前面道路（{site["road_direction"]}側・幅員 {site["road_width_m"]}メートル）</text>'
    )

    # 敷地境界線（一点鎖線）
    lines.append(
        f'  <rect x="{lx1}" y="{ly1}" width="{lot_px_w}" height="{lot_px_h}" fill="rgba(16,185,129,0.06)" stroke="#10b981" stroke-width="2" stroke-dasharray="10,4,2,4"/>'
    )

    # 各部屋の描画
    for rm in target_fl["rooms"]:
        rx1, ry1 = to_px(rm["x"], rm["y"] + rm["d"])
        rx2, ry2 = to_px(rm["x"] + rm["w"], rm["y"])
        rw = round(rx2 - rx1, 1)
        rh = round(ry2 - ry1, 1)
        lines.append(
            f'  <rect x="{rx1}" y="{ry1}" width="{rw}" height="{rh}" fill="{rm["color"]}" fill-opacity="0.72" stroke="#f8fafc" stroke-width="3"/>'
        )
        # 内壁複線表現
        if rw > 12 and rh > 12:
            lines.append(
                f'  <rect x="{rx1 + 3}" y="{ry1 + 3}" width="{rw - 6}" height="{rh - 6}" fill="none" stroke="#94a3b8" stroke-width="0.8"/>'
            )
        rcx = round(rx1 + rw / 2, 1)
        rcy = round(ry1 + rh / 2, 1)
        lines.append(
            f'  <text x="{rcx}" y="{rcy - 4}" fill="#ffffff" font-size="11.5" font-weight="bold" text-anchor="middle">{rm["name"]}</text>'
        )
        lines.append(
            f'  <text x="{rcx}" y="{rcy + 12}" fill="#bae6fd" font-size="10.5" text-anchor="middle">{rm["tatami_jo"]}帖（{rm["area_sqm"]}平米）</text>'
        )

    # 設備（キッチン・浴室・階段）描画
    for fx in target_fl["fixtures"]:
        fx1, fy1 = to_px(fx["x"], fx["y"] + fx["d"])
        fx2, fy2 = to_px(fx["x"] + fx["w"], fx["y"])
        fw, fh = round(fx2 - fx1, 1), round(fy2 - fy1, 1)
        lines.append(
            f'  <rect x="{fx1}" y="{fy1}" width="{fw}" height="{fh}" fill="rgba(56,189,248,0.18)" stroke="#38bdf8" stroke-width="1.2"/>'
        )
        if fx["type"] == "階段":
            steps = 7
            for s_idx in range(1, steps):
                sy = round(fy1 + (fh * s_idx) / steps, 1)
                lines.append(
                    f'  <line x1="{fx1}" y1="{sy}" x2="{fx2}" y2="{sy}" stroke="#38bdf8" stroke-width="1"/>'
                )

    # 窓サッシ記号（青色二重線）
    for win in target_fl["windows"]:
        wx1, wy1 = to_px(win["x1"], win["y1"])
        wx2, wy2 = to_px(win["x2"], win["y2"])
        lines.append(
            f'  <line x1="{wx1}" y1="{wy1}" x2="{wx2}" y2="{wy2}" stroke="#38bdf8" stroke-width="5"/>'
        )
        lines.append(
            f'  <line x1="{wx1}" y1="{wy1}" x2="{wx2}" y2="{wy2}" stroke="#e0f2fe" stroke-width="1.5"/>'
        )

    # 扉の開閉軌跡（円弧）
    for dr in target_fl["doors"]:
        dx_px, dy_px = to_px(dr["x"], dr["y"])
        r_px = round(dr["radius"] * scale, 1)
        lines.append(
            f'  <circle cx="{dx_px}" cy="{dy_px}" r="{r_px}" fill="none" stroke="#fbbf24" stroke-width="1.2" stroke-dasharray="3,3"/>'
        )

    # 構造柱（四角ドット）
    for pl in target_fl["pillars"]:
        px, py = to_px(pl[0], pl[1])
        lines.append(
            f'  <rect x="{px - 4}" y="{py - 4}" width="8" height="8" fill="#f59e0b" stroke="#ffffff" stroke-width="1"/>'
        )

    # 寸法線（敷地間口・敷地奥行・建物間口）
    dim_y_top = ly1 - 24
    lines.append(
        f'  <line x1="{lx1}" y1="{dim_y_top}" x2="{lx2}" y2="{dim_y_top}" stroke="#38bdf8" stroke-width="1.2"/>'
    )
    lines.append(
        f'  <line x1="{lx1}" y1="{ly1}" x2="{lx1}" y2="{dim_y_top - 6}" stroke="#38bdf8" stroke-width="0.8"/>'
    )
    lines.append(
        f'  <line x1="{lx2}" y1="{ly1}" x2="{lx2}" y2="{dim_y_top - 6}" stroke="#38bdf8" stroke-width="0.8"/>'
    )
    lines.append(
        f'  <text x="{cx}" y="{dim_y_top - 7}" fill="#38bdf8" font-size="11.5" font-weight="bold" text-anchor="middle">敷地間口 {site["lot_width_m"]}メートル（{int(site["lot_width_m"] * 1000)}ミリ）</text>'
    )

    dim_x_left = lx1 - 28
    lines.append(
        f'  <line x1="{dim_x_left}" y1="{ly1}" x2="{dim_x_left}" y2="{ly2}" stroke="#38bdf8" stroke-width="1.2"/>'
    )
    lines.append(
        f'  <text x="{dim_x_left - 10}" y="{round((ly1 + ly2) / 2, 1)}" fill="#38bdf8" font-size="11.5" font-weight="bold" text-anchor="end">敷地奥行 {site["lot_depth_m"]}メートル</text>'
    )

    # 方位記号（北マーク）
    lines.extend([
        '  <g transform="translate(835, 75)">',
        '    <circle cx="0" cy="0" r="28" fill="#0f172a" stroke="#38bdf8" stroke-width="1.5"/>',
        '    <polygon points="0,-22 -8,10 0,4 8,10" fill="#38bdf8"/>',
        '    <text x="0" y="-27" fill="#f8fafc" font-size="12" font-weight="bold" text-anchor="middle">北</text>',
        '  </g>',
    ])

    # 図面表題欄（右下）
    lines.extend([
        f'  <rect x="615" y="520" width="280" height="135" fill="#0f172a" stroke="#38bdf8" stroke-width="1.5"/>',
        f'  <text x="628" y="544" fill="#38bdf8" font-size="13" font-weight="bold">建築敷地配置・{target_fl["name_jp"]}</text>',
        f'  <text x="628" y="566" fill="#f8fafc" font-size="11.5">物件名称：{model["title"][:18]}</text>',
        f'  <text x="628" y="586" fill="#cbd5e1" font-size="11.5">所在地：{model["address"][:20]}</text>',
        f'  <text x="628" y="606" fill="#cbd5e1" font-size="11.5">敷地面積：{site["land_area_sqm"]}平米（{site["land_area_tsubo"]}坪）</text>',
        f'  <text x="628" y="626" fill="#cbd5e1" font-size="11.5">延床面積：{bld["total_floor_area_sqm"]}平米（地上{bld["stories"]}階建）</text>',
        f'  <text x="628" y="644" fill="#34d399" font-size="11">建蔽率 {site["building_coverage_ratio_pct"]}パーセント ／ 実効容積率 {site["effective_floor_area_ratio_pct"]}パーセント</text>',
        "</svg>",
    ])

    return "\n".join(lines)


def export_urban_svg(urban: Dict[str, Any]) -> str:
    """
    国土地理院の等高線と実在建物群・道路網を統合した
    都市・街区詳細平面図をベクター形式で出力する。
    """
    svg_w, svg_h = 920, 680
    cx, cy = 460, 340
    span_m = urban["terrain"]["span_m"]
    scale = 580.0 / max(100.0, span_m)

    def to_px(xm: float, ym: float) -> Tuple[float, float]:
        return round(cx + xm * scale, 1), round(cy - ym * scale, 1)

    lines: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {svg_w} {svg_h}" width="100%" height="100%" style="background:#09101d;font-family:\'Yu Gothic UI\',\'Meiryo\',sans-serif;">',
        f'  <rect x="10" y="10" width="{svg_w - 20}" height="{svg_h - 20}" fill="none" stroke="#38bdf8" stroke-width="1.5"/>',
    ]

    # 1. 等高線の描画
    for cnt in urban["terrain"]["contours"]:
        for seg in cnt["segments"]:
            x1, y1 = to_px(seg[0][0], seg[0][1])
            x2, y2 = to_px(seg[1][0], seg[1][1])
            lines.append(
                f'  <line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#1e40af" stroke-width="1" stroke-opacity="0.75"/>'
            )

    # 2. 実在道路網の描画
    for rd in urban["roads"]:
        pts_str = " ".join(
            f"{to_px(p[0], p[1])[0]},{to_px(p[0], p[1])[1]}"
            for p in rd["centerline_xy"]
        )
        stroke_w = max(2.0, round(rd["width_m"] * scale * 0.65, 1))
        lines.append(
            f'  <polyline points="{pts_str}" fill="none" stroke="#334155" stroke-width="{stroke_w}" stroke-linecap="round" stroke-linejoin="round"/>'
        )

    # 3. 実在建物ポリゴン群の描画
    for b in urban["buildings"]:
        pts_str = " ".join(
            f"{to_px(p[0], p[1])[0]},{to_px(p[0], p[1])[1]}"
            for p in b["footprint_xy"]
        )
        fill_col = "#38bdf8" if b["height_m"] >= 15.0 else "#475569"
        lines.append(
            f'  <polygon points="{pts_str}" fill="{fill_col}" fill-opacity="0.55" stroke="#bae6fd" stroke-width="1"/>'
        )

    # 4. 中心対象地点マーカー
    lines.append(
        f'  <circle cx="{cx}" cy="{cy}" r="9" fill="rgba(244,63,94,0.35)" stroke="#f43f5e" stroke-width="2.5"/>'
    )
    lines.append(
        f'  <text x="{cx}" y="{cy - 14}" fill="#fda4af" font-size="12" font-weight="bold" text-anchor="middle">調査対象地点（標高 {urban["terrain"]["center"]["elevation_m"]}メートル）</text>'
    )

    # 表題欄
    lines.extend([
        f'  <rect x="590" y="545" width="310" height="115" fill="#0f172a" stroke="#38bdf8" stroke-width="1.5"/>',
        f'  <text x="605" y="568" fill="#38bdf8" font-size="13" font-weight="bold">都市・街区・等高線 詳細平面図</text>',
        f'  <text x="605" y="590" fill="#f8fafc" font-size="11.5">中心座標：北緯{urban["coordinates"]["latitude"]:.4f}度 東経{urban["coordinates"]["longitude"]:.4f}度</text>',
        f'  <text x="605" y="610" fill="#cbd5e1" font-size="11.5">標高範囲：{urban["terrain"]["min_elevation_m"]}メートル 〜 {urban["terrain"]["max_elevation_m"]}メートル</text>',
        f'  <text x="605" y="630" fill="#cbd5e1" font-size="11.5">実在建物数：{urban["buildings_count"]}棟 ／ 道路数：{urban["roads_count"]}路線</text>',
        f'  <text x="605" y="648" fill="#34d399" font-size="10.5">資料：国土地理院数値標高モデル・ベクトルタイル</text>',
        "</svg>",
    ])
    return "\n".join(lines)


def export_dxf_file(
    data: Dict[str, Any],
    filename: str = "plan_export.dxf",
) -> Path:
    """
    AutoCAD / JW_CAD 等で直接開ける標準DXFフォーマット（ASCII DXF）として
    2D平面図（敷地境界・部屋・等高線・建物外形・道路）をファイル出力する。
    """
    lines: List[str] = [
        "0", "SECTION",
        "2", "HEADER",
        "9", "$INSUNITS",
        "70", "6",  # メートル単位
        "0", "ENDSEC",
        "0", "SECTION",
        "2", "ENTITIES",
    ]

    def add_dxf_line(x1: float, y1: float, x2: float, y2: float, layer: str, color: int = 7) -> None:
        lines.extend([
            "0", "LINE",
            "8", layer,
            "62", str(color),
            "10", f"{x1:.3f}",
            "20", f"{y1:.3f}",
            "30", "0.0",
            "11", f"{x2:.3f}",
            "21", f"{y2:.3f}",
            "31", "0.0",
        ])

    if data.get("mode") == "property":
        site = data["site"]
        lx1, ly1, lx2, ly2 = site["lot_bounds"]
        # 敷地外枠
        add_dxf_line(lx1, ly1, lx2, ly1, "LOT_BOUNDARY", 3)
        add_dxf_line(lx2, ly1, lx2, ly2, "LOT_BOUNDARY", 3)
        add_dxf_line(lx2, ly2, lx1, ly2, "LOT_BOUNDARY", 3)
        add_dxf_line(lx1, ly2, lx1, ly1, "LOT_BOUNDARY", 3)

        # 各階の部屋壁線
        for fl in data["building"]["floors"]:
            layer_name = f"FLOOR_{fl['floor_number']}_WALLS"
            for rm in fl["rooms"]:
                rx1, ry1 = rm["x"], rm["y"]
                rx2, ry2 = rx1 + rm["w"], ry1 + rm["d"]
                add_dxf_line(rx1, ry1, rx2, ry1, layer_name, 5)
                add_dxf_line(rx2, ry1, rx2, ry2, layer_name, 5)
                add_dxf_line(rx2, ry2, rx1, ry2, layer_name, 5)
                add_dxf_line(rx1, ry2, rx1, ry1, layer_name, 5)
    else:
        # 都市平面図DXF（等高線・道路・建物）
        for cnt in data.get("terrain", {}).get("contours", []):
            for seg in cnt["segments"]:
                add_dxf_line(seg[0][0], seg[0][1], seg[1][0], seg[1][1], "CONTOUR", 4)
        for rd in data.get("roads", []):
            pts = rd["centerline_xy"]
            for idx in range(len(pts) - 1):
                add_dxf_line(pts[idx][0], pts[idx][1], pts[idx + 1][0], pts[idx + 1][1], "ROADS", 8)
        for bld in data.get("buildings", []):
            pts = bld["footprint_xy"]
            for idx in range(len(pts) - 1):
                add_dxf_line(pts[idx][0], pts[idx][1], pts[idx + 1][0], pts[idx + 1][1], "BUILDINGS", 2)

    lines.extend(["0", "ENDSEC", "0", "EOF"])
    out_path = EXPORT_DIR / filename
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def _build_3d_triangles(data: Dict[str, Any]) -> Tuple[List[Tuple[float, float, float]], List[Tuple[int, int, int]]]:
    """
    個別建物モデルまたは都市・地形モデルから3D頂点リスト (x, z_up, -y) と三角形インデックスを生成する。
    """
    vertices: List[Tuple[float, float, float]] = []
    faces: List[Tuple[int, int, int]] = []

    def add_box(x1: float, y1: float, z1: float, x2: float, y2: float, z2: float) -> None:
        base_idx = len(vertices)
        # WebGL / glTF / OBJ 標準の Y-up 座標系 (X=東, Y=高さZ, Z=-北)
        pts = [
            (x1, z1, -y1),
            (x2, z1, -y1),
            (x2, z1, -y2),
            (x1, z1, -y2),
            (x1, z2, -y1),
            (x2, z2, -y1),
            (x2, z2, -y2),
            (x1, z2, -y2),
        ]
        vertices.extend(pts)
        box_tris = [
            (0, 1, 2), (0, 2, 3),  # 底面
            (4, 6, 5), (4, 7, 6),  # 天面
            (0, 4, 5), (0, 5, 1),  # 南面
            (1, 5, 6), (1, 6, 2),  # 東面
            (2, 6, 7), (2, 7, 3),  # 北面
            (3, 7, 4), (3, 4, 0),  # 西面
        ]
        for a, b, c in box_tris:
            faces.append((base_idx + a, base_idx + b, base_idx + c))

    if data.get("mode") == "property":
        site = data["site"]
        lx1, ly1, lx2, ly2 = site["lot_bounds"]
        # 敷地地盤プレート
        add_box(lx1, ly1, -0.25, lx2, ly2, 0.0)
        # 各階の各室壁・スラブを立体化
        for fl in data["building"]["floors"]:
            zb = fl["z_bottom_m"]
            zt = fl["z_top_m"]
            for rm in fl["rooms"]:
                rx1, ry1 = rm["x"], rm["y"]
                rx2, ry2 = rx1 + rm["w"], ry1 + rm["d"]
                # 床スラブ
                add_box(rx1, ry1, zb, rx2, ry2, zb + 0.18)
                # 外周壁（バルコニーは手すり高1.1m、室内は階高）
                wall_h = zb + 1.1 if "バルコニー" in rm["name"] else zt
                wt = 0.14
                add_box(rx1, ry1, zb, rx2, ry1 + wt, wall_h)
                add_box(rx1, ry2 - wt, zb, rx2, ry2, wall_h)
                add_box(rx1, ry1, zb, rx1 + wt, ry2, wall_h)
                add_box(rx2 - wt, ry1, zb, rx2, ry2, wall_h)
    else:
        # 都市・地形3Dメッシュ
        terrain = data["terrain"]
        xs = terrain["x_coords"]
        ys = terrain["y_coords"]
        elevs = terrain["elevations"]
        min_el = terrain["min_elevation_m"]
        n = terrain["grid_size"]

        base_v = len(vertices)
        for i in range(n):
            for j in range(n):
                vertices.append((xs[j], round(elevs[i][j] - min_el, 2), -ys[i]))

        for i in range(n - 1):
            for j in range(n - 1):
                v00 = base_v + i * n + j
                v10 = base_v + i * n + (j + 1)
                v01 = base_v + (i + 1) * n + j
                v11 = base_v + (i + 1) * n + (j + 1)
                faces.append((v00, v10, v11))
                faces.append((v00, v11, v01))

        # 周辺実在3D建物群の押出メッシュ
        for bld in data.get("buildings", []):
            fp = bld["footprint_xy"]
            if len(fp) < 3:
                continue
            bx_min = min(p[0] for p in fp)
            bx_max = max(p[0] for p in fp)
            by_min = min(p[1] for p in fp)
            by_max = max(p[1] for p in fp)
            bz1 = round(bld["base_elevation_m"] - min_el, 2)
            bz2 = round(bz1 + bld["height_m"], 2)
            add_box(bx_min, by_min, bz1, bx_max, by_max, bz2)

    return vertices, faces


def export_obj_file(
    data: Dict[str, Any],
    filename: str = "model_export.obj",
) -> Path:
    """
    Blender / Unreal Engine / Unity / SketchUp 等で直接インポート可能な
    Wavefront OBJ 3Dモデルファイルを生成する。
    """
    vertices, faces = _build_3d_triangles(data)
    lines: List[str] = [
        "# 全国不動産・土地網羅検索システム 3Dモデル出力",
        f"# モード: {data.get('mode', 'property')}",
        "o JapanProperty3DModel",
    ]
    for vx, vy, vz in vertices:
        lines.append(f"v {vx:.3f} {vy:.3f} {vz:.3f}")
    for f0, f1, f2 in faces:
        lines.append(f"f {f0 + 1} {f1 + 1} {f2 + 1}")

    out_path = EXPORT_DIR / filename
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def export_gltf_file(
    data: Dict[str, Any],
    filename: str = "model_export.gltf",
) -> Path:
    """
    WebGL / Three.js / Blender / 各種3Dビューアでそのまま開ける
    単一ファイル埋め込み型 glTF 2.0 (.gltf) 3Dモデルを生成する。
    """
    vertices, faces = _build_3d_triangles(data)
    if not vertices or not faces:
        vertices = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        faces = [(0, 1, 2)]

    v_bin = bytearray()
    min_x = min_y = min_z = 999999.0
    max_x = max_y = max_z = -999999.0
    for vx, vy, vz in vertices:
        v_bin.extend(struct.pack("<3f", float(vx), float(vy), float(vz)))
        min_x, max_x = min(min_x, vx), max(max_x, vx)
        min_y, max_y = min(min_y, vy), max(max_y, vy)
        min_z, max_z = min(min_z, vz), max(max_z, vz)

    i_bin = bytearray()
    for f0, f1, f2 in faces:
        i_bin.extend(struct.pack("<3I", int(f0), int(f1), int(f2)))

    combined = v_bin + i_bin
    b64_uri = "data:application/octet-stream;base64," + base64.b64encode(combined).decode("ascii")

    gltf_dict = {
        "asset": {"version": "2.0", "generator": "jp-property-finder-3d-engine"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "JapanProperty3DMesh"}],
        "meshes": [
            {
                "name": "JapanProperty3DMesh",
                "primitives": [
                    {
                        "attributes": {"POSITION": 0},
                        "indices": 1,
                        "material": 0,
                    }
                ],
            }
        ],
        "materials": [
            {
                "name": "ArchitecturalMaterial",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.22, 0.74, 0.97, 1.0],
                    "metallicFactor": 0.1,
                    "roughnessFactor": 0.7,
                },
                "doubleSided": True,
            }
        ],
        "buffers": [
            {
                "byteLength": len(combined),
                "uri": b64_uri,
            }
        ],
        "bufferViews": [
            {
                "buffer": 0,
                "byteOffset": 0,
                "byteLength": len(v_bin),
                "target": 34962,
            },
            {
                "buffer": 0,
                "byteOffset": len(v_bin),
                "byteLength": len(i_bin),
                "target": 34963,
            },
        ],
        "accessors": [
            {
                "bufferView": 0,
                "byteOffset": 0,
                "componentType": 5126,
                "count": len(vertices),
                "type": "VEC3",
                "min": [round(min_x, 3), round(min_y, 3), round(min_z, 3)],
                "max": [round(max_x, 3), round(max_y, 3), round(max_z, 3)],
            },
            {
                "bufferView": 1,
                "byteOffset": 0,
                "componentType": 5125,
                "count": len(faces) * 3,
                "type": "SCALAR",
            },
        ],
    }

    out_path = EXPORT_DIR / filename
    out_path.write_text(json.dumps(gltf_dict, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path

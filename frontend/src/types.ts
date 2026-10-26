export interface PrefectureInfo {
  code: string;
  name: string;
  region: string;
  lat: number;
  lon: number;
}

export interface MunicipalityInfo {
  muni_code: string;
  pref_code: string;
  pref_name: string;
  muni_name: string;
  full_name: string;
}

export interface MapLayerInfo {
  id: string;
  name: string;
  url: string;
  attribution: string;
}

export interface AgentEvaluation {
  id: number;
  property_id: number;
  agent_name: string;
  score: number;
  recommendation: string;
  pros: string;
  cons: string;
  notes: string;
  created_at: string;
}

export interface PriceHistoryEntry {
  id: number;
  property_id: number;
  price: number;
  source_site: string;
  recorded_at: string;
}

export interface PropertyItem {
  id: number;
  canonical_hash: string;
  title: string;
  headline: string;
  category: string;
  transaction_type: string;
  price: number;
  monthly_rent: number | null;
  price_per_sqm: number | null;
  price_per_tsubo: number | null;
  prefecture_code: string;
  prefecture_name: string;
  municipality_name: string;
  address: string;
  latitude: number | null;
  longitude: number | null;
  elevation_m: number | null;
  nearest_station: string | null;
  station_walk_min: number | null;
  station_raw: string;
  land_area_sqm: number | null;
  land_area_tsubo: number | null;
  building_area_sqm: number | null;
  building_area_tsubo: number | null;
  floor_plan: string | null;
  structure: string | null;
  build_year: number | null;
  building_coverage_ratio: number | null;
  floor_area_ratio: number | null;
  max_buildable_area_sqm: number | null;
  market_deviation_pct: number | null;
  estimated_gross_yield_pct: number | null;
  primary_source_site: string;
  primary_source_url: string;
  source_sites: string[];
  source_urls: Record<string, string>;
  image_urls: string[];
  features: string[];
  first_seen_at: string;
  last_seen_at: string;
  evaluation_count?: number;
  avg_agent_score?: number | null;
  price_history?: PriceHistoryEntry[];
  agent_evaluations?: AgentEvaluation[];
}

export interface SpatialAnalysisResult {
  property_id: number;
  title: string;
  address: string;
  hazard_and_topography: {
    coordinates: { latitude: number; longitude: number };
    elevation: { elevation_m: number | null; elevation_source: string };
    terrain_evaluation: string;
    hazard_layers: Record<
      string,
      {
        layer_name: string;
        area_has_designated_zone: boolean;
        tile_url: string;
        tile_bytes: number;
        judgment: string;
      }
    >;
  };
  surrounding_facilities: Array<{
    name: string;
    type: string;
    distance_m: number;
    walk_minutes: number;
    latitude: number;
    longitude: number;
  }>;
  mlit_public_data: {
    enabled: boolean;
    message: string;
    records: unknown[];
  };
}

export interface WatchlistGroup {
  id: number;
  name: string;
  description: string;
  created_at: string;
  items: Array<
    PropertyItem & {
      item_id: number;
      status: string;
      memo: string;
      added_by: string;
      added_at: string;
    }
  >;
}

export interface AgentActivityEntry {
  agent_name: string;
  action: string;
  summary: string;
  highlighted_property_ids: number[];
  timestamp: string;
}

export interface UISyncState {
  map_view: {
    center_lat: number;
    center_lon: number;
    zoom: number;
    polygon: number[][];
  };
  selected_property_ids: number[];
  highlighted_property_ids: number[];
  active_filters: Record<string, unknown>;
  agent_activity_log: AgentActivityEntry[];
}

export interface DatabaseStats {
  total_properties: number;
  multi_portal_merged_count: number;
  total_agent_evaluations: number;
  by_category: Array<{
    category: string;
    count: number;
    avg_price: number;
    avg_tsubo_price: number;
  }>;
  by_prefecture: Array<{
    prefecture_code: string;
    prefecture_name: string;
    count: number;
  }>;
  by_source: Array<{
    source_site: string;
    count: number;
  }>;
}

/**
 * 金額（円）を純日本語の億・万円表記へ変換する
 */
export function formatYenJP(yen: number | null | undefined): string {
  if (yen === null || yen === undefined || Number.isNaN(yen)) {
    return "未定";
  }
  const n = Math.round(yen);
  if (n >= 100_000_000) {
    const oku = Math.floor(n / 100_000_000);
    const man = Math.floor((n % 100_000_000) / 10_000);
    return man > 0 ? `${oku}億${man.toLocaleString("ja-JP")}万円` : `${oku}億円`;
  }
  if (n >= 10_000) {
    return `${Math.floor(n / 10_000).toLocaleString("ja-JP")}万円`;
  }
  return `${n.toLocaleString("ja-JP")}円`;
}

/**
 * 間取り表記（3LDK、1R等）を英字の混ざらない純日本語表記へ変換する
 */
export function formatFloorPlanJP(fp: string | null | undefined): string {
  if (!fp) return "記載なし";
  let s = fp;
  s = s.replace(/ワンルーム/g, "単身一室");
  s = s.replace(/(\d+)SLDK/gi, "$1室＋納戸・居間・食堂・台所");
  s = s.replace(/(\d+)LDK/gi, "$1室＋居間・食堂・台所");
  s = s.replace(/(\d+)SDK/gi, "$1室＋納戸・食堂・台所");
  s = s.replace(/(\d+)DK/gi, "$1室＋食堂・台所");
  s = s.replace(/(\d+)SK/gi, "$1室＋納戸・台所");
  s = s.replace(/(\d+)K/gi, "$1室＋台所");
  s = s.replace(/(\d+)R/gi, "$1洋室");
  s = s.replace(/\+(\d*)S/gi, "＋納戸$1");
  s = s.replace(/[A-Za-z]+/g, "");
  return s.trim() || "一室";
}

/**
 * ポータルから取得した物件名等に含まれる英単語（ADCAST, NEW, JR等）を純日本語表記に正規化する
 */
export function toPureJapaneseText(raw: string | null | undefined): string {
  if (!raw) return "";
  let s = raw;
  const replacements: Array<[RegExp, string]> = [
    [/ADCAST/gi, "アドキャスト"],
    [/NEW/gi, "新着"],
    [/JR/gi, "ジェイアール"],
    [/PR/gi, "注目"],
    [/TOP(\d+)/gi, "上位$1"],
    [/III/g, "第3"],
    [/II/g, "第2"],
    [/NTPR/gi, "エヌティ"],
    [/ParkAxis/gi, "パークアクシス"],
    [/Prime\s*Art/gi, "プライムアート"],
    [/The\s*Verlux\s*Takanawa/gi, "ヴェラックス高輪"],
    [/Due\s*West\s*Azabu\s*House/gi, "ウエスト麻布ハウス"],
    [/WORLD\s*TOWER\s*RESIDENCE/gi, "ワールドタワーレジデンス"],
    [/BELLA/gi, "ベラ"],
    [/west/gi, "ウエスト"],
    [/m2/gi, "平米"],
    [/㎡/g, "平米"],
    [/[A-Za-z]{2,}/g, ""],
  ];
  for (const [pattern, rep] of replacements) {
    s = s.replace(pattern, rep);
  }
  return s.replace(/\s+/g, " ").trim() || "物件詳細";
}

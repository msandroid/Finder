import {
  AgentEvaluation,
  DatabaseStats,
  MapLayerInfo,
  MunicipalityInfo,
  PrefectureInfo,
  PropertyItem,
  SpatialAnalysisResult,
  UISyncState,
  WatchlistGroup,
} from "./types";

export async function fetchPrefectures(): Promise<PrefectureInfo[]> {
  const res = await fetch("/api/prefectures");
  return res.json();
}

export async function fetchMunicipalities(prefCode: string): Promise<MunicipalityInfo[]> {
  const res = await fetch(`/api/municipalities/${prefCode}`);
  return res.json();
}

export async function fetchLayers(): Promise<{
  base_layers: MapLayerInfo[];
  hazard_layers: MapLayerInfo[];
}> {
  const res = await fetch("/api/layers");
  return res.json();
}

export async function fetchStats(): Promise<DatabaseStats> {
  const res = await fetch("/api/stats");
  return res.json();
}

export async function fetchProperties(params: Record<string, string | number | boolean | undefined>): Promise<{
  count: number;
  properties: PropertyItem[];
}> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "" && v !== null) {
      qs.set(k, String(v));
    }
  }
  const res = await fetch(`/api/properties?${qs.toString()}`);
  return res.json();
}

export async function triggerLiveSearch(payload: {
  prefecture: string;
  municipality?: string;
  category: string;
  keyword?: string;
  agent_name?: string;
}): Promise<{
  fetched_raw_count: number;
  source_breakdown: Record<string, number>;
  upsert_summary: { inserted: number; updated: number; merged_multi_portal: number };
  properties: PropertyItem[];
}> {
  const res = await fetch("/api/search/live", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function runPolygonSearch(payload: {
  polygon: number[][];
  category?: string;
  only_undervalued?: boolean;
  sort_by?: string;
}): Promise<{ count: number; properties: PropertyItem[] }> {
  const res = await fetch("/api/properties/polygon-search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function fetchPropertyDetail(propertyId: number): Promise<PropertyItem> {
  const res = await fetch(`/api/properties/${propertyId}`);
  return res.json();
}

export async function fetchSpatialAnalysis(propertyId: number): Promise<SpatialAnalysisResult> {
  const res = await fetch(`/api/properties/${propertyId}/spatial-analysis`);
  return res.json();
}

export async function submitAgentEvaluation(
  propertyId: number,
  payload: {
    agent_name: string;
    score: number;
    recommendation: string;
    pros: string;
    cons: string;
    notes: string;
  }
): Promise<AgentEvaluation> {
  const res = await fetch(`/api/properties/${propertyId}/evaluations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function fetchWatchlists(): Promise<WatchlistGroup[]> {
  const res = await fetch("/api/watchlists");
  return res.json();
}

export async function addToWatchlistApi(payload: {
  property_id: number;
  watchlist_name?: string;
  status?: string;
  memo?: string;
  added_by?: string;
}): Promise<unknown> {
  const res = await fetch("/api/watchlists/items", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function removeFromWatchlistApi(watchlistId: number, propertyId: number): Promise<void> {
  await fetch(`/api/watchlists/${watchlistId}/items/${propertyId}`, {
    method: "DELETE",
  });
}

export async function simulateVolumeApi(payload: {
  land_area_sqm: number;
  building_coverage_ratio: number;
  floor_area_ratio: number;
  road_width_m: number;
  zoning_category: string;
  corner_lot_relaxation: boolean;
  structure_type: string;
}): Promise<{
  result: {
    effective_coverage_ratio_pct: number;
    effective_floor_area_ratio_pct: number;
    max_building_footprint_sqm: number;
    max_building_footprint_tsubo: number;
    max_total_floor_area_sqm: number;
    max_total_floor_area_tsubo: number;
    estimated_rentable_area_sqm: number;
    recommended_stories: number;
    estimated_construction_cost_yen: number;
    construction_cost_per_tsubo_yen: number;
  };
}> {
  const res = await fetch("/api/simulate/volume", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function simulateYieldApi(payload: {
  price_yen: number;
  expected_monthly_rent_yen: number;
  renovation_or_build_cost_yen: number;
  down_payment_yen: number;
  loan_interest_rate_pct: number;
  loan_years: number;
}): Promise<{
  summary: {
    total_investment_yen: number;
    loan_principal_yen: number;
    annual_gross_income_yen: number;
    annual_noi_yen: number;
    monthly_loan_payment_yen: number;
    annual_cash_flow_yen: number;
    monthly_cash_flow_yen: number;
    gross_yield_pct: number;
    net_yield_pct: number;
    equity_ccr_pct: number | null;
    payback_years: number | null;
  };
}> {
  const res = await fetch("/api/simulate/yield", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function comparePropertiesApi(propertyIds: number[]): Promise<{
  count: number;
  properties: PropertyItem[];
  markdown_report: string;
}> {
  const res = await fetch("/api/compare", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ property_ids: propertyIds }),
  });
  return res.json();
}

export async function fetchUISync(): Promise<UISyncState> {
  const res = await fetch("/api/ui-sync");
  return res.json();
}

export async function updateUISync(key: string, value: unknown): Promise<UISyncState> {
  const res = await fetch("/api/ui-sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, value, notify_agents: true }),
  });
  return res.json();
}

export async function fetchAgentIntegrationGuide(): Promise<{
  workspace_path: string;
  mcp_server_config: unknown;
  cli_examples: string[];
}> {
  const res = await fetch("/api/agent-integration-guide");
  return res.json();
}

import React, { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import {
  addToWatchlistApi,
  comparePropertiesApi,
  fetchLayers,
  fetchMunicipalities,
  fetchPrefectures,
  fetchProperties,
  fetchPropertyDetail,
  fetchSpatialAnalysis,
  fetchStats,
  fetchUISync,
  fetchWatchlists,
  removeFromWatchlistApi,
  runPolygonSearch,
  simulateVolumeApi,
  simulateYieldApi,
  submitAgentEvaluation,
  triggerLiveSearch,
  updateUISync,
} from "./api";
import {
  DatabaseStats,
  formatFloorPlanJP,
  formatYenJP,
  MapLayerInfo,
  MunicipalityInfo,
  PrefectureInfo,
  PropertyItem,
  SpatialAnalysisResult,
  toPureJapaneseText,
  UISyncState,
  WatchlistGroup,
} from "./types";
import { Cad3DStudio } from "./Cad3DStudio";

type MainTab = "search" | "cad3d" | "simulator" | "watchlist" | "agent";
type RightSubTab = "list" | "detail" | "activity";

const MAPLIBRE_JP_LOCALE: Record<string, string> = {
  "NavigationControl.ZoomIn": "地図を拡大",
  "NavigationControl.ZoomOut": "地図を縮小",
  "NavigationControl.ResetBearing": "北を上に戻す",
  "ScaleControl.Meters": "メートル",
  "ScaleControl.Kilometers": "キロメートル",
};

export function App() {
  const [mainTab, setMainTab] = useState<MainTab>("search");
  const [rightSubTab, setRightSubTab] = useState<RightSubTab>("list");

  const [prefectures, setPrefectures] = useState<PrefectureInfo[]>([]);
  const [municipalities, setMunicipalities] = useState<MunicipalityInfo[]>([]);
  const [baseLayers, setBaseLayers] = useState<MapLayerInfo[]>([]);
  const [hazardLayers, setHazardLayers] = useState<MapLayerInfo[]>([]);
  const [stats, setStats] = useState<DatabaseStats | null>(null);
  const [wsConnected, setWsConnected] = useState<boolean>(false);

  // 検索条件ステート
  const [selectedPrefCode, setSelectedPrefCode] = useState<string>("13");
  const [selectedMuniName, setSelectedMuniName] = useState<string>("港区");
  const [selectedCategory, setSelectedCategory] = useState<string>("すべて");
  const [minPriceMan, setMinPriceMan] = useState<string>("");
  const [maxPriceMan, setMaxPriceMan] = useState<string>("");
  const [minLandArea, setMinLandArea] = useState<string>("");
  const [maxWalkMin, setMaxWalkMin] = useState<string>("");
  const [keyword, setKeyword] = useState<string>("");
  const [onlyUndervalued, setOnlyUndervalued] = useState<boolean>(false);
  const [sortBy, setSortBy] = useState<string>("price_asc");

  // 地図・レイヤーステート
  const [activeBaseLayer, setActiveBaseLayer] = useState<string>("std");
  const [activeHazards, setActiveHazards] = useState<Record<string, boolean>>({
    flood: false,
    tsunami: false,
    landslide: false,
    debris: false,
  });
  const [polygonMode, setPolygonMode] = useState<boolean>(false);
  const [polygonPoints, setPolygonPoints] = useState<number[][]>([]);

  // 物件データ・詳細ステート
  const [properties, setProperties] = useState<PropertyItem[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [liveSearching, setLiveSearching] = useState<boolean>(false);
  const [statusMessage, setStatusMessage] = useState<string>(
    "日本全国の不動産・土地データベースおよび国土地理院地図と接続完了"
  );

  const [selectedProperty, setSelectedProperty] = useState<PropertyItem | null>(null);
  const [spatialAnalysis, setSpatialAnalysis] = useState<SpatialAnalysisResult | null>(null);
  const [analyzingSpatial, setAnalyzingSpatial] = useState<boolean>(false);

  // 比較チェック・案件リスト・同期状態
  const [compareIds, setCompareIds] = useState<number[]>([]);
  const [comparisonReport, setComparisonReport] = useState<string>("");
  const [watchlists, setWatchlists] = useState<WatchlistGroup[]>([]);
  const [uiSyncState, setUiSyncState] = useState<UISyncState | null>(null);

  // エージェント評価入力フォーム
  const [evalAgent, setEvalAgent] = useState<string>("アンチグラビティ");
  const [evalScore, setEvalScore] = useState<number>(85);
  const [evalRec, setEvalRec] = useState<string>("推奨");
  const [evalPros, setEvalPros] = useState<string>("");
  const [evalCons, setEvalCons] = useState<string>("");
  const [evalNotes, setEvalNotes] = useState<string>("");

  // 建築ボリューム試算ステート
  const [volLandArea, setVolLandArea] = useState<number>(100);
  const [volCoverage, setVolCoverage] = useState<number>(60);
  const [volFar, setVolFar] = useState<number>(200);
  const [volRoadWidth, setVolRoadWidth] = useState<number>(6);
  const [volZoning, setVolZoning] = useState<string>("住居系");
  const [volCorner, setVolCorner] = useState<boolean>(false);
  const [volStructure, setVolStructure] = useState<string>("木造");
  const [volResult, setVolResult] = useState<{
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
  } | null>(null);

  // 収益利回り試算ステート
  const [yldPriceMan, setYldPriceMan] = useState<number>(8000);
  const [yldMonthlyRentMan, setYldMonthlyRentMan] = useState<number>(45);
  const [yldBuildCostMan, setYldBuildCostMan] = useState<number>(0);
  const [yldDownPaymentMan, setYldDownPaymentMan] = useState<number>(1000);
  const [yldInterest, setYldInterest] = useState<number>(1.8);
  const [yldYears, setYldYears] = useState<number>(30);
  const [yldResult, setYldResult] = useState<{
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
  } | null>(null);

  // MapLibre 参照
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const markersRef = useRef<maplibregl.Marker[]>([]);
  const polygonModeRef = useRef<boolean>(false);
  polygonModeRef.current = polygonMode;

  // 初期データ読込
  useEffect(() => {
    async function init() {
      const [prefs, layers, st, wls, sync] = await Promise.all([
        fetchPrefectures(),
        fetchLayers(),
        fetchStats(),
        fetchWatchlists(),
        fetchUISync(),
      ]);
      setPrefectures(prefs);
      setBaseLayers(layers.base_layers);
      setHazardLayers(layers.hazard_layers);
      setStats(st);
      setWatchlists(wls);
      setUiSyncState(sync);

      const munis = await fetchMunicipalities("13");
      setMunicipalities(munis);

      await executeLocalQuery("13", "港区", "すべて");
      await handleRunVolumeSim();
      await handleRunYieldSim();
    }
    init();
  }, []);

  // 都道府県変更時に市区町村一覧を更新
  useEffect(() => {
    if (!selectedPrefCode) return;
    fetchMunicipalities(selectedPrefCode).then((munis) => {
      setMunicipalities(munis);
    });
  }, [selectedPrefCode]);

  // WebSocket リアルタイム同期接続
  useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    let ws: WebSocket | null = null;
    let reconnectTimer: number | null = null;

    function connectWs() {
      ws = new WebSocket(wsUrl);
      ws.onopen = () => setWsConnected(true);
      ws.onclose = () => {
        setWsConnected(false);
        reconnectTimer = window.setTimeout(connectWs, 3000);
      };
      ws.onmessage = async (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.ui_sync_state) {
            setUiSyncState(msg.ui_sync_state);
            const mv = msg.ui_sync_state.map_view;
            if (mv && mapRef.current && msg.type === "LIVE_SEARCH_COMPLETED") {
              mapRef.current.flyTo({
                center: [mv.center_lon, mv.center_lat],
                zoom: mv.zoom || 13,
              });
            }
          }
          if (
            msg.type === "LIVE_SEARCH_COMPLETED" ||
            msg.type === "AGENT_EVALUATION_ADDED" ||
            msg.type === "WATCHLIST_UPDATED" ||
            msg.type === "CAD3D_GENERATED"
          ) {
            const [st, wls] = await Promise.all([fetchStats(), fetchWatchlists()]);
            setStats(st);
            setWatchlists(wls);
            await executeLocalQuery();
          }
        } catch {
          // 無視
        }
      };
    }

    connectWs();
    return () => {
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) ws.close();
    };
  }, []);

  // MapLibre 初期化
  useEffect(() => {
    if (mainTab !== "search" || !mapContainerRef.current || mapRef.current) {
      return;
    }

    const map = new maplibregl.Map({
      container: mapContainerRef.current,
      locale: MAPLIBRE_JP_LOCALE,
      attributionControl: false,
      style: {
        version: 8,
        sources: {
          "gsi-std": {
            type: "raster",
            tiles: ["https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png"],
            tileSize: 256,
          },
          "gsi-pale": {
            type: "raster",
            tiles: ["https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png"],
            tileSize: 256,
          },
          "gsi-seamlessphoto": {
            type: "raster",
            tiles: ["https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg"],
            tileSize: 256,
          },
          "gsi-relief": {
            type: "raster",
            tiles: ["https://cyberjapandata.gsi.go.jp/xyz/relief/{z}/{x}/{y}.png"],
            tileSize: 256,
          },
          "hazard-flood": {
            type: "raster",
            tiles: [
              "https://disaportaldata.gsi.go.jp/raster/01_flood_l2_shinsuishin_data/{z}/{x}/{y}.png",
            ],
            tileSize: 256,
          },
          "hazard-tsunami": {
            type: "raster",
            tiles: [
              "https://disaportaldata.gsi.go.jp/raster/04_tsunami_newlegend_data/{z}/{x}/{y}.png",
            ],
            tileSize: 256,
          },
          "hazard-landslide": {
            type: "raster",
            tiles: [
              "https://disaportaldata.gsi.go.jp/raster/05_kyukeishakeikaikuiki/{z}/{x}/{y}.png",
            ],
            tileSize: 256,
          },
          "hazard-debris": {
            type: "raster",
            tiles: [
              "https://disaportaldata.gsi.go.jp/raster/05_dosekiryukeikaikuiki/{z}/{x}/{y}.png",
            ],
            tileSize: 256,
          },
          "search-polygon": {
            type: "geojson",
            data: {
              type: "FeatureCollection",
              features: [],
            },
          },
        },
        layers: [
          {
            id: "layer-std",
            type: "raster",
            source: "gsi-std",
            layout: { visibility: "visible" },
          },
          {
            id: "layer-pale",
            type: "raster",
            source: "gsi-pale",
            layout: { visibility: "none" },
          },
          {
            id: "layer-seamlessphoto",
            type: "raster",
            source: "gsi-seamlessphoto",
            layout: { visibility: "none" },
          },
          {
            id: "layer-relief",
            type: "raster",
            source: "gsi-relief",
            layout: { visibility: "none" },
          },
          {
            id: "layer-hazard-flood",
            type: "raster",
            source: "hazard-flood",
            paint: { "raster-opacity": 0.7 },
            layout: { visibility: "none" },
          },
          {
            id: "layer-hazard-tsunami",
            type: "raster",
            source: "hazard-tsunami",
            paint: { "raster-opacity": 0.7 },
            layout: { visibility: "none" },
          },
          {
            id: "layer-hazard-landslide",
            type: "raster",
            source: "hazard-landslide",
            paint: { "raster-opacity": 0.75 },
            layout: { visibility: "none" },
          },
          {
            id: "layer-hazard-debris",
            type: "raster",
            source: "hazard-debris",
            paint: { "raster-opacity": 0.75 },
            layout: { visibility: "none" },
          },
          {
            id: "polygon-fill",
            type: "fill",
            source: "search-polygon",
            paint: {
              "fill-color": "#38bdf8",
              "fill-opacity": 0.22,
            },
          },
          {
            id: "polygon-outline",
            type: "line",
            source: "search-polygon",
            paint: {
              "line-color": "#38bdf8",
              "line-width": 2.5,
            },
          },
        ],
      },
      center: [139.7454, 35.6586],
      zoom: 13,
    });

    map.addControl(new maplibregl.NavigationControl({ showCompass: true }), "top-right");

    map.on("click", (e) => {
      if (!polygonModeRef.current) return;
      const pt: [number, number] = [
        Number(e.lngLat.lat.toFixed(6)),
        Number(e.lngLat.lng.toFixed(6)),
      ];
      setPolygonPoints((prev) => [...prev, pt]);
    });

    mapRef.current = map;

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [mainTab]);

  // ベースマップ切替反映
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    for (const id of ["std", "pale", "seamlessphoto", "relief"]) {
      const layerId = `layer-${id}`;
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(
          layerId,
          "visibility",
          activeBaseLayer === id ? "visible" : "none"
        );
      }
    }
  }, [activeBaseLayer]);

  // ハザードマップ切替反映
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    for (const [key, enabled] of Object.entries(activeHazards)) {
      const layerId = `layer-hazard-${key}`;
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(layerId, "visibility", enabled ? "visible" : "none");
      }
    }
  }, [activeHazards]);

  // ポリゴン描画反映
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const src = map.getSource("search-polygon") as maplibregl.GeoJSONSource | undefined;
    if (!src) return;
    if (polygonPoints.length < 2) {
      src.setData({ type: "FeatureCollection", features: [] });
      return;
    }
    const ring = polygonPoints.map((p) => [p[1], p[0]]);
    ring.push([polygonPoints[0][1], polygonPoints[0][0]]);
    src.setData({
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: {},
          geometry: {
            type: "Polygon",
            coordinates: [ring],
          },
        },
      ],
    });
  }, [polygonPoints]);

  // 地図マーカー描画
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    for (const m of markersRef.current) {
      m.remove();
    }
    markersRef.current = [];

    const highlightedIds = uiSyncState?.highlighted_property_ids || [];

    for (const prop of properties) {
      if (prop.latitude === null || prop.longitude === null) continue;

      const isSelected = selectedProperty?.id === prop.id;
      const isHighlighted = highlightedIds.includes(prop.id);
      const isUndervalued =
        prop.market_deviation_pct !== null && prop.market_deviation_pct < -10;

      let bgColor = "#4f46e5";
      if (isSelected) bgColor = "#0ea5e9";
      else if (isHighlighted) bgColor = "#f59e0b";
      else if (isUndervalued) bgColor = "#10b981";

      const el = document.createElement("div");
      el.style.background = bgColor;
      el.style.color = "#ffffff";
      el.style.padding = "3px 7px";
      el.style.borderRadius = "999px";
      el.style.fontSize = "11px";
      el.style.fontWeight = "700";
      el.style.border = isSelected ? "2px solid #ffffff" : "1px solid rgba(255,255,255,0.75)";
      el.style.boxShadow = "0 2px 6px rgba(0,0,0,0.45)";
      el.style.cursor = "pointer";
      el.style.whiteSpace = "nowrap";
      el.textContent = formatYenJP(prop.price);

      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        handleSelectProperty(prop);
      });

      const marker = new maplibregl.Marker({ element: el })
        .setLngLat([prop.longitude, prop.latitude])
        .addTo(map);
      markersRef.current.push(marker);
    }
  }, [properties, selectedProperty, uiSyncState]);

  async function executeLocalQuery(
    overridePref?: string,
    overrideMuni?: string,
    overrideCat?: string
  ) {
    setLoading(true);
    try {
      const pref = overridePref !== undefined ? overridePref : selectedPrefCode;
      const muni = overrideMuni !== undefined ? overrideMuni : selectedMuniName;
      const cat = overrideCat !== undefined ? overrideCat : selectedCategory;

      const minPrice = minPriceMan ? Number(minPriceMan) * 10_000 : undefined;
      const maxPrice = maxPriceMan ? Number(maxPriceMan) * 10_000 : undefined;
      const minArea = minLandArea ? Number(minLandArea) : undefined;
      const maxWalk = maxWalkMin ? Number(maxWalkMin) : undefined;

      const res = await fetchProperties({
        prefecture_code: pref || undefined,
        municipality_name: muni || undefined,
        category: cat !== "すべて" ? cat : undefined,
        min_price: minPrice,
        max_price: maxPrice,
        min_land_area_sqm: minArea,
        max_walk_min: maxWalk,
        keyword: keyword || undefined,
        only_undervalued: onlyUndervalued,
        sort_by: sortBy,
        limit: 150,
      });
      setProperties(res.properties);
      setStatusMessage(`ローカルデータベースより ${res.count} 件の物件を抽出しました`);

      if (res.properties.length > 0 && mapRef.current) {
        const firstWithCoord = res.properties.find(
          (p) => p.latitude !== null && p.longitude !== null
        );
        if (firstWithCoord && firstWithCoord.latitude && firstWithCoord.longitude) {
          mapRef.current.flyTo({
            center: [firstWithCoord.longitude, firstWithCoord.latitude],
            zoom: muni ? 13.5 : 10.5,
          });
        }
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleLiveFetch() {
    setLiveSearching(true);
    setStatusMessage(
      "スーモ・ヤフー不動産・ニフティ不動産および国土地理院へリアルタイム照会中..."
    );
    try {
      const res = await triggerLiveSearch({
        prefecture: selectedPrefCode,
        municipality: selectedMuniName || undefined,
        category: selectedCategory,
        keyword: keyword || undefined,
        agent_name: "人間",
      });
      setProperties(res.properties);
      const st = await fetchStats();
      setStats(st);
      setStatusMessage(
        `横断取得完了：生データ ${res.fetched_raw_count} 件を取得（新規登録 ${res.upsert_summary.inserted} 件・複数サイト名寄せ統合 ${res.upsert_summary.merged_multi_portal} 件）`
      );
      if (res.properties.length > 0 && mapRef.current) {
        const first = res.properties[0];
        if (first.latitude && first.longitude) {
          mapRef.current.flyTo({
            center: [first.longitude, first.latitude],
            zoom: selectedMuniName ? 13.5 : 11,
          });
        }
      }
    } finally {
      setLiveSearching(false);
    }
  }

  async function handlePolygonFilter() {
    if (polygonPoints.length < 3) {
      setStatusMessage("地図上で3点以上の頂点をクリックして多角形範囲を指定してください");
      return;
    }
    setLoading(true);
    try {
      const res = await runPolygonSearch({
        polygon: polygonPoints,
        category: selectedCategory !== "すべて" ? selectedCategory : undefined,
        only_undervalued: onlyUndervalued,
        sort_by: sortBy,
      });
      setProperties(res.properties);
      setStatusMessage(`指定した多角形空間範囲内から ${res.count} 件の物件を抽出しました`);
    } finally {
      setLoading(false);
    }
  }

  async function handleSelectProperty(prop: PropertyItem) {
    const full = await fetchPropertyDetail(prop.id);
    setSelectedProperty(full);
    setSpatialAnalysis(null);
    setRightSubTab("detail");

    if (full.latitude && full.longitude && mapRef.current) {
      mapRef.current.flyTo({
        center: [full.longitude, full.latitude],
        zoom: 15.5,
      });
    }

    // 選択状態をエージェント共有コンテキストへ即時同期
    await updateUISync("selected_property_ids", [full.id]);
  }

  async function handleAnalyzeSpatial(propertyId: number) {
    setAnalyzingSpatial(true);
    try {
      const res = await fetchSpatialAnalysis(propertyId);
      setSpatialAnalysis(res);
    } finally {
      setAnalyzingSpatial(false);
    }
  }

  async function handleSaveEvaluation(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedProperty) return;
    await submitAgentEvaluation(selectedProperty.id, {
      agent_name: evalAgent,
      score: evalScore,
      recommendation: evalRec,
      pros: evalPros,
      cons: evalCons,
      notes: evalNotes,
    });
    const updated = await fetchPropertyDetail(selectedProperty.id);
    setSelectedProperty(updated);
    setEvalPros("");
    setEvalCons("");
    setEvalNotes("");
    setStatusMessage(`物件番号 ${selectedProperty.id} に評価所見を記録しました`);
  }

  async function handleAddWatchlist(prop: PropertyItem) {
    await addToWatchlistApi({
      property_id: prop.id,
      watchlist_name: "重点調査案件リスト",
      status: "調査中",
      memo: `${prop.category}・${formatYenJP(prop.price)}`,
      added_by: "人間",
    });
    const wls = await fetchWatchlists();
    setWatchlists(wls);
    setStatusMessage(`物件番号 ${prop.id} を「重点調査案件リスト」に追加しました`);
  }

  function toggleCompareId(id: number) {
    setCompareIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
  }

  async function handleGenerateCompareReport() {
    if (compareIds.length === 0) return;
    const res = await comparePropertiesApi(compareIds);
    setComparisonReport(res.markdown_report);
    setMainTab("watchlist");
  }

  async function handleRunVolumeSim() {
    const res = await simulateVolumeApi({
      land_area_sqm: volLandArea,
      building_coverage_ratio: volCoverage,
      floor_area_ratio: volFar,
      road_width_m: volRoadWidth,
      zoning_category: volZoning,
      corner_lot_relaxation: volCorner,
      structure_type: volStructure,
    });
    setVolResult(res.result);
  }

  async function handleRunYieldSim() {
    const res = await simulateYieldApi({
      price_yen: Math.round(yldPriceMan * 10_000),
      expected_monthly_rent_yen: Math.round(yldMonthlyRentMan * 10_000),
      renovation_or_build_cost_yen: Math.round(yldBuildCostMan * 10_000),
      down_payment_yen: Math.round(yldDownPaymentMan * 10_000),
      loan_interest_rate_pct: yldInterest,
      loan_years: yldYears,
    });
    setYldResult(res.summary);
  }

  function applySelectedPropertyToSimulator(prop: PropertyItem) {
    if (prop.land_area_sqm) setVolLandArea(prop.land_area_sqm);
    if (prop.building_coverage_ratio) setVolCoverage(prop.building_coverage_ratio);
    if (prop.floor_area_ratio) setVolFar(prop.floor_area_ratio);
    setYldPriceMan(Math.round(prop.price / 10_000));
    if (prop.monthly_rent) {
      setYldMonthlyRentMan( Number((prop.monthly_rent / 10_000).toFixed(1)) );
    } else if (prop.estimated_gross_yield_pct) {
      const estAnnual = prop.price * (prop.estimated_gross_yield_pct / 100);
      setYldMonthlyRentMan(Number((estAnnual / 12 / 10_000).toFixed(1)));
    }
    setMainTab("simulator");
  }

  function getCategoryBadgeClass(cat: string): string {
    if (cat === "売地") return "prop-badge badge-land";
    if (cat === "マンション") return "prop-badge badge-condo";
    if (cat === "賃貸") return "prop-badge badge-rent";
    return "prop-badge badge-house";
  }

  return (
    <>
      {/* トップヘッダー */}
      <header className="app-header">
        <div className="brand-group">
          <div className="brand-title">全国不動産・土地網羅検索システム</div>
          <div className="brand-subtitle">
            クロード・コーデックス・アンチグラビティ ネイティブ連携基盤
          </div>
        </div>

        <nav className="nav-tabs">
          <button
            type="button"
            className={`nav-tab ${mainTab === "search" ? "active" : ""}`}
            onClick={() => setMainTab("search")}
          >
            地図・一覧統合検索
          </button>
          <button
            type="button"
            className={`nav-tab ${mainTab === "cad3d" ? "active" : ""}`}
            onClick={() => setMainTab("cad3d")}
          >
            平面図・立体モデル設計
          </button>
          <button
            type="button"
            className={`nav-tab ${mainTab === "simulator" ? "active" : ""}`}
            onClick={() => setMainTab("simulator")}
          >
            建築・収益シミュレーター
          </button>
          <button
            type="button"
            className={`nav-tab ${mainTab === "watchlist" ? "active" : ""}`}
            onClick={() => setMainTab("watchlist")}
          >
            案件管理・比較レポート（{compareIds.length}件選択）
          </button>
          <button
            type="button"
            className={`nav-tab ${mainTab === "agent" ? "active" : ""}`}
            onClick={() => setMainTab("agent")}
          >
            エージェント連携案内
          </button>
        </nav>

        <div className="header-stats">
          <div className="stat-pill">
            蓄積物件総数<strong>{stats?.total_properties ?? 0}件</strong>
          </div>
          <div className="stat-pill">
            名寄せ統合<strong>{stats?.multi_portal_merged_count ?? 0}件</strong>
          </div>
          <div className={`sync-indicator ${wsConnected ? "online" : "offline"}`}>
            <span className="sync-dot" />
            {wsConnected ? "エージェント同期：接続中" : "エージェント同期：再接続中"}
          </div>
        </div>
      </header>

      {/* タブ1：地図・一覧統合検索 */}
      {mainTab === "search" && (
        <div className="workspace-grid">
          {/* 左パネル：検索条件とリアルタイム収集 */}
          <aside className="left-sidebar">
            <div className="panel-scroll">
              <div className="section-box">
                <div className="section-title">
                  <span>対象地域・種別指定</span>
                </div>

                <div className="form-row">
                  <label className="form-label">都道府県（全国47都道府県対応）</label>
                  <select
                    className="form-select"
                    value={selectedPrefCode}
                    onChange={(e) => {
                      setSelectedPrefCode(e.target.value);
                      setSelectedMuniName("");
                    }}
                  >
                    {prefectures.map((p) => (
                      <option key={p.code} value={p.code}>
                        {p.name}（{p.region}）
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-row">
                  <label className="form-label">
                    市区町村（国土地理院公式 {municipalities.length} 自治体）
                  </label>
                  <select
                    className="form-select"
                    value={selectedMuniName}
                    onChange={(e) => setSelectedMuniName(e.target.value)}
                  >
                    <option value="">すべての市区町村</option>
                    {municipalities.map((m) => (
                      <option key={m.muni_code} value={m.muni_name}>
                        {m.muni_name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-row">
                  <label className="form-label">物件種別</label>
                  <select
                    className="form-select"
                    value={selectedCategory}
                    onChange={(e) => setSelectedCategory(e.target.value)}
                  >
                    <option value="すべて">すべて（全種別横断）</option>
                    <option value="売地">売地（宅地・建築条件付・更地）</option>
                    <option value="戸建て">戸建て（中古戸建・新築戸建）</option>
                    <option value="マンション">マンション（区分所有）</option>
                    <option value="空き家・古民家">空き家・古民家・地方再生</option>
                    <option value="収益物件">収益物件・投資対象</option>
                    <option value="賃貸">賃貸（居住用・戸建・マンション）</option>
                  </select>
                </div>

                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={liveSearching}
                  onClick={handleLiveFetch}
                  style={{ marginBottom: "8px" }}
                >
                  {liveSearching
                    ? "各ポータルからリアルタイム収集中..."
                    : "最新物件を複数サイトから横断取得"}
                </button>

                <button
                  type="button"
                  className="btn btn-secondary"
                  style={{ width: "100%" }}
                  disabled={loading}
                  onClick={() => executeLocalQuery()}
                >
                  蓄積データベース内を即時検索
                </button>
              </div>

              <div className="section-box">
                <div className="section-title">
                  <span>詳細条件・割安度フィルタ</span>
                </div>

                <div className="form-grid-2 form-row">
                  <div>
                    <label className="form-label">下限価格（万円）</label>
                    <input
                      type="number"
                      className="form-input"
                      placeholder="下限なし"
                      value={minPriceMan}
                      onChange={(e) => setMinPriceMan(e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="form-label">上限価格（万円）</label>
                    <input
                      type="number"
                      className="form-input"
                      placeholder="上限なし"
                      value={maxPriceMan}
                      onChange={(e) => setMaxPriceMan(e.target.value)}
                    />
                  </div>
                </div>

                <div className="form-grid-2 form-row">
                  <div>
                    <label className="form-label">最小土地面積（平米）</label>
                    <input
                      type="number"
                      className="form-input"
                      placeholder="指定なし"
                      value={minLandArea}
                      onChange={(e) => setMinLandArea(e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="form-label">駅徒歩上限（分）</label>
                    <input
                      type="number"
                      className="form-input"
                      placeholder="指定なし"
                      value={maxWalkMin}
                      onChange={(e) => setMaxWalkMin(e.target.value)}
                    />
                  </div>
                </div>

                <div className="form-row">
                  <label className="form-label">自由語句（町名・駅名・特徴）</label>
                  <input
                    type="text"
                    className="form-input"
                    placeholder="例：赤坂、白金台、角地、整形地"
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                  />
                </div>

                <div className="form-row">
                  <label className="form-label">並び順</label>
                  <select
                    className="form-select"
                    value={sortBy}
                    onChange={(e) => setSortBy(e.target.value)}
                  >
                    <option value="price_asc">価格が安い順</option>
                    <option value="price_desc">価格が高い順</option>
                    <option value="tsubo_asc">坪単価が安い順</option>
                    <option value="deviation_asc">周辺相場より割安な順</option>
                    <option value="area_desc">面積が広い順</option>
                    <option value="yield_desc">想定表面利回りが高い順</option>
                    <option value="walk_asc">駅から近い順</option>
                  </select>
                </div>

                <div className="form-row" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <input
                    id="chk-undervalued"
                    type="checkbox"
                    checked={onlyUndervalued}
                    onChange={(e) => setOnlyUndervalued(e.target.checked)}
                  />
                  <label htmlFor="chk-undervalued" style={{ fontSize: "12.5px", cursor: "pointer" }}>
                    周辺相場より5パーセント以上割安な物件のみ表示
                  </label>
                </div>

                <button
                  type="button"
                  className="btn btn-emerald"
                  style={{ width: "100%" }}
                  onClick={() => executeLocalQuery()}
                >
                  絞り込み条件を適用
                </button>
              </div>

              <div className="section-box">
                <div className="section-title">
                  <span>空間ポリゴン（多角形）範囲検索</span>
                </div>
                <p style={{ fontSize: "11.5px", color: "var(--text-muted)", marginBottom: "8px" }}>
                  地図上をクリックして頂点を打ち、任意の境界線内にある土地・不動産だけを空間抽出します。
                </p>
                <div className="form-grid-2" style={{ marginBottom: "8px" }}>
                  <button
                    type="button"
                    className={`btn ${polygonMode ? "btn-amber" : "btn-secondary"}`}
                    onClick={() => setPolygonMode((v) => !v)}
                  >
                    {polygonMode ? "頂点入力中（停止）" : "多角形描画を開始"}
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => {
                      setPolygonPoints([]);
                      executeLocalQuery();
                    }}
                  >
                    範囲を消去
                  </button>
                </div>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={polygonPoints.length < 3}
                  onClick={handlePolygonFilter}
                >
                  多角形内の物件を抽出（頂点数：{polygonPoints.length}）
                </button>
              </div>
            </div>
          </aside>

          {/* 中央：国土地理院地図 ＋ ハザードマップ重ね合わせステージ */}
          <main className="map-stage">
            <div className="map-floating-controls">
              <div className="map-control-card">
                <span style={{ color: "var(--text-muted)", fontWeight: 700 }}>背景地図：</span>
                {baseLayers.map((bl) => (
                  <button
                    key={bl.id}
                    type="button"
                    className={`layer-chip ${activeBaseLayer === bl.id ? "active" : ""}`}
                    onClick={() => setActiveBaseLayer(bl.id)}
                  >
                    {bl.name.replace("国土地理院 ", "")}
                  </button>
                ))}
              </div>

              <div className="map-control-card">
                <span style={{ color: "#fda4af", fontWeight: 700 }}>災害リスク重ね合わせ：</span>
                {hazardLayers.map((hl) => (
                  <button
                    key={hl.id}
                    type="button"
                    className={`layer-chip ${activeHazards[hl.id] ? "hazard-active" : ""}`}
                    onClick={() =>
                      setActiveHazards((prev) => ({
                        ...prev,
                        [hl.id]: !prev[hl.id],
                      }))
                    }
                  >
                    {hl.name.replace("区域", "").replace("（想定最大規模）", "")}
                  </button>
                ))}
              </div>
            </div>

            <div ref={mapContainerRef} className="map-container" />

            <div className="map-legend-bar">
              <div className="legend-item">
                <span className="legend-dot" style={{ background: "#10b981" }} />
                <span>周辺相場より割安（10パーセント以上低廉）</span>
              </div>
              <div className="legend-item">
                <span className="legend-dot" style={{ background: "#f59e0b" }} />
                <span>エージェント注目物件</span>
              </div>
              <div className="legend-item">
                <span className="legend-dot" style={{ background: "#0ea5e9" }} />
                <span>現在選択中</span>
              </div>
              <div className="legend-item">
                <span className="legend-dot" style={{ background: "#4f46e5" }} />
                <span>通常掲載物件</span>
              </div>
              <div style={{ marginLeft: "10px", color: "var(--text-muted)" }}>
                地図資料：国土地理院・国土交通省ハザードマップポータルサイト
              </div>
            </div>
          </main>

          {/* 右パネル：物件一覧・詳細解析・エージェント活動ログ */}
          <aside className="right-sidebar">
            <div
              style={{
                padding: "8px 12px",
                background: "#090d16",
                borderBottom: "1px solid var(--border-color)",
                fontSize: "11.5px",
                color: "var(--accent-blue)",
              }}
            >
              状態：{statusMessage}
            </div>

            <div className="sub-tabs">
              <button
                type="button"
                className={`sub-tab ${rightSubTab === "list" ? "active" : ""}`}
                onClick={() => setRightSubTab("list")}
              >
                検索結果一覧（{properties.length}件）
              </button>
              <button
                type="button"
                className={`sub-tab ${rightSubTab === "detail" ? "active" : ""}`}
                onClick={() => setRightSubTab("detail")}
              >
                物件詳細・空間解析
              </button>
              <button
                type="button"
                className={`sub-tab ${rightSubTab === "activity" ? "active" : ""}`}
                onClick={() => setRightSubTab("activity")}
              >
                エージェント活動履歴
              </button>
            </div>

            {/* サブタブ1：物件一覧 */}
            {rightSubTab === "list" && (
              <div className="panel-scroll">
                {compareIds.length > 0 && (
                  <div
                    className="section-box"
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <span style={{ fontSize: "12px" }}>
                      比較対象として <strong>{compareIds.length}件</strong> 選択中
                    </span>
                    <button
                      type="button"
                      className="btn btn-emerald"
                      onClick={handleGenerateCompareReport}
                    >
                      比較分析レポートを作成
                    </button>
                  </div>
                )}

                {properties.map((prop) => {
                  const isSelected = selectedProperty?.id === prop.id;
                  const isHighlighted = (
                    uiSyncState?.highlighted_property_ids || []
                  ).includes(prop.id);

                  return (
                    <div
                      key={prop.id}
                      className={`property-card ${isSelected ? "selected" : ""} ${
                        isHighlighted ? "agent-highlighted" : ""
                      }`}
                      onClick={() => handleSelectProperty(prop)}
                    >
                      <div className="prop-card-header">
                        <div>
                          <span className={getCategoryBadgeClass(prop.category)}>
                            {prop.category}
                          </span>
                          <span
                            style={{
                              fontSize: "11px",
                              color: "var(--text-muted)",
                              marginLeft: "6px",
                            }}
                          >
                            物件番号：{prop.id}
                          </span>
                        </div>
                        <div>
                          {prop.market_deviation_pct !== null && (
                            <span
                              className={`deviation-tag ${
                                prop.market_deviation_pct < 0
                                  ? "deviation-good"
                                  : "deviation-high"
                              }`}
                            >
                              周辺相場比{" "}
                              {prop.market_deviation_pct > 0
                                ? `+${prop.market_deviation_pct}`
                                : prop.market_deviation_pct}
                              パーセント
                            </span>
                          )}
                        </div>
                      </div>

                      <div style={{ marginBottom: "4px" }}>
                        <span className="prop-price">{formatYenJP(prop.price)}</span>
                        {prop.price_per_tsubo && (
                          <span className="prop-tsubo">
                            （坪単価：{formatYenJP(prop.price_per_tsubo)}）
                          </span>
                        )}
                      </div>

                      <div className="prop-title">
                        {toPureJapaneseText(prop.title)}
                      </div>

                      <div className="prop-meta-grid">
                        <div>所在地：{prop.address}</div>
                        <div>
                          最寄駅：
                          {prop.nearest_station
                            ? `${prop.nearest_station}駅 徒歩${prop.station_walk_min ?? "-"}分`
                            : "記載なし"}
                        </div>
                        <div>
                          土地面積：
                          {prop.land_area_sqm
                            ? `${prop.land_area_sqm}平米（${prop.land_area_tsubo}坪）`
                            : "対象外"}
                        </div>
                        <div>
                          建物・専有：
                          {prop.building_area_sqm
                            ? `${prop.building_area_sqm}平米`
                            : formatFloorPlanJP(prop.floor_plan)}
                        </div>
                      </div>

                      <div
                        style={{
                          marginTop: "8px",
                          display: "flex",
                          justifyContent: "space-between",
                          alignItems: "center",
                        }}
                        onClick={(ev) => ev.stopPropagation()}
                      >
                        <div>
                          {(prop.source_sites || [prop.primary_source_site]).map(
                            (s) => (
                              <span key={s} className="portal-tag">
                                {s}
                              </span>
                            )
                          )}
                        </div>

                        <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
                          <label
                            style={{
                              fontSize: "11.5px",
                              color: "var(--text-secondary)",
                              cursor: "pointer",
                              display: "flex",
                              alignItems: "center",
                              gap: "4px",
                            }}
                          >
                            <input
                              type="checkbox"
                              checked={compareIds.includes(prop.id)}
                              onChange={() => toggleCompareId(prop.id)}
                            />
                            比較選択
                          </label>
                          <button
                            type="button"
                            className="layer-chip"
                            onClick={() => handleAddWatchlist(prop)}
                          >
                            案件保存
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* サブタブ2：物件詳細・空間解析・エージェント評価 */}
            {rightSubTab === "detail" && (
              <div className="panel-scroll">
                {!selectedProperty ? (
                  <div className="section-box">
                    地図上のピンまたは検索結果一覧から物件を選択してください。
                  </div>
                ) : (
                  <>
                    <div className="section-box">
                      <div className="prop-card-header">
                        <span className={getCategoryBadgeClass(selectedProperty.category)}>
                          {selectedProperty.category}（{selectedProperty.transaction_type}）
                        </span>
                        <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                          物件番号：{selectedProperty.id}
                        </span>
                      </div>

                      <h3 style={{ fontSize: "15px", margin: "6px 0" }}>
                        {toPureJapaneseText(selectedProperty.title)}
                      </h3>

                      <div style={{ marginBottom: "10px" }}>
                        <span className="prop-price">
                          {formatYenJP(selectedProperty.price)}
                        </span>
                        {selectedProperty.price_per_tsubo && (
                          <span className="prop-tsubo">
                            坪単価：{formatYenJP(selectedProperty.price_per_tsubo)} ／ 平米単価：
                            {formatYenJP(selectedProperty.price_per_sqm)}
                          </span>
                        )}
                      </div>

                      <table className="kv-table">
                        <tbody>
                          <tr>
                            <th>所在地</th>
                            <td>{selectedProperty.address}</td>
                          </tr>
                          <tr>
                            <th>最寄駅・交通</th>
                            <td>
                              {selectedProperty.nearest_station
                                ? `${selectedProperty.nearest_station}駅 徒歩${
                                    selectedProperty.station_walk_min ?? "-"
                                  }分`
                                : "記載なし"}
                            </td>
                          </tr>
                          <tr>
                            <th>土地面積</th>
                            <td>
                              {selectedProperty.land_area_sqm
                                ? `${selectedProperty.land_area_sqm}平米（${selectedProperty.land_area_tsubo}坪）`
                                : "記載なし"}
                            </td>
                          </tr>
                          <tr>
                            <th>建物・専有面積</th>
                            <td>
                              {selectedProperty.building_area_sqm
                                ? `${selectedProperty.building_area_sqm}平米（${selectedProperty.building_area_tsubo}坪）`
                                : "記載なし"}
                            </td>
                          </tr>
                          <tr>
                            <th>間取り・築年</th>
                            <td>
                              {formatFloorPlanJP(selectedProperty.floor_plan)} ／{" "}
                              {selectedProperty.build_year
                                ? `${selectedProperty.build_year}年築`
                                : "築年未記載"}
                            </td>
                          </tr>
                          <tr>
                            <th>建蔽率・容積率</th>
                            <td>
                              {selectedProperty.building_coverage_ratio
                                ? `建蔽率 ${selectedProperty.building_coverage_ratio}パーセント ／ 容積率 ${
                                    selectedProperty.floor_area_ratio ?? "-"
                                  }パーセント`
                                : "現地・公図要確認"}
                            </td>
                          </tr>
                          <tr>
                            <th>周辺相場乖離率</th>
                            <td>
                              {selectedProperty.market_deviation_pct !== null
                                ? `${selectedProperty.market_deviation_pct}パーセント（マイナスは周辺平均より割安）`
                                : "算出対象外"}
                            </td>
                          </tr>
                          <tr>
                            <th>想定表面利回り</th>
                            <td>
                              {selectedProperty.estimated_gross_yield_pct
                                ? `${selectedProperty.estimated_gross_yield_pct}パーセント`
                                : "対象外"}
                            </td>
                          </tr>
                          <tr>
                            <th>測地座標・標高</th>
                            <td>
                              北緯 {selectedProperty.latitude?.toFixed(5)}度 ／ 東経{" "}
                              {selectedProperty.longitude?.toFixed(5)}度
                              {selectedProperty.elevation_m !== null &&
                                `（標高 ${selectedProperty.elevation_m}メートル）`}
                            </td>
                          </tr>
                        </tbody>
                      </table>

                      <div className="form-grid-2" style={{ marginBottom: "8px" }}>
                        <button
                          type="button"
                          className="btn btn-primary"
                          disabled={analyzingSpatial}
                          onClick={() => handleAnalyzeSpatial(selectedProperty.id)}
                        >
                          {analyzingSpatial
                            ? "国土地理院・周辺環境を解析中..."
                            : "標高・災害リスク・周辺施設を精密解析"}
                        </button>
                        <button
                          type="button"
                          className="btn btn-emerald"
                          onClick={() => applySelectedPropertyToSimulator(selectedProperty)}
                        >
                          建築・収益試算へ数値を転送
                        </button>
                      </div>

                      <div style={{ marginBottom: "8px" }}>
                        <button
                          type="button"
                          className="btn btn-amber"
                          style={{ width: "100%" }}
                          onClick={() => setMainTab("cad3d")}
                        >
                          この物件の詳細平面図・立体建築モデル・周辺地形を開く
                        </button>
                      </div>

                      <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                        {Object.entries(selectedProperty.source_urls || {}).map(
                          ([siteName, url]) => (
                            <a
                              key={siteName}
                              href={url}
                              target="_blank"
                              rel="noreferrer"
                              className="btn btn-secondary"
                              style={{ textDecoration: "none", fontSize: "12px" }}
                            >
                              {siteName}の元記事を確認
                            </a>
                          )
                        )}
                      </div>
                    </div>

                    {/* 空間・ハザード・周辺施設リアルタイム解析結果 */}
                    {spatialAnalysis && (
                      <div className="section-box">
                        <div className="section-title">
                          <span>国土地理院標高・ハザードマップ・周辺施設解析結果</span>
                        </div>
                        <p style={{ fontSize: "12.5px", marginBottom: "8px", color: "#34d399" }}>
                          {spatialAnalysis.hazard_and_topography.terrain_evaluation}
                        </p>

                        <table className="kv-table">
                          <tbody>
                            {Object.entries(
                              spatialAnalysis.hazard_and_topography.hazard_layers
                            ).map(([k, v]) => (
                              <tr key={k}>
                                <th>{v.layer_name}</th>
                                <td
                                  style={{
                                    color: v.area_has_designated_zone
                                      ? "#fda4af"
                                      : "#34d399",
                                  }}
                                >
                                  {v.judgment}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>

                        <div
                          style={{
                            fontSize: "12px",
                            fontWeight: 700,
                            marginBottom: "6px",
                            color: "var(--text-secondary)",
                          }}
                        >
                          半径800メートル圏内の実在生活利便施設（
                          {spatialAnalysis.surrounding_facilities.length}件）
                        </div>
                        <div style={{ maxHeight: "170px", overflowY: "auto" }}>
                          <table className="kv-table">
                            <tbody>
                              {spatialAnalysis.surrounding_facilities.slice(0, 12).map((f, idx) => (
                                <tr key={`${f.name}-${idx}`}>
                                  <th>{f.type}</th>
                                  <td>
                                    {toPureJapaneseText(f.name)}（直線 {f.distance_m}メートル・徒歩約
                                    {f.walk_minutes}分）
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}

                    {/* エージェント評価履歴・新規記録フォーム */}
                    <div className="section-box">
                      <div className="section-title">
                        <span>
                          エージェント評価・調査所見（
                          {selectedProperty.agent_evaluations?.length ?? 0}件）
                        </span>
                      </div>

                      {(selectedProperty.agent_evaluations || []).map((ev) => (
                        <div key={ev.id} className="activity-item">
                          <div className="activity-header">
                            <span>
                              担当：{ev.agent_name} ／ 評価点：{ev.score}点（{ev.recommendation}）
                            </span>
                          </div>
                          {ev.pros && <div>強み：{ev.pros}</div>}
                          {ev.cons && <div>懸念点：{ev.cons}</div>}
                          {ev.notes && <div>所見：{ev.notes}</div>}
                        </div>
                      ))}

                      <form onSubmit={handleSaveEvaluation} style={{ marginTop: "10px" }}>
                        <div className="form-grid-2 form-row">
                          <div>
                            <label className="form-label">記録者・エージェント</label>
                            <select
                              className="form-select"
                              value={evalAgent}
                              onChange={(e) => setEvalAgent(e.target.value)}
                            >
                              <option value="アンチグラビティ">アンチグラビティ</option>
                              <option value="クロード">クロード</option>
                              <option value="コーデックス">コーデックス</option>
                              <option value="人間">人間（担当者）</option>
                            </select>
                          </div>
                          <div>
                            <label className="form-label">総合判定・評価点（0〜100点）</label>
                            <div style={{ display: "flex", gap: "6px" }}>
                              <input
                                type="number"
                                min={0}
                                max={100}
                                className="form-input"
                                value={evalScore}
                                onChange={(e) => setEvalScore(Number(e.target.value))}
                              />
                              <select
                                className="form-select"
                                value={evalRec}
                                onChange={(e) => setEvalRec(e.target.value)}
                              >
                                <option value="強く推奨">強く推奨</option>
                                <option value="推奨">推奨</option>
                                <option value="要検討">要検討</option>
                                <option value="見送り">見送り</option>
                              </select>
                            </div>
                          </div>
                        </div>

                        <div className="form-row">
                          <label className="form-label">強み・立地優位性</label>
                          <input
                            type="text"
                            className="form-input"
                            placeholder="例：駅徒歩5分・周辺坪単価より20パーセント割安"
                            value={evalPros}
                            onChange={(e) => setEvalPros(e.target.value)}
                          />
                        </div>

                        <div className="form-row">
                          <label className="form-label">懸念点・現地確認事項</label>
                          <input
                            type="text"
                            className="form-input"
                            placeholder="例：前面道路幅員および境界確定書の確認が必要"
                            value={evalCons}
                            onChange={(e) => setEvalCons(e.target.value)}
                          />
                        </div>

                        <div className="form-row">
                          <label className="form-label">詳細調査メモ</label>
                          <textarea
                            rows={2}
                            className="form-textarea"
                            placeholder="エージェントおよび担当者間で共有する調査メモ"
                            value={evalNotes}
                            onChange={(e) => setEvalNotes(e.target.value)}
                          />
                        </div>

                        <button type="submit" className="btn btn-primary">
                          評価・調査所見を保存して全体共有
                        </button>
                      </form>
                    </div>
                  </>
                )}
              </div>
            )}

            {/* サブタブ3：エージェント活動履歴 */}
            {rightSubTab === "activity" && (
              <div className="panel-scroll">
                <div className="section-box">
                  <div className="section-title">
                    <span>クロード・コーデックス・アンチグラビティ リアルタイム連携履歴</span>
                  </div>
                  {(uiSyncState?.agent_activity_log || []).length === 0 ? (
                    <div style={{ fontSize: "12.5px", color: "var(--text-muted)" }}>
                      まだ活動履歴がありません。
                    </div>
                  ) : (
                    (uiSyncState?.agent_activity_log || []).map((act, idx) => (
                      <div key={idx} className="activity-item">
                        <div className="activity-header">
                          <span>
                            {act.agent_name}｜{act.action}
                          </span>
                        </div>
                        <div style={{ marginBottom: "4px" }}>{act.summary}</div>
                        {act.highlighted_property_ids.length > 0 && (
                          <div style={{ display: "flex", gap: "4px", flexWrap: "wrap" }}>
                            {act.highlighted_property_ids.slice(0, 6).map((pid) => (
                              <button
                                key={pid}
                                type="button"
                                className="layer-chip"
                                onClick={async () => {
                                  const p = await fetchPropertyDetail(pid);
                                  handleSelectProperty(p);
                                }}
                              >
                                物件番号 {pid} を開く
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>
            )}
          </aside>
        </div>
      )}

      {/* タブ2：平面図・立体モデル設計スタジオ */}
      {mainTab === "cad3d" && (
        <Cad3DStudio
          selectedProperty={selectedProperty}
          properties={properties}
          onSelectProperty={handleSelectProperty}
          onStatusChange={setStatusMessage}
        />
      )}

      {/* タブ3：建築ボリューム・投資収益シミュレーター */}
      {mainTab === "simulator" && (
        <div className="full-page-view">
          <div className="sim-grid">
            {/* 建築ボリューム・最大延床面積シミュレーター */}
            <div className="section-box">
              <div className="section-title">
                <span>建築基準法準拠・土地建築ボリュームおよび概算建築費試算</span>
              </div>

              <div className="form-grid-2 form-row">
                <div>
                  <label className="form-label">敷地面積（平米）</label>
                  <input
                    type="number"
                    className="form-input"
                    value={volLandArea}
                    onChange={(e) => setVolLandArea(Number(e.target.value))}
                  />
                </div>
                <div>
                  <label className="form-label">前面道路幅員（メートル）</label>
                  <input
                    type="number"
                    step="0.1"
                    className="form-input"
                    value={volRoadWidth}
                    onChange={(e) => setVolRoadWidth(Number(e.target.value))}
                  />
                </div>
              </div>

              <div className="form-grid-2 form-row">
                <div>
                  <label className="form-label">指定建蔽率（パーセント）</label>
                  <input
                    type="number"
                    className="form-input"
                    value={volCoverage}
                    onChange={(e) => setVolCoverage(Number(e.target.value))}
                  />
                </div>
                <div>
                  <label className="form-label">指定容積率（パーセント）</label>
                  <input
                    type="number"
                    className="form-input"
                    value={volFar}
                    onChange={(e) => setVolFar(Number(e.target.value))}
                  />
                </div>
              </div>

              <div className="form-grid-2 form-row">
                <div>
                  <label className="form-label">用途地域区分（道路斜線・容積率係数）</label>
                  <select
                    className="form-select"
                    value={volZoning}
                    onChange={(e) => setVolZoning(e.target.value)}
                  >
                    <option value="住居系">住居系用途地域（道路幅員×40パーセント制限）</option>
                    <option value="商業・工業系">
                      商業・工業系用途地域（道路幅員×60パーセント制限）
                    </option>
                  </select>
                </div>
                <div>
                  <label className="form-label">想定建築構造</label>
                  <select
                    className="form-select"
                    value={volStructure}
                    onChange={(e) => setVolStructure(e.target.value)}
                  >
                    <option value="木造">木造（概算坪単価95万円）</option>
                    <option value="重量鉄骨造">重量鉄骨造（概算坪単価125万円）</option>
                    <option value="鉄筋コンクリート造">
                      鉄筋コンクリート造（概算坪単価145万円）
                    </option>
                  </select>
                </div>
              </div>

              <div className="form-row" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <input
                  id="chk-corner"
                  type="checkbox"
                  checked={volCorner}
                  onChange={(e) => setVolCorner(e.target.checked)}
                />
                <label htmlFor="chk-corner" style={{ fontSize: "12.5px", cursor: "pointer" }}>
                  角地緩和（建蔽率＋10パーセント緩和）を適用する
                </label>
              </div>

              <button type="button" className="btn btn-primary" onClick={handleRunVolumeSim}>
                建築可能ボリュームを計算する
              </button>

              {volResult && (
                <div className="result-kpi-grid">
                  <div className="kpi-box">
                    <div className="kpi-label">実効建蔽率 ／ 実効容積率</div>
                    <div className="kpi-value">
                      {volResult.effective_coverage_ratio_pct}パーセント ／{" "}
                      {volResult.effective_floor_area_ratio_pct}パーセント
                    </div>
                  </div>
                  <div className="kpi-box">
                    <div className="kpi-label">最大建築面積（1階面積上限）</div>
                    <div className="kpi-value">
                      {volResult.max_building_footprint_sqm}平米（
                      {volResult.max_building_footprint_tsubo}坪）
                    </div>
                  </div>
                  <div className="kpi-box">
                    <div className="kpi-label">最大延床面積（総床面積上限）</div>
                    <div className="kpi-value">
                      {volResult.max_total_floor_area_sqm}平米（
                      {volResult.max_total_floor_area_tsubo}坪）
                    </div>
                  </div>
                  <div className="kpi-box">
                    <div className="kpi-label">推奨階数 ／ 想定有効率床面積</div>
                    <div className="kpi-value">
                      地上{volResult.recommended_stories}階建 ／{" "}
                      {volResult.estimated_rentable_area_sqm}平米
                    </div>
                  </div>
                  <div className="kpi-box" style={{ gridColumn: "span 2" }}>
                    <div className="kpi-label">構造別概算建築費（外構・設計費別途）</div>
                    <div className="kpi-value">
                      {formatYenJP(volResult.estimated_construction_cost_yen)}
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* 不動産投資利回り・融資収支シミュレーター */}
            <div className="section-box">
              <div className="section-title">
                <span>不動産投資・実質利回り・融資返済・税引前収支試算</span>
              </div>

              <div className="form-grid-2 form-row">
                <div>
                  <label className="form-label">物件・土地取得価格（万円）</label>
                  <input
                    type="number"
                    className="form-input"
                    value={yldPriceMan}
                    onChange={(e) => setYldPriceMan(Number(e.target.value))}
                  />
                </div>
                <div>
                  <label className="form-label">建築費・改修費（万円）</label>
                  <input
                    type="number"
                    className="form-input"
                    value={yldBuildCostMan}
                    onChange={(e) => setYldBuildCostMan(Number(e.target.value))}
                  />
                </div>
              </div>

              <div className="form-grid-2 form-row">
                <div>
                  <label className="form-label">満室想定月額賃料収入（万円）</label>
                  <input
                    type="number"
                    step="0.5"
                    className="form-input"
                    value={yldMonthlyRentMan}
                    onChange={(e) => setYldMonthlyRentMan(Number(e.target.value))}
                  />
                </div>
                <div>
                  <label className="form-label">自己資金投入額（万円）</label>
                  <input
                    type="number"
                    className="form-input"
                    value={yldDownPaymentMan}
                    onChange={(e) => setYldDownPaymentMan(Number(e.target.value))}
                  />
                </div>
              </div>

              <div className="form-grid-2 form-row">
                <div>
                  <label className="form-label">融資金利（年利・パーセント）</label>
                  <input
                    type="number"
                    step="0.1"
                    className="form-input"
                    value={yldInterest}
                    onChange={(e) => setYldInterest(Number(e.target.value))}
                  />
                </div>
                <div>
                  <label className="form-label">融資返済期間（年）</label>
                  <input
                    type="number"
                    className="form-input"
                    value={yldYears}
                    onChange={(e) => setYldYears(Number(e.target.value))}
                  />
                </div>
              </div>

              <button type="button" className="btn btn-primary" onClick={handleRunYieldSim}>
                投資利回り・収支を計算する
              </button>

              {yldResult && (
                <div className="result-kpi-grid">
                  <div className="kpi-box">
                    <div className="kpi-label">表面利回り ／ 実質利回り</div>
                    <div className="kpi-value">
                      {yldResult.gross_yield_pct}パーセント ／ {yldResult.net_yield_pct}パーセント
                    </div>
                  </div>
                  <div className="kpi-box">
                    <div className="kpi-label">総事業費（諸費用7パーセント含）</div>
                    <div className="kpi-value">
                      {formatYenJP(yldResult.total_investment_yen)}
                    </div>
                  </div>
                  <div className="kpi-box">
                    <div className="kpi-label">年間純営業収益（空室・経費控除後）</div>
                    <div className="kpi-value">{formatYenJP(yldResult.annual_noi_yen)}</div>
                  </div>
                  <div className="kpi-box">
                    <div className="kpi-label">毎月元利均等返済額</div>
                    <div className="kpi-value">
                      {formatYenJP(yldResult.monthly_loan_payment_yen)}
                    </div>
                  </div>
                  <div className="kpi-box">
                    <div className="kpi-label">税引前年間手残り収支</div>
                    <div
                      className="kpi-value"
                      style={{
                        color:
                          yldResult.annual_cash_flow_yen >= 0 ? "#34d399" : "#fda4af",
                      }}
                    >
                      {formatYenJP(yldResult.annual_cash_flow_yen)}
                    </div>
                  </div>
                  <div className="kpi-box">
                    <div className="kpi-label">自己資金配当率 ／ 回収目安年数</div>
                    <div className="kpi-value">
                      {yldResult.equity_ccr_pct !== null
                        ? `${yldResult.equity_ccr_pct}パーセント`
                        : "自己資金なし"}{" "}
                      ／ {yldResult.payback_years ?? "-"}年
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* タブ3：案件管理・比較レポート */}
      {mainTab === "watchlist" && (
        <div className="full-page-view">
          <div className="sim-grid">
            <div className="section-box">
              <div className="section-title">
                <span>重点調査案件リスト（人間・エージェント共有）</span>
              </div>
              {watchlists.map((wl) => (
                <div key={wl.id}>
                  <p
                    style={{
                      fontSize: "12px",
                      color: "var(--text-muted)",
                      marginBottom: "10px",
                    }}
                  >
                    {wl.description}（登録数：{wl.items.length}件）
                  </p>
                  {wl.items.map((it) => (
                    <div key={it.item_id} className="property-card">
                      <div className="prop-card-header">
                        <span className={getCategoryBadgeClass(it.category)}>
                          {it.category}
                        </span>
                        <span style={{ fontSize: "11.5px", color: "var(--accent-blue)" }}>
                          状態：{it.status}（追加者：{it.added_by}）
                        </span>
                      </div>
                      <div className="prop-price">{formatYenJP(it.price)}</div>
                      <div className="prop-title">{toPureJapaneseText(it.title)}</div>
                      <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
                        所在地：{it.address}
                      </div>
                      <div
                        style={{
                          marginTop: "8px",
                          display: "flex",
                          justifyContent: "space-between",
                        }}
                      >
                        <button
                          type="button"
                          className="layer-chip"
                          onClick={() => {
                            setMainTab("search");
                            handleSelectProperty(it);
                          }}
                        >
                          地図・詳細で確認
                        </button>
                        <button
                          type="button"
                          className="layer-chip"
                          onClick={async () => {
                            await removeFromWatchlistApi(wl.id, it.id);
                            setWatchlists(await fetchWatchlists());
                          }}
                        >
                          リストから除外
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </div>

            <div className="section-box">
              <div className="section-title">
                <span>複数物件横断比較分析レポート</span>
                <button
                  type="button"
                  className="btn btn-emerald"
                  onClick={handleGenerateCompareReport}
                >
                  選択中の {compareIds.length} 件でレポート更新
                </button>
              </div>
              {comparisonReport ? (
                <pre className="code-block">{comparisonReport}</pre>
              ) : (
                <p style={{ fontSize: "12.5px", color: "var(--text-muted)" }}>
                  検索結果一覧で「比較選択」にチェックを入れ、「比較分析レポートを作成」を押すとここに横断比較表とエージェント評価まとめが出力されます。
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* タブ4：エージェント連携案内 */}
      {mainTab === "agent" && (
        <div className="full-page-view">
          <div className="section-box">
            <div className="section-title">
              <span>クロード・コーデックス・アンチグラビティ ネイティブ連携状態</span>
            </div>
            <p style={{ fontSize: "13px", lineHeight: 1.7, marginBottom: "14px" }}>
              本システムは「モデルコンテキストプロトコル（標準入出力通信）」および「コマンドラインインターフェース」と「ウェブ画面双方向通信」の三層統合構成で稼働しています。クロード、コーデックス、アンチグラビティの各エージェントから直接、日本全国の不動産・土地の横断検索、国土地理院の標高・ハザードマップ照会、建築ボリューム計算、利回り試算、物件評価の書き込みを実行でき、その操作結果は現在開いているこの地図画面にもリアルタイムで反映されます。
            </p>

            <div className="result-kpi-grid" style={{ marginBottom: "16px" }}>
              <div className="kpi-box">
                <div className="kpi-label">現在画面で選択中の物件番号</div>
                <div className="kpi-value">
                  {(uiSyncState?.selected_property_ids || []).length > 0
                    ? `物件番号 ${(uiSyncState?.selected_property_ids || []).join("・")}`
                    : "未選択"}
                </div>
              </div>
              <div className="kpi-box">
                <div className="kpi-label">エージェントがハイライト中の物件数</div>
                <div className="kpi-value">
                  {(uiSyncState?.highlighted_property_ids || []).length}件
                </div>
              </div>
            </div>

            <div className="section-title">
              <span>対応しているネイティブ機能一覧</span>
            </div>
            <table className="kv-table">
              <tbody>
                <tr>
                  <th>全国リアルタイム横断検索</th>
                  <td>
                    47都道府県・約1900市区町村を指定し、スーモ・ヤフー不動産・ニフティ不動産から売地・戸建て・マンション・空き家・収益物件・賃貸を取得し、自動名寄せ統合します。
                  </td>
                </tr>
                <tr>
                  <th>国土地理院・災害リスク解析</th>
                  <td>
                    所在地座標の精密標高（レーザ測量データ）と、洪水・津波・土砂災害警戒区域の該当有無、および半径800メートル以内の生活施設距離を即時判定します。
                  </td>
                </tr>
                <tr>
                  <th>建築ボリューム・投資収支計算</th>
                  <td>
                    前面道路幅員制限を考慮した実効容積率・最大延床面積・概算建築費、および融資金利・期間に基づく実質利回り・税引前収支を計算します。
                  </td>
                </tr>
                <tr>
                  <th>詳細平面図・立体モデル・都市地形生成</th>
                  <td>
                    個別敷地の各階間取り平面図・建築基準法斜線制限・季節別日影投影モデル、および国土地理院の数値標高モデルとベクトルタイルに基づく日本全国の実在街区・等高線立体モデルを生成し、各種設計データ形式で保存します。
                  </td>
                </tr>
                <tr>
                  <th>双方向画面同期・評価記録</th>
                  <td>
                    人間が画面上で選択した物件や描画した多角形範囲をエージェントが読み取り、エージェントが記録した100点満点の評価スコアや注目物件を地図上に即時反映します。
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}

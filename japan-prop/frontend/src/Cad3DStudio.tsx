import React, { useEffect, useRef, useState } from "react";
import { generatePropertyCad3DApi, generateUrbanCad3DApi } from "./api";
import {
  ArchitecturalModelData,
  formatFloorPlanJP,
  PropertyItem,
  toPureJapaneseText,
  Urban3DSceneData,
} from "./types";

interface Cad3DStudioProps {
  selectedProperty: PropertyItem | null;
  properties: PropertyItem[];
  onSelectProperty: (prop: PropertyItem) => void;
  onStatusChange: (msg: string) => void;
}

type StudioMode = "property" | "urban";

interface JapanPresetLocation {
  label: string;
  latitude: number;
  longitude: number;
  radius_m: number;
}

const JAPAN_URBAN_PRESETS: JapanPresetLocation[] = [
  {
    label: "北海道札幌市中央区・大通〜札幌駅周辺（チ・カ・ホ直結・中枢ビジネス街区）",
    latitude: 43.0621,
    longitude: 141.3544,
    radius_m: 260,
  },
  {
    label: "東京都港区・六本木一丁目周辺（都心街区・高低差地形）",
    latitude: 35.6645,
    longitude: 139.7385,
    radius_m: 240,
  },
  {
    label: "東京都港区・赤坂周辺（台地・傾斜街区）",
    latitude: 35.6725,
    longitude: 139.7362,
    radius_m: 240,
  },
  {
    label: "東京都千代田区・丸の内周辺（超高層業務街区）",
    latitude: 35.6812,
    longitude: 139.7649,
    radius_m: 260,
  },
  {
    label: "京都府京都市・東山区清水周辺（歴史的街並み・東山斜面）",
    latitude: 34.9965,
    longitude: 135.7812,
    radius_m: 260,
  },
  {
    label: "大阪府大阪市・北区中之島周辺（水都街区・堂島川沿い）",
    latitude: 34.6937,
    longitude: 135.5012,
    radius_m: 260,
  },
  {
    label: "長野県松本市・松本城周辺（城下町・アルプス盆地地形）",
    latitude: 36.2381,
    longitude: 137.9688,
    radius_m: 260,
  },
  {
    label: "静岡県・山梨県 富士山剣ヶ峰周辺（山岳火口・高標高地形）",
    latitude: 35.3606,
    longitude: 138.7274,
    radius_m: 450,
  },
  {
    label: "福岡県福岡市・博多駅周辺（九州中枢商業街区）",
    latitude: 33.5897,
    longitude: 130.4207,
    radius_m: 260,
  },
];

export function Cad3DStudio({
  selectedProperty,
  properties,
  onSelectProperty,
  onStatusChange,
}: Cad3DStudioProps) {
  const [studioMode, setStudioMode] = useState<StudioMode>("property");

  // 個別敷地・建物設計パラメータ
  const [propId, setPropId] = useState<number | undefined>(selectedProperty?.id ?? 4);
  const [landArea, setLandArea] = useState<number>(selectedProperty?.land_area_sqm ?? 115);
  const [coverageRatio, setCoverageRatio] = useState<number>(
    selectedProperty?.building_coverage_ratio ?? 60
  );
  const [farRatio, setFarRatio] = useState<number>(selectedProperty?.floor_area_ratio ?? 200);
  const [roadWidth, setRoadWidth] = useState<number>(5.4);
  const [roadDirection, setRoadDirection] = useState<string>("南");
  const [stories, setStories] = useState<number>(2);
  const [floorPlanText, setFloorPlanText] = useState<string>(
    selectedProperty?.floor_plan ?? "4LDK"
  );

  // 個別建物レスポンスステート
  const [archModel, setArchModel] = useState<ArchitecturalModelData | null>(null);
  const [floorSvgs, setFloorSvgs] = useState<Record<string, string>>({});
  const [propDownloads, setPropDownloads] = useState<{
    svg_floor_1: string;
    dxf: string;
    obj: string;
    gltf: string;
  } | null>(null);
  const [loadingArch, setLoadingArch] = useState<boolean>(false);

  const [activeFloor, setActiveFloor] = useState<number>(1);
  const [visibleMaxFloor, setVisibleMaxFloor] = useState<number>(99);
  const [showSlantCage, setShowSlantCage] = useState<boolean>(true);
  const [showShadow, setShowShadow] = useState<boolean>(true);
  const [showRoomLabels, setShowRoomLabels] = useState<boolean>(true);
  const [solarSeason, setSolarSeason] = useState<string>("冬至");
  const [solarHour, setSolarHour] = useState<number>(12);

  // 都市・広域地形パラメータ
  const [urbanLat, setUrbanLat] = useState<number>(selectedProperty?.latitude ?? 35.6645);
  const [urbanLon, setUrbanLon] = useState<number>(selectedProperty?.longitude ?? 139.7385);
  const [urbanRadius, setUrbanRadius] = useState<number>(240);
  const [zExaggeration, setZExaggeration] = useState<number>(1.8);
  const [urbanScene, setUrbanScene] = useState<Urban3DSceneData | null>(null);
  const [urbanSvg, setUrbanSvg] = useState<string>("");
  const [urbanDownloads, setUrbanDownloads] = useState<{
    svg: string;
    dxf: string;
    obj: string;
    gltf: string;
  } | null>(null);
  const [loadingUrban, setLoadingUrban] = useState<boolean>(false);
  const [showUrbanContours, setShowUrbanContours] = useState<boolean>(true);
  const [showUrbanBuildings, setShowUrbanBuildings] = useState<boolean>(true);

  // カメラ回転・ズーム操作ステート
  const [camYawDeg, setCamYawDeg] = useState<number>(-36);
  const [camPitchDeg, setCamPitchDeg] = useState<number>(32);
  const [camZoom, setCamZoom] = useState<number>(1.0);
  const dragStateRef = useRef<{
    dragging: boolean;
    startX: number;
    startY: number;
    startYaw: number;
    startPitch: number;
  }>({
    dragging: false,
    startX: 0,
    startY: 0,
    startYaw: -36,
    startPitch: 32,
  });

  const propCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const urbanCanvasRef = useRef<HTMLCanvasElement | null>(null);

  // 選択物件が変更されたら初期値を同期
  useEffect(() => {
    if (!selectedProperty) return;
    setPropId(selectedProperty.id);
    if (selectedProperty.land_area_sqm) setLandArea(selectedProperty.land_area_sqm);
    else if (selectedProperty.building_area_sqm) setLandArea(selectedProperty.building_area_sqm);
    if (selectedProperty.building_coverage_ratio)
      setCoverageRatio(selectedProperty.building_coverage_ratio);
    if (selectedProperty.floor_area_ratio) setFarRatio(selectedProperty.floor_area_ratio);
    if (selectedProperty.floor_plan) setFloorPlanText(selectedProperty.floor_plan);
    if (selectedProperty.latitude && selectedProperty.longitude) {
      setUrbanLat(selectedProperty.latitude);
      setUrbanLon(selectedProperty.longitude);
    }
  }, [selectedProperty]);

  // 初回マウント時に個別建物モデルと都市モデルを生成
  useEffect(() => {
    handleGeneratePropertyModel(selectedProperty?.id, true);
    handleGenerateUrbanModel(
      selectedProperty?.latitude ?? 35.6645,
      selectedProperty?.longitude ?? 139.7385,
      240,
      selectedProperty?.id
    );
  }, []);

  async function handleGeneratePropertyModel(overridePropId?: number, useDbDefaults = false) {
    setLoadingArch(true);
    onStatusChange("敷地配置図・各階間取り平面図・斜線制限・日影立体モデルを計算中...");
    try {
      const targetId = overridePropId !== undefined ? overridePropId : propId;
      const res = await generatePropertyCad3DApi({
        property_id: targetId,
        land_area_sqm: useDbDefaults ? undefined : landArea,
        building_coverage_ratio: useDbDefaults ? undefined : coverageRatio,
        floor_area_ratio: useDbDefaults ? undefined : farRatio,
        road_width_m: roadWidth,
        road_direction: roadDirection,
        stories: useDbDefaults ? undefined : stories,
        floor_plan_type: useDbDefaults ? undefined : floorPlanText,
        latitude: selectedProperty?.latitude ?? urbanLat,
        longitude: selectedProperty?.longitude ?? urbanLon,
        agent_name: "人間",
      });
      setArchModel(res.model);
      setFloorSvgs(res.floor_svgs);
      setPropDownloads(res.downloads);
      setLandArea(res.model.site.land_area_sqm);
      setCoverageRatio(res.model.site.building_coverage_ratio_pct);
      setFarRatio(res.model.site.floor_area_ratio_pct);
      setStories(res.model.building.stories);
      setActiveFloor(1);
      setVisibleMaxFloor(res.model.building.stories);
      onStatusChange(
        `平面図・立体モデル生成完了：地上${res.model.building.stories}階建・延床${res.model.building.total_floor_area_sqm}平米`
      );
    } finally {
      setLoadingArch(false);
    }
  }

  async function handleGenerateUrbanModel(
    lat = urbanLat,
    lon = urbanLon,
    radius = urbanRadius,
    targetPropId?: number
  ) {
    setLoadingUrban(true);
    onStatusChange(
      "国土地理院の数値標高モデルおよびベクトルタイルから実在建物・道路・等高線を抽出中..."
    );
    try {
      const res = await generateUrbanCad3DApi({
        property_id: targetPropId,
        latitude: lat,
        longitude: lon,
        radius_m: radius,
        agent_name: "人間",
      });
      setUrbanScene(res.urban);
      setUrbanSvg(res.svg);
      setUrbanDownloads(res.downloads);
      onStatusChange(
        `日本全国・都市地形立体モデル生成完了：実在建物 ${res.urban.buildings_count} 棟・道路 ${res.urban.roads_count} 区間・等高線 ${res.urban.terrain.contours.length} 本`
      );
    } finally {
      setLoadingUrban(false);
    }
  }

  function handleCanvasMouseDown(e: React.MouseEvent<HTMLCanvasElement>) {
    dragStateRef.current = {
      dragging: true,
      startX: e.clientX,
      startY: e.clientY,
      startYaw: camYawDeg,
      startPitch: camPitchDeg,
    };
  }

  function handleCanvasMouseMove(e: React.MouseEvent<HTMLCanvasElement>) {
    if (!dragStateRef.current.dragging) return;
    const dx = e.clientX - dragStateRef.current.startX;
    const dy = e.clientY - dragStateRef.current.startY;
    const nextYaw = dragStateRef.current.startYaw + dx * 0.45;
    const nextPitch = Math.max(10, Math.min(82, dragStateRef.current.startPitch + dy * 0.35));
    setCamYawDeg(nextYaw);
    setCamPitchDeg(nextPitch);
  }

  function handleCanvasMouseUp() {
    dragStateRef.current.dragging = false;
  }

  // 個別建物立体モデルのCanvas描画
  useEffect(() => {
    const canvas = propCanvasRef.current;
    if (!canvas || !archModel || studioMode !== "property") return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    const bgGrad = ctx.createLinearGradient(0, 0, 0, height);
    bgGrad.addColorStop(0, "#090f1d");
    bgGrad.addColorStop(1, "#111c33");
    ctx.fillStyle = bgGrad;
    ctx.fillRect(0, 0, width, height);

    const [lotMinX, lotMinY, lotMaxX, lotMaxY] = archModel.site.lot_bounds;
    const lotW = archModel.site.lot_width_m;
    const lotD = archModel.site.lot_depth_m;
    const totalH = archModel.building.total_height_m;
    const cz = totalH * 0.35;

    const yaw = (camYawDeg * Math.PI) / 180.0;
    const pitch = (camPitchDeg * Math.PI) / 180.0;
    const cosY = Math.cos(yaw);
    const sinY = Math.sin(yaw);
    const cosP = Math.cos(pitch);
    const sinP = Math.sin(pitch);

    const baseScale = Math.min(width, height) / (Math.max(lotW, lotD, totalH) * 1.9);
    const scale = baseScale * camZoom;

    function projectPoint(x: number, y: number, z: number): { sx: number; sy: number; depth: number } {
      const rx = x;
      const ry = y;
      const rz = z - cz;

      const x1 = rx * cosY - ry * sinY;
      const y1 = rx * sinY + ry * cosY;
      const z1 = rz;

      const y2 = y1 * cosP - z1 * sinP;
      const z2 = y1 * sinP + z1 * cosP;

      return {
        sx: width * 0.5 + x1 * scale,
        sy: height * 0.56 - z2 * scale,
        depth: y2,
      };
    }

    function drawPolygon3D(
      pts: [number, number, number][],
      fillStyle: string,
      strokeStyle: string,
      lineWidth = 1.0,
      dash: number[] = []
    ) {
      if (pts.length === 0) return;
      ctx!.save();
      ctx!.beginPath();
      ctx!.setLineDash(dash);
      pts.forEach((pt, idx) => {
        const p = projectPoint(pt[0], pt[1], pt[2]);
        if (idx === 0) ctx!.moveTo(p.sx, p.sy);
        else ctx!.lineTo(p.sx, p.sy);
      });
      ctx!.closePath();
      if (fillStyle !== "none") {
        ctx!.fillStyle = fillStyle;
        ctx!.fill();
      }
      if (strokeStyle !== "none") {
        ctx!.strokeStyle = strokeStyle;
        ctx!.lineWidth = lineWidth;
        ctx!.stroke();
      }
      ctx!.restore();
    }

    // 1. 周囲地面グリッド描画
    const gridMargin = 5.0;
    for (let gx = Math.floor(lotMinX - gridMargin); gx <= Math.ceil(lotMaxX + gridMargin); gx += 2) {
      const p1 = projectPoint(gx, lotMinY - gridMargin, 0);
      const p2 = projectPoint(gx, lotMaxY + gridMargin, 0);
      ctx.strokeStyle = "rgba(51, 65, 85, 0.35)";
      ctx.lineWidth = 0.7;
      ctx.beginPath();
      ctx.moveTo(p1.sx, p1.sy);
      ctx.lineTo(p2.sx, p2.sy);
      ctx.stroke();
    }
    for (let gy = Math.floor(lotMinY - gridMargin); gy <= Math.ceil(lotMaxY + gridMargin); gy += 2) {
      const p1 = projectPoint(lotMinX - gridMargin, gy, 0);
      const p2 = projectPoint(lotMaxX + gridMargin, gy, 0);
      ctx.strokeStyle = "rgba(51, 65, 85, 0.35)";
      ctx.lineWidth = 0.7;
      ctx.beginPath();
      ctx.moveTo(p1.sx, p1.sy);
      ctx.lineTo(p2.sx, p2.sy);
      ctx.stroke();
    }

    // 2. 前面道路（南側 lotMinY - roadWidth .. lotMinY）
    const rw = archModel.site.road_width_m;
    drawPolygon3D(
      [
        [lotMinX - 4, lotMinY - rw, -0.02],
        [lotMaxX + 4, lotMinY - rw, -0.02],
        [lotMaxX + 4, lotMinY, -0.02],
        [lotMinX - 4, lotMinY, -0.02],
      ],
      "rgba(30, 41, 59, 0.88)",
      "#475569",
      1.2
    );
    const rc1 = projectPoint(lotMinX - 3.5, lotMinY - rw / 2, 0);
    const rc2 = projectPoint(lotMaxX + 3.5, lotMinY - rw / 2, 0);
    ctx.save();
    ctx.strokeStyle = "#94a3b8";
    ctx.setLineDash([8, 6]);
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(rc1.sx, rc1.sy);
    ctx.lineTo(rc2.sx, rc2.sy);
    ctx.stroke();
    ctx.restore();

    // 3. 敷地地盤面
    drawPolygon3D(
      [
        [lotMinX, lotMinY, 0],
        [lotMaxX, lotMinY, 0],
        [lotMaxX, lotMaxY, 0],
        [lotMinX, lotMaxY, 0],
      ],
      "rgba(16, 185, 129, 0.16)",
      "#10b981",
      2.0
    );

    // 4. 季節・時刻別の日影ポリゴン描画
    const seasonList = archModel.solar_simulation[solarSeason] || [];
    const activeSolar =
      seasonList.find((sp) => Math.round(sp.hour_jst) === solarHour) || seasonList[0];
    if (showShadow && activeSolar && activeSolar.shadow_polygon_xy.length >= 3) {
      const shadowPts: [number, number, number][] = activeSolar.shadow_polygon_xy.map((pt) => [
        pt[0],
        pt[1],
        0.02,
      ]);
      drawPolygon3D(
        shadowPts,
        "rgba(15, 23, 42, 0.72)",
        "rgba(245, 158, 11, 0.85)",
        1.6,
        [4, 3]
      );
    }

    // 5. 各階の部屋ボックスを奥行きソートで立体描画
    interface RenderFace {
      pts: [number, number, number][];
      fill: string;
      stroke: string;
      depth: number;
      lineWidth: number;
    }
    const facesToDraw: RenderFace[] = [];

    for (const fl of archModel.building.floors) {
      if (fl.floor_number > visibleMaxFloor) continue;
      const z0 = fl.z_bottom_m;
      const z1 = fl.z_top_m - 0.12;

      for (const rm of fl.rooms) {
        const x0 = rm.x;
        const y0 = rm.y;
        const x1 = rm.x + rm.w;
        const y1 = rm.y + rm.d;
        const isBalcony = rm.name.includes("バルコニー");
        const topZ = isBalcony ? z0 + 1.1 : z1;
        const wallColor = isBalcony
          ? "rgba(16, 185, 129, 0.55)"
          : rm.name.includes("居間")
          ? "rgba(245, 158, 11, 0.72)"
          : "rgba(56, 189, 248, 0.65)";
        const topColor = isBalcony
          ? "rgba(52, 211, 153, 0.78)"
          : rm.name.includes("居間")
          ? "rgba(251, 191, 36, 0.85)"
          : "rgba(125, 211, 252, 0.82)";

        const boxFaces: { pts: [number, number, number][]; isTop?: boolean }[] = [
          {
            pts: [
              [x0, y0, z0],
              [x1, y0, z0],
              [x1, y1, z0],
              [x0, y1, z0],
            ],
          },
          {
            pts: [
              [x0, y0, z0],
              [x1, y0, z0],
              [x1, y0, topZ],
              [x0, y0, topZ],
            ],
          },
          {
            pts: [
              [x1, y1, z0],
              [x0, y1, z0],
              [x0, y1, topZ],
              [x1, y1, topZ],
            ],
          },
          {
            pts: [
              [x0, y1, z0],
              [x0, y0, z0],
              [x0, y0, topZ],
              [x0, y1, topZ],
            ],
          },
          {
            pts: [
              [x1, y0, z0],
              [x1, y1, z0],
              [x1, y1, topZ],
              [x1, y0, topZ],
            ],
          },
          {
            pts: [
              [x0, y0, topZ],
              [x1, y0, topZ],
              [x1, y1, topZ],
              [x0, y1, topZ],
            ],
            isTop: true,
          },
        ];

        for (const bf of boxFaces) {
          const avgDepth =
            bf.pts.reduce((acc, p) => acc + projectPoint(p[0], p[1], p[2]).depth, 0) /
            bf.pts.length;
          facesToDraw.push({
            pts: bf.pts,
            fill: bf.isTop ? topColor : wallColor,
            stroke: "#f8fafc",
            depth: avgDepth,
            lineWidth: bf.isTop ? 1.3 : 0.9,
          });
        }
      }
    }

    facesToDraw.sort((a, b) => b.depth - a.depth);
    for (const f of facesToDraw) {
      drawPolygon3D(f.pts, f.fill, f.stroke, f.lineWidth);
    }

    // 6. 建築基準法・斜線制限立体枠（道路斜線・北側斜線）
    if (showSlantCage && archModel.slant_envelope?.vertices_3d?.length >= 10) {
      const v = archModel.slant_envelope.vertices_3d as [number, number, number][];
      const cageLoops: [number, number, number][][] = [
        [v[0], v[1], v[5], v[4]], // 南立面立上り
        [v[4], v[5], v[6], v[7]], // 道路斜線勾配面
        [v[7], v[6], v[8], v[9]], // 北側斜線勾配面
        [v[9], v[8], v[2], v[3]], // 北側立面
      ];
      for (const poly of cageLoops) {
        drawPolygon3D(
          poly,
          "rgba(56, 189, 248, 0.08)",
          "rgba(56, 189, 248, 0.88)",
          1.4,
          [5, 4]
        );
      }
    }

    // 7. 部屋ラベル表示
    if (showRoomLabels) {
      const targetFloorObj =
        archModel.building.floors.find(
          (f) => f.floor_number === Math.min(activeFloor, visibleMaxFloor)
        ) || archModel.building.floors[archModel.building.floors.length - 1];
      if (targetFloorObj) {
        const labelZ = targetFloorObj.z_top_m + 0.15;
        ctx.font = "bold 11px 'Hiragino Kaku Gothic ProN', 'Yu Gothic UI', Meiryo, sans-serif";
        ctx.textAlign = "center";
        for (const rm of targetFloorObj.rooms) {
          const p = projectPoint(rm.x + rm.w / 2, rm.y + rm.d / 2, labelZ);
          const text = `${toPureJapaneseText(rm.name)}（${rm.tatami_jo}帖）`;
          const tw = ctx.measureText(text).width + 10;
          ctx.fillStyle = "rgba(15, 23, 42, 0.86)";
          ctx.fillRect(p.sx - tw / 2, p.sy - 10, tw, 18);
          ctx.strokeStyle = "#38bdf8";
          ctx.lineWidth = 0.8;
          ctx.strokeRect(p.sx - tw / 2, p.sy - 10, tw, 18);
          ctx.fillStyle = "#f8fafc";
          ctx.fillText(text, p.sx, p.sy + 3);
        }
      }
    }

    // 方位ラベル
    const compassPts: { label: string; pos: [number, number, number]; color: string }[] = [
      { label: "北", pos: [0, lotMaxY + 2.0, 0], color: "#f43f5e" },
      { label: "南（前面道路側）", pos: [0, lotMinY - rw - 1.3, 0], color: "#38bdf8" },
      { label: "東", pos: [lotMaxX + 2.0, 0, 0], color: "#cbd5e1" },
      { label: "西", pos: [lotMinX - 2.0, 0, 0], color: "#cbd5e1" },
    ];
    ctx.font = "bold 12px 'Hiragino Kaku Gothic ProN', 'Yu Gothic UI', Meiryo, sans-serif";
    ctx.textAlign = "center";
    for (const cp of compassPts) {
      const p = projectPoint(cp.pos[0], cp.pos[1], cp.pos[2]);
      ctx.fillStyle = cp.color;
      ctx.fillText(cp.label, p.sx, p.sy);
    }

    // 左上ステータスオーバーレイ
    ctx.textAlign = "left";
    ctx.fillStyle = "rgba(15, 23, 42, 0.88)";
    ctx.fillRect(12, 12, 375, 76);
    ctx.strokeStyle = "#334155";
    ctx.strokeRect(12, 12, 375, 76);
    ctx.fillStyle = "#38bdf8";
    ctx.font = "bold 12.5px 'Hiragino Kaku Gothic ProN', 'Yu Gothic UI', Meiryo, sans-serif";
    ctx.fillText(
      `立体建築モデル（地上${archModel.building.stories}階建・最高高さ${archModel.building.total_height_m}メートル）`,
      22,
      32
    );
    ctx.fillStyle = "#e2e8f0";
    ctx.font = "11.5px 'Hiragino Kaku Gothic ProN', 'Yu Gothic UI', Meiryo, sans-serif";
    ctx.fillText(
      `敷地：間口${archModel.site.lot_width_m}メートル × 奥行${archModel.site.lot_depth_m}メートル（前面道路${archModel.site.road_width_m}メートル）`,
      22,
      52
    );
    if (activeSolar) {
      ctx.fillStyle = "#fbbf24";
      ctx.fillText(
        `太陽位置：${activeSolar.season} ${Math.round(activeSolar.hour_jst)}時（太陽高度${activeSolar.solar_altitude_deg}度・方位${activeSolar.solar_azimuth_south_deg}度）`,
        22,
        72
      );
    }
  }, [
    archModel,
    studioMode,
    camYawDeg,
    camPitchDeg,
    camZoom,
    visibleMaxFloor,
    activeFloor,
    showSlantCage,
    showShadow,
    showRoomLabels,
    solarSeason,
    solarHour,
  ]);

  // 日本全国・都市地形立体モデルのCanvas描画
  useEffect(() => {
    const canvas = urbanCanvasRef.current;
    if (!canvas || !urbanScene || studioMode !== "urban") return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    const bgGrad = ctx.createLinearGradient(0, 0, 0, height);
    bgGrad.addColorStop(0, "#070d19");
    bgGrad.addColorStop(1, "#0f1b30");
    ctx.fillStyle = bgGrad;
    ctx.fillRect(0, 0, width, height);

    const minElev = urbanScene.terrain.min_elevation_m;
    const maxElev = urbanScene.terrain.max_elevation_m;
    const elevSpan = Math.max(1.0, maxElev - minElev);
    const radiusM = urbanScene.radius_m;

    const yaw = (camYawDeg * Math.PI) / 180.0;
    const pitch = (camPitchDeg * Math.PI) / 180.0;
    const cosY = Math.cos(yaw);
    const sinY = Math.sin(yaw);
    const cosP = Math.cos(pitch);
    const sinP = Math.sin(pitch);

    const baseScale = Math.min(width, height) / (radiusM * 2.45);
    const scale = baseScale * camZoom;

    function projectUrban(
      x: number,
      y: number,
      zElev: number
    ): { sx: number; sy: number; depth: number } {
      const zRel = (zElev - minElev) * zExaggeration;
      const x1 = x * cosY - y * sinY;
      const y1 = x * sinY + y * cosY;
      const y2 = y1 * cosP - zRel * sinP;
      const z2 = y1 * sinP + zRel * cosP;
      return {
        sx: width * 0.5 + x1 * scale,
        sy: height * 0.58 - z2 * scale,
        depth: y2,
      };
    }

    // 1. 国土地理院DEM標高グリッドのポリゴン描画
    const xs = urbanScene.terrain.x_coords;
    const ys = urbanScene.terrain.y_coords;
    const elevs = urbanScene.terrain.elevations;
    const n = urbanScene.terrain.grid_size;

    interface TerrainQuad {
      p00: { sx: number; sy: number; depth: number };
      p10: { sx: number; sy: number; depth: number };
      p11: { sx: number; sy: number; depth: number };
      p01: { sx: number; sy: number; depth: number };
      avgZ: number;
      depth: number;
    }

    const quads: TerrainQuad[] = [];
    for (let i = 0; i < n - 1; i++) {
      for (let j = 0; j < n - 1; j++) {
        const z00 = elevs[i][j];
        const z10 = elevs[i][j + 1];
        const z11 = elevs[i + 1][j + 1];
        const z01 = elevs[i + 1][j];
        const p00 = projectUrban(xs[j], ys[i], z00);
        const p10 = projectUrban(xs[j + 1], ys[i], z10);
        const p11 = projectUrban(xs[j + 1], ys[i + 1], z11);
        const p01 = projectUrban(xs[j], ys[i + 1], z01);
        quads.push({
          p00,
          p10,
          p11,
          p01,
          avgZ: (z00 + z10 + z11 + z01) / 4.0,
          depth: (p00.depth + p10.depth + p11.depth + p01.depth) / 4.0,
        });
      }
    }

    quads.sort((a, b) => b.depth - a.depth);
    for (const q of quads) {
      const ratio = Math.min(1, Math.max(0, (q.avgZ - minElev) / elevSpan));
      const r = Math.round(18 + ratio * 65);
      const g = Math.round(48 + ratio * 105);
      const b = Math.round(74 + (1 - ratio) * 45);
      ctx.fillStyle = `rgba(${r}, ${g}, ${b}, 0.82)`;
      ctx.strokeStyle = "rgba(148, 163, 184, 0.25)";
      ctx.lineWidth = 0.6;
      ctx.beginPath();
      ctx.moveTo(q.p00.sx, q.p00.sy);
      ctx.lineTo(q.p10.sx, q.p10.sy);
      ctx.lineTo(q.p11.sx, q.p11.sy);
      ctx.lineTo(q.p01.sx, q.p01.sy);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    }

    // 2. 等高線の立体描画
    if (showUrbanContours) {
      for (const c of urbanScene.terrain.contours) {
        ctx.strokeStyle = "rgba(52, 211, 153, 0.72)";
        ctx.lineWidth = 1.1;
        for (const seg of c.segments) {
          if (seg.length < 2) continue;
          const pA = projectUrban(seg[0][0], seg[0][1], c.elevation_m + 0.15);
          const pB = projectUrban(seg[1][0], seg[1][1], c.elevation_m + 0.15);
          ctx.beginPath();
          ctx.moveTo(pA.sx, pA.sy);
          ctx.lineTo(pB.sx, pB.sy);
          ctx.stroke();
        }
      }
    }

    // 3. 実在道路ネットワークの立体描画
    for (const rd of urbanScene.roads) {
      if (rd.centerline_xy.length < 2) continue;
      ctx.strokeStyle = "rgba(148, 163, 184, 0.78)";
      ctx.lineWidth = Math.max(1.2, rd.width_m * scale * 0.22);
      ctx.beginPath();
      rd.centerline_xy.forEach((pt, idx) => {
        const p = projectUrban(pt[0], pt[1], minElev + elevSpan * 0.38);
        if (idx === 0) ctx.moveTo(p.sx, p.sy);
        else ctx.lineTo(p.sx, p.sy);
      });
      ctx.stroke();
    }

    // 4. 実在建物フットプリントの立体押し出し描画
    if (showUrbanBuildings) {
      const sortedBuildings = [...urbanScene.buildings].map((b) => {
        const first = b.footprint_xy[0] || [0, 0];
        const proj = projectUrban(first[0], first[1], b.base_elevation_m);
        return { b, depth: proj.depth };
      });
      sortedBuildings.sort((a, b) => b.depth - a.depth);

      for (const item of sortedBuildings) {
        const b = item.b;
        const ring = b.footprint_xy;
        if (ring.length < 3) continue;
        const zBase = b.base_elevation_m;
        const zTop = b.base_elevation_m + b.height_m / Math.max(1.0, zExaggeration * 0.55);

        const isCenterTarget = Math.hypot(ring[0][0], ring[0][1]) < 30.0;

        const wallFill = isCenterTarget
          ? "rgba(245, 158, 11, 0.85)"
          : "rgba(56, 189, 248, 0.55)";
        const roofFill = isCenterTarget
          ? "rgba(251, 191, 36, 0.95)"
          : "rgba(125, 211, 252, 0.78)";
        const strokeCol = isCenterTarget ? "#ffffff" : "rgba(226, 232, 240, 0.65)";

        for (let i = 0; i < ring.length; i++) {
          const pA = ring[i];
          const pB = ring[(i + 1) % ring.length];
          const s0 = projectUrban(pA[0], pA[1], zBase);
          const s1 = projectUrban(pB[0], pB[1], zBase);
          const s2 = projectUrban(pB[0], pB[1], zTop);
          const s3 = projectUrban(pA[0], pA[1], zTop);

          ctx.fillStyle = wallFill;
          ctx.strokeStyle = strokeCol;
          ctx.lineWidth = 0.65;
          ctx.beginPath();
          ctx.moveTo(s0.sx, s0.sy);
          ctx.lineTo(s1.sx, s1.sy);
          ctx.lineTo(s2.sx, s2.sy);
          ctx.lineTo(s3.sx, s3.sy);
          ctx.closePath();
          ctx.fill();
          ctx.stroke();
        }

        ctx.fillStyle = roofFill;
        ctx.strokeStyle = strokeCol;
        ctx.lineWidth = 0.85;
        ctx.beginPath();
        ring.forEach((pt, idx) => {
          const s = projectUrban(pt[0], pt[1], zTop);
          if (idx === 0) ctx.moveTo(s.sx, s.sy);
          else ctx.lineTo(s.sx, s.sy);
        });
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      }
    }

    // 中心マーカー（対象地点）
    const centerElev = urbanScene.terrain.center.elevation_m;
    const centerGround = projectUrban(0, 0, centerElev);
    const centerPinTop = projectUrban(0, 0, centerElev + 28);
    ctx.strokeStyle = "#f43f5e";
    ctx.lineWidth = 2.2;
    ctx.beginPath();
    ctx.moveTo(centerGround.sx, centerGround.sy);
    ctx.lineTo(centerPinTop.sx, centerPinTop.sy);
    ctx.stroke();
    ctx.fillStyle = "#f43f5e";
    ctx.beginPath();
    ctx.arc(centerPinTop.sx, centerPinTop.sy, 5, 0, Math.PI * 2);
    ctx.fill();

    // 左上オーバーレイ
    ctx.textAlign = "left";
    ctx.fillStyle = "rgba(15, 23, 42, 0.88)";
    ctx.fillRect(12, 12, 405, 76);
    ctx.strokeStyle = "#334155";
    ctx.strokeRect(12, 12, 405, 76);
    ctx.fillStyle = "#38bdf8";
    ctx.font = "bold 12.5px 'Hiragino Kaku Gothic ProN', 'Yu Gothic UI', Meiryo, sans-serif";
    ctx.fillText(
      `国土地理院 数値標高モデル・実在街区立体表示（半径${urbanScene.radius_m}メートル）`,
      22,
      32
    );
    ctx.fillStyle = "#e2e8f0";
    ctx.font = "11.5px 'Hiragino Kaku Gothic ProN', 'Yu Gothic UI', Meiryo, sans-serif";
    ctx.fillText(
      `中心標高：${centerElev}メートル（領域内最低 ${minElev}メートル 〜 最高 ${maxElev}メートル）`,
      22,
      52
    );
    ctx.fillStyle = "#34d399";
    ctx.fillText(
      `実在建物ポリゴン：${urbanScene.buildings_count}棟 ／ 道路区間：${urbanScene.roads_count}本 ／ 等高線：${urbanScene.terrain.contours.length}本`,
      22,
      72
    );
  }, [
    urbanScene,
    studioMode,
    camYawDeg,
    camPitchDeg,
    camZoom,
    zExaggeration,
    showUrbanContours,
    showUrbanBuildings,
  ]);

  const currentFloorSvg = floorSvgs[String(activeFloor)] || floorSvgs["1"] || "";

  return (
    <div className="cad3d-studio-layout">
      {/* 左サイドバー：モード切替と設計パラメータ */}
      <aside className="left-sidebar">
        <div className="panel-scroll">
          <div className="section-box">
            <div className="section-title">
              <span>設計・立体解析モード切替</span>
            </div>
            <div className="form-grid-2">
              <button
                type="button"
                className={`btn ${studioMode === "property" ? "btn-primary" : "btn-secondary"}`}
                onClick={() => setStudioMode("property")}
              >
                個別敷地・建物設計
              </button>
              <button
                type="button"
                className={`btn ${studioMode === "urban" ? "btn-primary" : "btn-secondary"}`}
                onClick={() => setStudioMode("urban")}
              >
                日本全国・都市地形
              </button>
            </div>
          </div>

          {studioMode === "property" ? (
            <>
              <div className="section-box">
                <div className="section-title">
                  <span>対象物件・敷地条件の指定</span>
                </div>

                <div className="form-row">
                  <label className="form-label">登録物件から敷地・面積条件を読込</label>
                  <select
                    className="form-select"
                    value={propId ?? ""}
                    onChange={(e) => {
                      const idVal = Number(e.target.value);
                      setPropId(idVal);
                      const found = properties.find((p) => p.id === idVal);
                      if (found) {
                        onSelectProperty(found);
                        handleGeneratePropertyModel(found.id, true);
                      }
                    }}
                  >
                    {properties.slice(0, 60).map((p) => (
                      <option key={p.id} value={p.id}>
                        物件番号{p.id}｜{p.municipality_name}（{p.category}・
                        {p.land_area_sqm ?? p.building_area_sqm ?? 100}平米）
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-grid-2 form-row">
                  <div>
                    <label className="form-label">敷地面積（平米）</label>
                    <input
                      type="number"
                      className="form-input"
                      value={landArea}
                      onChange={(e) => setLandArea(Number(e.target.value))}
                    />
                  </div>
                  <div>
                    <label className="form-label">前面道路幅員（メートル）</label>
                    <input
                      type="number"
                      step="0.1"
                      className="form-input"
                      value={roadWidth}
                      onChange={(e) => setRoadWidth(Number(e.target.value))}
                    />
                  </div>
                </div>

                <div className="form-grid-2 form-row">
                  <div>
                    <label className="form-label">指定建蔽率（パーセント）</label>
                    <input
                      type="number"
                      className="form-input"
                      value={coverageRatio}
                      onChange={(e) => setCoverageRatio(Number(e.target.value))}
                    />
                  </div>
                  <div>
                    <label className="form-label">指定容積率（パーセント）</label>
                    <input
                      type="number"
                      className="form-input"
                      value={farRatio}
                      onChange={(e) => setFarRatio(Number(e.target.value))}
                    />
                  </div>
                </div>

                <div className="form-grid-2 form-row">
                  <div>
                    <label className="form-label">接道方位</label>
                    <select
                      className="form-select"
                      value={roadDirection}
                      onChange={(e) => setRoadDirection(e.target.value)}
                    >
                      <option value="南">南側道路接道</option>
                      <option value="東">東側道路接道</option>
                      <option value="西">西側道路接道</option>
                      <option value="北">北側道路接道</option>
                    </select>
                  </div>
                  <div>
                    <label className="form-label">地上階数</label>
                    <select
                      className="form-select"
                      value={stories}
                      onChange={(e) => setStories(Number(e.target.value))}
                    >
                      <option value={1}>平屋（地上1階建）</option>
                      <option value={2}>地上2階建</option>
                      <option value={3}>地上3階建</option>
                      <option value={4}>地上4階建</option>
                    </select>
                  </div>
                </div>

                <div className="form-row">
                  <label className="form-label">間取り・空間構成</label>
                  <select
                    className="form-select"
                    value={floorPlanText}
                    onChange={(e) => setFloorPlanText(e.target.value)}
                  >
                    <option value="少人数事務所">
                      少人数事務所（執務4〜5席＋4名会議室＋給湯・書庫）
                    </option>
                    <option value="2LDK">{formatFloorPlanJP("2LDK")}</option>
                    <option value="3LDK">{formatFloorPlanJP("3LDK")}</option>
                    <option value="4LDK">{formatFloorPlanJP("4LDK")}</option>
                    <option value="5LDK">{formatFloorPlanJP("5LDK")}</option>
                  </select>
                </div>

                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={loadingArch}
                  onClick={() => handleGeneratePropertyModel(propId, false)}
                >
                  {loadingArch
                    ? "平面図・立体モデルを再計算中..."
                    : "平面図・立体モデルを生成・更新"}
                </button>
              </div>

              <div className="section-box">
                <div className="section-title">
                  <span>断面階層・斜線制限・日影シミュレーション</span>
                </div>

                <div className="form-row">
                  <label className="form-label">平面図および立体断面の対象階層</label>
                  <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                    {(archModel?.building.floors || []).map((f) => (
                      <button
                        key={f.floor_number}
                        type="button"
                        className={`layer-chip ${
                          activeFloor === f.floor_number ? "active" : ""
                        }`}
                        onClick={() => {
                          setActiveFloor(f.floor_number);
                          setVisibleMaxFloor(f.floor_number);
                        }}
                      >
                        {f.floor_number}階断面・平面図
                      </button>
                    ))}
                    <button
                      type="button"
                      className={`layer-chip ${
                        visibleMaxFloor >= (archModel?.building.stories ?? 2)
                          ? "active"
                          : ""
                      }`}
                      onClick={() =>
                        setVisibleMaxFloor(archModel?.building.stories ?? 2)
                      }
                    >
                      全階層立体表示
                    </button>
                  </div>
                </div>

                <div className="form-grid-2 form-row">
                  <div>
                    <label className="form-label">太陽軌道・季節指定</label>
                    <select
                      className="form-select"
                      value={solarSeason}
                      onChange={(e) => setSolarSeason(e.target.value)}
                    >
                      <option value="冬至">冬至（日影規制基準日）</option>
                      <option value="春分・秋分">春分・秋分</option>
                      <option value="夏至">夏至</option>
                    </select>
                  </div>
                  <div>
                    <label className="form-label">時刻指定：{solarHour}時00分</label>
                    <input
                      type="range"
                      min={8}
                      max={16}
                      step={1}
                      value={solarHour}
                      onChange={(e) => setSolarHour(Number(e.target.value))}
                      style={{ width: "100%", marginTop: "6px" }}
                    />
                  </div>
                </div>

                <div
                  className="form-row"
                  style={{ display: "flex", flexDirection: "column", gap: "6px" }}
                >
                  <label
                    style={{ fontSize: "12px", cursor: "pointer", display: "flex", gap: "6px" }}
                  >
                    <input
                      type="checkbox"
                      checked={showSlantCage}
                      onChange={(e) => setShowSlantCage(e.target.checked)}
                    />
                    建築基準法・道路斜線および北側斜線制限枠を表示
                  </label>
                  <label
                    style={{ fontSize: "12px", cursor: "pointer", display: "flex", gap: "6px" }}
                  >
                    <input
                      type="checkbox"
                      checked={showShadow}
                      onChange={(e) => setShowShadow(e.target.checked)}
                    />
                    地盤面への日影投影ポリゴンを表示
                  </label>
                  <label
                    style={{ fontSize: "12px", cursor: "pointer", display: "flex", gap: "6px" }}
                  >
                    <input
                      type="checkbox"
                      checked={showRoomLabels}
                      onChange={(e) => setShowRoomLabels(e.target.checked)}
                    />
                    立体モデル上に室名・帖数ラベルを表示
                  </label>
                </div>
              </div>

              {propDownloads && (
                <div className="section-box">
                  <div className="section-title">
                    <span>図面・立体データ外部出力</span>
                  </div>
                  <div className="form-grid-2" style={{ gap: "8px" }}>
                    <a
                      href={propDownloads.svg_floor_1}
                      download
                      className="btn btn-secondary"
                      style={{ textDecoration: "none", fontSize: "11.5px" }}
                    >
                      平面図ベクター保存
                    </a>
                    <a
                      href={propDownloads.dxf}
                      download
                      className="btn btn-secondary"
                      style={{ textDecoration: "none", fontSize: "11.5px" }}
                    >
                      建築設計図面保存
                    </a>
                    <a
                      href={propDownloads.obj}
                      download
                      className="btn btn-emerald"
                      style={{ textDecoration: "none", fontSize: "11.5px" }}
                    >
                      立体形状データ保存
                    </a>
                    <a
                      href={propDownloads.gltf}
                      download
                      className="btn btn-emerald"
                      style={{ textDecoration: "none", fontSize: "11.5px" }}
                    >
                      標準立体モデル保存
                    </a>
                  </div>
                </div>
              )}
            </>
          ) : (
            <>
              <div className="section-box">
                <div className="section-title">
                  <span>日本全国・対象地域の指定</span>
                </div>

                <div className="form-row">
                  <label className="form-label">日本の主要都市・山岳地形プリセット</label>
                  <select
                    className="form-select"
                    onChange={(e) => {
                      const idx = Number(e.target.value);
                      const preset = JAPAN_URBAN_PRESETS[idx];
                      if (preset) {
                        setUrbanLat(preset.latitude);
                        setUrbanLon(preset.longitude);
                        setUrbanRadius(preset.radius_m);
                        handleGenerateUrbanModel(
                          preset.latitude,
                          preset.longitude,
                          preset.radius_m
                        );
                      }
                    }}
                  >
                    {JAPAN_URBAN_PRESETS.map((pr, idx) => (
                      <option key={pr.label} value={idx}>
                        {pr.label}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-grid-2 form-row">
                  <div>
                    <label className="form-label">中心北緯（度）</label>
                    <input
                      type="number"
                      step="0.0005"
                      className="form-input"
                      value={urbanLat}
                      onChange={(e) => setUrbanLat(Number(e.target.value))}
                    />
                  </div>
                  <div>
                    <label className="form-label">中心東経（度）</label>
                    <input
                      type="number"
                      step="0.0005"
                      className="form-input"
                      value={urbanLon}
                      onChange={(e) => setUrbanLon(Number(e.target.value))}
                    />
                  </div>
                </div>

                <div className="form-grid-2 form-row">
                  <div>
                    <label className="form-label">抽出半径（メートル）</label>
                    <input
                      type="number"
                      min={150}
                      max={600}
                      step={20}
                      className="form-input"
                      value={urbanRadius}
                      onChange={(e) => setUrbanRadius(Number(e.target.value))}
                    />
                  </div>
                  <div>
                    <label className="form-label">標高強調倍率：{zExaggeration}倍</label>
                    <input
                      type="range"
                      min={1.0}
                      max={5.0}
                      step={0.2}
                      value={zExaggeration}
                      onChange={(e) => setZExaggeration(Number(e.target.value))}
                      style={{ width: "100%", marginTop: "8px" }}
                    />
                  </div>
                </div>

                <div
                  className="form-row"
                  style={{ display: "flex", flexDirection: "column", gap: "6px" }}
                >
                  <label
                    style={{ fontSize: "12px", cursor: "pointer", display: "flex", gap: "6px" }}
                  >
                    <input
                      type="checkbox"
                      checked={showUrbanBuildings}
                      onChange={(e) => setShowUrbanBuildings(e.target.checked)}
                    />
                    国土地理院ベクトルタイルの実在建物群を立体表示
                  </label>
                  <label
                    style={{ fontSize: "12px", cursor: "pointer", display: "flex", gap: "6px" }}
                  >
                    <input
                      type="checkbox"
                      checked={showUrbanContours}
                      onChange={(e) => setShowUrbanContours(e.target.checked)}
                    />
                    標高メッシュから抽出した等高線を表示
                  </label>
                </div>

                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={loadingUrban}
                  onClick={() =>
                    handleGenerateUrbanModel(urbanLat, urbanLon, urbanRadius)
                  }
                >
                  {loadingUrban
                    ? "国土地理院タイルから都市・地形を解析中..."
                    : "この地点の都市平面図・地形立体モデルを生成"}
                </button>
              </div>

              {urbanDownloads && (
                <div className="section-box">
                  <div className="section-title">
                    <span>都市平面図・地形立体データ保存</span>
                  </div>
                  <div className="form-grid-2" style={{ gap: "8px" }}>
                    <a
                      href={urbanDownloads.svg}
                      download
                      className="btn btn-secondary"
                      style={{ textDecoration: "none", fontSize: "11.5px" }}
                    >
                      都市平面図ベクター保存
                    </a>
                    <a
                      href={urbanDownloads.dxf}
                      download
                      className="btn btn-secondary"
                      style={{ textDecoration: "none", fontSize: "11.5px" }}
                    >
                      都市設計図面保存
                    </a>
                    <a
                      href={urbanDownloads.obj}
                      download
                      className="btn btn-emerald"
                      style={{ textDecoration: "none", fontSize: "11.5px" }}
                    >
                      都市立体形状保存
                    </a>
                    <a
                      href={urbanDownloads.gltf}
                      download
                      className="btn btn-emerald"
                      style={{ textDecoration: "none", fontSize: "11.5px" }}
                    >
                      都市標準立体保存
                    </a>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </aside>

      {/* 中央＋右側：2画面並列ビューア（左：精密平面図ベクター ／ 右：対話型立体モデル） */}
      <main className="cad3d-main-stage">
        {studioMode === "property" ? (
          <div className="cad3d-split-view">
            {/* 左半分：精密建築平面図・配置図 */}
            <div className="cad3d-pane">
              <div className="cad3d-pane-header">
                <span>
                  精密建築平面図・配置図（現在表示：{activeFloor}階平面図 ／ 全
                  {archModel?.building.stories ?? stories}階）
                </span>
                <div style={{ display: "flex", gap: "6px" }}>
                  {(archModel?.building.floors || []).map((f) => (
                    <button
                      key={f.floor_number}
                      type="button"
                      className={`layer-chip ${
                        activeFloor === f.floor_number ? "active" : ""
                      }`}
                      onClick={() => setActiveFloor(f.floor_number)}
                    >
                      {f.floor_number}階平面図
                    </button>
                  ))}
                </div>
              </div>
              <div
                className="cad3d-svg-container"
                dangerouslySetInnerHTML={{ __html: currentFloorSvg }}
              />
            </div>

            {/* 右半分：対話型立体建物・斜線制限・日影モデル */}
            <div className="cad3d-pane">
              <div className="cad3d-pane-header">
                <span>
                  対話型立体建物・斜線制限・日影モデル（ドラッグで回転・拡大縮小可能）
                </span>
                <div style={{ display: "flex", gap: "6px" }}>
                  <button
                    type="button"
                    className="layer-chip"
                    onClick={() => setCamZoom((z) => Math.min(2.2, Number((z + 0.15).toFixed(2))))}
                  >
                    拡大
                  </button>
                  <button
                    type="button"
                    className="layer-chip"
                    onClick={() => setCamZoom((z) => Math.max(0.55, Number((z - 0.15).toFixed(2))))}
                  >
                    縮小
                  </button>
                  <button
                    type="button"
                    className="layer-chip"
                    onClick={() => {
                      setCamYawDeg(-36);
                      setCamPitchDeg(32);
                      setCamZoom(1.0);
                    }}
                  >
                    視点初期化
                  </button>
                </div>
              </div>
              <div className="cad3d-canvas-wrap">
                <canvas
                  ref={propCanvasRef}
                  width={760}
                  height={620}
                  className="cad3d-canvas"
                  onMouseDown={handleCanvasMouseDown}
                  onMouseMove={handleCanvasMouseMove}
                  onMouseUp={handleCanvasMouseUp}
                  onMouseLeave={handleCanvasMouseUp}
                />
              </div>
            </div>
          </div>
        ) : (
          <div className="cad3d-split-view">
            {/* 左半分：都市街区・等高線平面図 */}
            <div className="cad3d-pane">
              <div className="cad3d-pane-header">
                <span>
                  国土地理院 都市街区・道路網・等高線平面図（実在建物{" "}
                  {urbanScene?.buildings_count ?? 0} 棟）
                </span>
              </div>
              <div
                className="cad3d-svg-container"
                dangerouslySetInnerHTML={{
                  __html: urbanSvg,
                }}
              />
            </div>

            {/* 右半分：国土地理院DEM標高メッシュ＋実在街区立体モデル */}
            <div className="cad3d-pane">
              <div className="cad3d-pane-header">
                <span>
                  国土地理院 数値標高地形メッシュ・実在都市立体モデル（ドラッグで回転）
                </span>
                <div style={{ display: "flex", gap: "6px" }}>
                  <button
                    type="button"
                    className="layer-chip"
                    onClick={() => setCamZoom((z) => Math.min(2.5, Number((z + 0.2).toFixed(2))))}
                  >
                    拡大
                  </button>
                  <button
                    type="button"
                    className="layer-chip"
                    onClick={() => setCamZoom((z) => Math.max(0.5, Number((z - 0.2).toFixed(2))))}
                  >
                    縮小
                  </button>
                  <button
                    type="button"
                    className="layer-chip"
                    onClick={() => {
                      setCamYawDeg(-36);
                      setCamPitchDeg(32);
                      setCamZoom(1.0);
                    }}
                  >
                    視点初期化
                  </button>
                </div>
              </div>
              <div className="cad3d-canvas-wrap">
                <canvas
                  ref={urbanCanvasRef}
                  width={760}
                  height={620}
                  className="cad3d-canvas"
                  onMouseDown={handleCanvasMouseDown}
                  onMouseMove={handleCanvasMouseMove}
                  onMouseUp={handleCanvasMouseUp}
                  onMouseLeave={handleCanvasMouseUp}
                />
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

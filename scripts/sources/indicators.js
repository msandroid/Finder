// 国別・地域別の指標（時系列）: BIS 住宅価格指数、OECD 住宅価格・家賃指数、米国 FHFA 住宅価格指数
import { writeIndicators } from '../../src/write.js';
import { csvRecords, parseCsvLine } from '../lib/csv.js';
import { fetchRetry } from '../lib/net.js';

// "2024-Q1" / "2024Q1" / "2024" → "2024Q1" / "2024"
export function normalizePeriod(p) {
  const m = String(p ?? '').trim().match(/^(\d{4})(?:-?Q([1-4]))?$/);
  return m ? (m[2] ? `${m[1]}Q${m[2]}` : m[1]) : null;
}

function pushPoint(map, key, meta, period, value) {
  const v = Number(value);
  if (!period || value === '' || value == null || !Number.isFinite(v)) return;
  let s = map.get(key);
  if (!s) map.set(key, (s = { ...meta, points: [] }));
  s.points.push([period, Math.round(v * 1000) / 1000]);
}

const finish = (map) =>
  [...map.values()]
    .map((s) => ({ ...s, points: s.points.sort((a, b) => a[0].localeCompare(b[0])) }))
    .sort((a, b) => `${a.country}${a.area}${a.indicator}`.localeCompare(`${b.country}${b.area}${b.indicator}`));

// ---------------------------------------------------------------- BIS
const BIS_URLS = [
  'https://stats.bis.org/api/v1/data/WS_SPP/Q..N+R.628/all?format=csv&startPeriod=1970',
  'https://stats.bis.org/api/v2/data/dataflow/BIS/WS_SPP/1.0/Q.*.N+R.628?format=csv&startPeriod=1970',
];

/** BIS SDMX CSV → 指標データセット。列名は "REF_AREA" または "REF_AREA:Reference area" のどちらでもよい */
export function parseBis(text) {
  const lines = text.split(/\r?\n/);
  const header = parseCsvLine(lines[0].replace(/^﻿/, '')).map((h) => h.split(':')[0].trim());
  const map = new Map();
  for (const r of csvRecords(lines.slice(1), { header })) {
    if (r.FREQ && r.FREQ !== 'Q') continue;
    const indicator = r.VALUE === 'R' ? 'bis_real' : r.VALUE === 'N' ? 'bis_nominal' : null;
    if (!indicator || !r.REF_AREA) continue;
    pushPoint(map, `${r.REF_AREA}|${indicator}`, { country: r.REF_AREA, area: '', indicator }, normalizePeriod(r.TIME_PERIOD), r.OBS_VALUE);
  }
  return {
    version: 1,
    source: 'bis',
    name: 'BIS Selected residential property prices',
    license: 'BIS statistics（出典明記で利用可）',
    generatedAt: new Date().toISOString(),
    indicators: {
      bis_nominal: { label: '住宅価格指数（名目）', unit: '2010=100', frequency: 'Q' },
      bis_real: { label: '住宅価格指数（実質・物価調整後）', unit: '2010=100', frequency: 'Q' },
    },
    series: finish(map),
  };
}

// ---------------------------------------------------------------- OECD
const OECD_URL =
  'https://sdmx.oecd.org/public/rest/data/OECD.SDD.TPS,DSD_RHPI_TARGET@DF_RHPI_TARGET,/all?startPeriod=1990&dimensionAtObservation=AllDimensions&format=csvfilewithlabels';

// OECD は ISO 3166-1 alpha-3 を使うので、他の出典と揃えるため alpha-2 に変換する
export const ISO3 = {
  AUS: 'AU', AUT: 'AT', BEL: 'BE', BGR: 'BG', BRA: 'BR', CAN: 'CA', CHE: 'CH', CHL: 'CL', CHN: 'CN', COL: 'CO',
  CRI: 'CR', CYP: 'CY', CZE: 'CZ', DEU: 'DE', DNK: 'DK', ESP: 'ES', EST: 'EE', FIN: 'FI', FRA: 'FR', GBR: 'GB',
  GRC: 'GR', HKG: 'HK', HRV: 'HR', HUN: 'HU', IDN: 'ID', IND: 'IN', IRL: 'IE', ISL: 'IS', ISR: 'IL', ITA: 'IT',
  JPN: 'JP', KOR: 'KR', LTU: 'LT', LUX: 'LU', LVA: 'LV', MAR: 'MA', MEX: 'MX', MKD: 'MK', MLT: 'MT', MYS: 'MY',
  NLD: 'NL', NOR: 'NO', NZL: 'NZ', PER: 'PE', PHL: 'PH', POL: 'PL', PRT: 'PT', ROU: 'RO', RUS: 'RU', SAU: 'SA',
  SGP: 'SG', SRB: 'RS', SVK: 'SK', SVN: 'SI', SWE: 'SE', THA: 'TH', TUR: 'TR', TWN: 'TW', UKR: 'UA', USA: 'US',
  ZAF: 'ZA', ARE: 'AE', ARG: 'AR',
};

/**
 * OECD SDMX CSV（ラベル付き）→ 指標データセット。
 * 指標は MEASURE × UNIT_MEASURE の組ごとに1つ作り、ラベルは CSV のラベル列から取る。
 * ラベル付き CSV では各コード列の直後にラベル列が並ぶ。
 */
export function parseOecd(text) {
  const lines = text.split(/\r?\n/);
  const header = parseCsvLine(lines[0].replace(/^﻿/, '')).map((h) => h.trim());
  const col = (name) => header.indexOf(name);
  const idx = { area: col('REF_AREA'), freq: col('FREQ'), measure: col('MEASURE'), unit: col('UNIT_MEASURE'), adj: col('ADJUSTMENT'), time: col('TIME_PERIOD'), value: col('OBS_VALUE') };
  if (idx.area < 0 || idx.measure < 0 || idx.time < 0 || idx.value < 0) throw new Error('OECD: 想定した列が見つかりません');
  const labelOf = (i, row) => (i >= 0 && header[i + 1] && !/^[A-Z_]+$/.test(header[i + 1]) ? row[i + 1] : '') || row[i];

  const map = new Map();
  const indicators = {};
  for (const line of lines.slice(1)) {
    if (!line) continue;
    const row = parseCsvLine(line);
    if (idx.freq >= 0 && row[idx.freq] !== 'Q') continue;
    // 季節調整の有無が分かれている場合は、原系列（N）だけを使う
    if (idx.adj >= 0 && row[idx.adj] && !['N', '_Z'].includes(row[idx.adj])) continue;
    const measure = row[idx.measure];
    const unit = idx.unit >= 0 ? row[idx.unit] : '';
    const id = `oecd_${measure}${unit ? `_${unit}` : ''}`.toLowerCase();
    indicators[id] ??= { label: labelOf(idx.measure, row), unit: idx.unit >= 0 ? labelOf(idx.unit, row) : '', frequency: 'Q' };
    const area = row[idx.area];
    const country = ISO3[area] ?? area;
    pushPoint(map, `${country}|${id}`, { country, area: '', indicator: id }, normalizePeriod(row[idx.time]), row[idx.value]);
  }
  return {
    version: 1,
    source: 'oecd',
    name: 'OECD Analytical house price indicators',
    license: 'OECD（CC BY 4.0）',
    generatedAt: new Date().toISOString(),
    indicators,
    series: finish(map),
  };
}

// ---------------------------------------------------------------- FHFA（米国）
const FHFA_URLS = ['https://www.fhfa.gov/hpi/download/monthly/hpi_master.csv', 'https://www.fhfa.gov/HPI_master.csv'];

/** FHFA HPI master CSV → 指標データセット。四半期の traditional 指数（全取引・購入のみ）を州・都市圏・全米で持つ */
export function parseFhfa(text) {
  const map = new Map();
  const flavors = { 'all-transactions': 'fhfa_all_transactions', 'purchase-only': 'fhfa_purchase_only' };
  for (const r of csvRecords(text.split(/\r?\n/))) {
    if (r.hpi_type !== 'traditional' || r.frequency !== 'quarterly') continue;
    const indicator = flavors[r.hpi_flavor];
    if (!indicator) continue;
    const national = r.place_id === 'USA';
    const area = national ? '' : r.place_id;
    const meta = { country: 'US', area, areaName: national ? '' : r.place_name, level: national ? 'national' : r.level, indicator };
    pushPoint(map, `${area}|${indicator}`, meta, normalizePeriod(`${r.yr}Q${r.period}`), r.index_nsa);
  }
  return {
    version: 1,
    source: 'fhfa',
    name: 'FHFA House Price Index',
    license: 'U.S. Federal Housing Finance Agency（パブリックドメイン）',
    generatedAt: new Date().toISOString(),
    indicators: {
      fhfa_all_transactions: { label: '住宅価格指数（全取引・鑑定含む）', unit: '1980Q1=100（州・全米）/ 1995Q1=100（都市圏）', frequency: 'Q' },
      fhfa_purchase_only: { label: '住宅価格指数（購入取引のみ）', unit: '1991Q1=100', frequency: 'Q' },
    },
    series: finish(map),
  };
}

// ----------------------------------------------------------------
async function fetchFirst(urls, init) {
  let lastErr;
  for (const url of urls) {
    try {
      const res = await fetchRetry(url, init);
      if (res) return await res.text();
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr ?? new Error(`取得できませんでした: ${urls.join(', ')}`);
}

export async function build({ out, log, only }) {
  const jobs = {
    bis: async () => parseBis(await fetchFirst(BIS_URLS)),
    oecd: async () => parseOecd(await fetchFirst([OECD_URL], { headers: { Accept: 'application/vnd.sdmx.data+csv; charset=utf-8; labels=both' } })),
    fhfa: async () => parseFhfa(await fetchFirst(FHFA_URLS)),
  };
  const failures = [];
  for (const [name, job] of Object.entries(jobs)) {
    if (only && !only.includes(name)) continue;
    try {
      const set = await job();
      if (set.series.length === 0) throw new Error('系列が0件でした');
      writeIndicators(out, set);
      log(`indicators ${name}: ${set.series.length} 系列`);
    } catch (err) {
      log(`indicators ${name}: 失敗 — ${err.message}`);
      failures.push(name);
    }
  }
  return { failures };
}

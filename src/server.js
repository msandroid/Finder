import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import { COUNTRIES, resolveCountry } from './countries.js';
import { Store } from './store.js';

const COLS = ['city', 'district', 'type', 'period', 'count', 'medianPrice', 'medianUnitPrice', 'unitPriceP25', 'unitPriceP75'];
const toRow = (r) => Object.fromEntries(COLS.map((k, i) => [k, r[i]]));
const json = (value) => ({ content: [{ type: 'text', text: JSON.stringify(value, null, 2) }] });
const fail = (message) => ({ content: [{ type: 'text', text: message }], isError: true });
const norm = (s) => String(s ?? '').trim().toLowerCase();

const jaNames = new Intl.DisplayNames(['ja'], { type: 'region' });
const enNames = new Intl.DisplayNames(['en'], { type: 'region' });
const safeName = (names, code) => {
  try {
    const n = names.of(code);
    return n && n !== code ? n : null;
  } catch {
    return null;
  }
};
const countryName = (code) => COUNTRIES[code]?.name ?? safeName(jaNames, code) ?? code;

const countryArg = z.string().default('JP').describe('国コード（ISO 3166-1 alpha-2。例: "JP", "GB", "FR", "SG", "TW", "KR"）');
const regionArg = z.string().describe('地域（日本は都道府県、英国はカウンティ、フランスは県 …）。コードまたは名称');
const cityArg = z.string().describe('市区町村など。コードまたは名称');
const typeArg = z.string().optional().describe('物件種別。省略時はその国の代表的な種別。list_areas で国を指定すると一覧が出る');

// ---------------------------------------------------------------- 取引データ

function regionsOf(store, country) {
  const index = store.countryIndex(country);
  const available = new Map((index?.regions ?? []).map((r) => [r.code, r]));
  const statics = COUNTRIES[country]?.regions ?? [];
  const merged = statics.map((s) => ({ ...s, ...available.get(s.code), available: available.has(s.code) }));
  for (const [code, r] of available) if (!statics.some((s) => s.code === code)) merged.push({ ...r, available: true });
  return merged.sort((a, b) => a.code.localeCompare(b.code));
}

function resolveRegion(regions, input) {
  if (input == null || input === '') {
    const avail = regions.filter((r) => r.available);
    return avail.length === 1 ? avail[0] : null;
  }
  const s = norm(input);
  const exact = regions.find((r) => norm(r.code) === s || norm(r.name) === s || (r.aliases ?? []).some((a) => norm(a) === s));
  if (exact) return exact;
  const partial = regions.filter((r) => norm(r.name).includes(s));
  return partial.length === 1 ? partial[0] : null;
}

function loadRegion(store, countryInput, regionInput) {
  const country = resolveCountry(countryInput);
  if (!COUNTRIES[country]) {
    return { error: `${countryName(country)} の取引データはありません。国別の指標は list_indicators / country_trends を使ってください` };
  }
  const regions = regionsOf(store, country);
  const region = resolveRegion(regions, regionInput);
  if (!region) {
    const hint = regions.filter((r) => r.available).slice(0, 20).map((r) => `${r.code}:${r.name}`).join(', ');
    return { error: `${COUNTRIES[country].regionLabel}を特定できません: ${regionInput ?? '(未指定)'}${hint ? `（例: ${hint}）` : ''}` };
  }
  const data = store.load(country, region.code);
  if (!data) return { error: `${region.name} のデータがまだありません（data/${country.toLowerCase()}/${region.code}.json.gz 未生成）` };
  return { country, data };
}

function resolveCity(data, input) {
  const s = norm(input);
  if (data.cities[input]) return input;
  const entries = Object.entries(data.cities);
  const exact = entries.filter(([code, c]) => norm(code) === s || norm(c.name) === s);
  if (exact.length === 1) return exact[0][0];
  const partial = entries.filter(([, c]) => norm(c.name).endsWith(s) || norm(c.name).startsWith(s));
  return partial.length === 1 ? partial[0][0] : null;
}

function resolveType(country, data, type) {
  if (type) return data.types.find((t) => norm(t) === norm(type)) ?? null;
  const preferred = COUNTRIES[country].defaultType;
  return data.types.includes(preferred) ? preferred : data.types[0];
}

const inRange = (period, from, to) => {
  const year = Number(period.slice(0, 4));
  return (!from || year >= from) && (!to || year <= to);
};

function meta(country, data) {
  const c = COUNTRIES[country];
  return {
    country,
    countryName: c.name,
    region: data.region,
    currency: data.currency,
    unitPrice: c.unitPrice ? `${data.currency}/㎡（${c.unitPrice}）` : 'この国のデータには面積がないため㎡単価はありません',
    source: `出典: ${c.source} を集計して作成`,
  };
}

// ---------------------------------------------------------------- 指標データ

function allSeries(store) {
  const out = [];
  for (const set of store.indicatorSets()) {
    for (const s of set.series) out.push({ ...s, source: set.source, def: set.indicators[s.indicator] });
  }
  return out;
}

function indicatorDefs(store) {
  const defs = {};
  for (const set of store.indicatorSets()) {
    for (const [id, d] of Object.entries(set.indicators)) defs[id] = { ...d, source: set.name, license: set.license };
  }
  return defs;
}

function resolveIndicator(defs, input) {
  if (defs[input]) return input;
  const s = norm(input);
  const hits = Object.entries(defs).filter(([id, d]) => norm(id) === s || norm(d.label).includes(s));
  return hits.length === 1 ? hits[0][0] : null;
}

// 国名（日本語・英語）や alpha-3 でも国を指定できるようにする
function countryMatcher(codes) {
  const table = new Map();
  for (const code of codes) {
    for (const k of [code, safeName(jaNames, code), safeName(enNames, code), COUNTRIES[code]?.name]) if (k) table.set(norm(k), code);
  }
  return (input) => {
    const s = norm(input);
    const c = resolveCountry(input);
    return table.get(s) ?? (codes.includes(c) ? c : null);
  };
}

const quarterIndex = (p) => Number(p.slice(0, 4)) * 4 + (p.length > 4 ? Number(p.slice(5)) - 1 : 0);
const pct = (a, b) => (a != null && b ? Math.round((a / b - 1) * 10000) / 100 : null);

function valueAt(points, period) {
  return points.find(([p]) => p === period)?.[1] ?? null;
}

function shiftQuarter(period, quarters) {
  const i = quarterIndex(period) - quarters;
  return `${Math.floor(i / 4)}Q${(i % 4) + 1}`;
}

function annualize(points) {
  const byYear = new Map();
  for (const [p, v] of points) {
    const y = p.slice(0, 4);
    if (!byYear.has(y)) byYear.set(y, []);
    byYear.get(y).push(v);
  }
  return [...byYear].map(([y, vs]) => [y, Math.round((vs.reduce((a, b) => a + b, 0) / vs.length) * 1000) / 1000]);
}

// ----------------------------------------------------------------

export function createServer(store = new Store()) {
  const server = new McpServer({ name: 'world-realestate-mcp', version: '0.2.0' });

  server.registerTool(
    'list_areas',
    {
      title: '収録国・エリア一覧',
      description:
        '収録データの一覧。country 省略時は、取引データのある国と、指標データのある国を返す。' +
        'country 指定で地域（都道府県・カウンティ・県など）の一覧、region も指定するとその市区町村と物件種別、' +
        'city も指定すると地区の一覧を返す。',
      inputSchema: {
        country: z.string().optional().describe('国コード（例: "JP", "GB", "US"）。省略時は収録国の一覧'),
        region: z.string().optional().describe('地域。country 省略時は日本の都道府県とみなす'),
        city: z.string().optional(),
      },
    },
    async ({ country: countryInput, region, city }) => {
      // 地域だけ指定された場合は、以前の版と同じく日本とみなす
      if (!countryInput && (region || city)) countryInput = 'JP';
      if (!countryInput) {
        const withData = new Set(store.countries());
        const indicatorCountries = new Map();
        for (const s of allSeries(store)) {
          const e = indicatorCountries.get(s.country) ?? new Set();
          e.add(s.indicator);
          indicatorCountries.set(s.country, e);
        }
        return json({
          transactions: Object.entries(COUNTRIES).map(([code, c]) => ({
            code,
            name: c.name,
            currency: c.currency,
            available: withData.has(code),
            regions: store.countryIndex(code)?.regions.length ?? 0,
            source: c.source,
          })),
          indicators: [...indicatorCountries]
            .map(([code, ids]) => ({ code, name: countryName(code), indicators: [...ids].sort() }))
            .sort((a, b) => a.code.localeCompare(b.code)),
          note: '取引データは market_stats / district_stats、指標は list_indicators / country_trends / country_ranking で参照できます',
        });
      }
      const country = resolveCountry(countryInput);
      if (!COUNTRIES[country]) {
        const areas = allSeries(store).filter((s) => s.country === country && s.area);
        if (!areas.length) return fail(`${countryName(country)} の取引データはありません。国別の指標は list_indicators を参照してください`);
        const unique = new Map(areas.map((s) => [s.area, { area: s.area, name: s.areaName, level: s.level }]));
        return json({ country, countryName: countryName(country), note: 'この国は指標（価格指数）のみ。country_trends の area に指定できます', areas: [...unique.values()] });
      }
      const c = COUNTRIES[country];
      if (!region) {
        const regions = regionsOf(store, country);
        return json({
          country,
          countryName: c.name,
          regionLabel: c.regionLabel,
          regions: regions.map((r) => ({ code: r.code, name: r.name, available: r.available, cities: r.cities, types: r.types, from: r.from, to: r.to })),
        });
      }
      const { data, error } = loadRegion(store, country, region);
      if (error) return fail(error);
      if (!city) {
        return json({
          ...meta(country, data),
          cityLabel: c.cityLabel,
          types: data.types,
          cities: Object.entries(data.cities).map(([code, x]) => ({ code, name: x.name, districts: x.districts.length })),
        });
      }
      const code = resolveCity(data, city);
      if (!code) return fail(`${c.cityLabel}を特定できません: ${city}`);
      return json({ ...meta(country, data), districtLabel: c.districtLabel, city: { code, ...data.cities[code] } });
    },
  );

  server.registerTool(
    'market_stats',
    {
      title: '相場推移（取引データ）',
      description:
        '取引データから、件数・価格中央値・㎡単価（中央値と25/75%点）の推移を返す。' +
        '対応国: 日本・英国・フランス・シンガポール・台湾・韓国。価格は各国の通貨建て。' +
        'city 省略時は地域内の市区町村ごとの値を返す。',
      inputSchema: {
        country: countryArg,
        region: regionArg.optional().describe('地域。国に地域が1つしかない場合（シンガポール）は省略可'),
        city: cityArg.optional(),
        district: z.string().optional().describe('地区名。city 指定時のみ有効'),
        type: typeArg,
        granularity: z.enum(['year', 'quarter']).default('year'),
        from: z.number().int().optional().describe('開始年（含む）'),
        to: z.number().int().optional().describe('終了年（含む）'),
      },
    },
    async ({ country: countryInput, region, city, district, type, granularity, from, to }) => {
      const { country, data, error } = loadRegion(store, countryInput, region);
      if (error) return fail(error);
      const t = resolveType(country, data, type);
      if (!t) return fail(`種別が見つかりません: ${type}（選べる種別: ${data.types.join(', ')}）`);
      let cityCode = null;
      if (city) {
        cityCode = resolveCity(data, city);
        if (!cityCode) return fail(`${COUNTRIES[country].cityLabel}を特定できません: ${city}`);
      } else if (district) {
        return fail('district を指定するときは city も指定してください');
      }
      const quarterly = granularity === 'quarter';
      const rows = data.rows
        .filter(
          (r) =>
            r[2] === t &&
            r[1] === (district ?? '') &&
            (!cityCode || r[0] === cityCode) &&
            r[3].includes('Q') === quarterly &&
            inRange(r[3], from, to),
        )
        .map((r) => ({ ...toRow(r), cityName: data.cities[r[0]]?.name }));
      if (rows.length === 0) return fail('該当するデータがありません');
      return json({ ...meta(country, data), type: t, types: data.types, granularity, rows });
    },
  );

  server.registerTool(
    'district_stats',
    {
      title: 'エリア別ランキング（取引データ）',
      description:
        '指定した年（または四半期）の地区別の統計を並べ替えて返す。地区の区分がない国（フランス・シンガポール・台湾）では市区町村別に並べる。' +
        'city 省略時は地域内のすべてが対象。',
      inputSchema: {
        country: countryArg,
        region: regionArg.optional(),
        city: cityArg.optional(),
        type: typeArg,
        period: z.string().regex(/^\d{4}(Q[1-4])?$/).optional().describe('"2024" または "2024Q3"。省略時は4四半期そろっている最新年'),
        sortBy: z.enum(['medianUnitPrice', 'medianPrice', 'count']).default('medianUnitPrice'),
        order: z.enum(['desc', 'asc']).default('desc'),
        minCount: z.number().int().min(1).default(5).describe('これ未満の件数のエリアは除外'),
        limit: z.number().int().min(1).max(200).default(20),
      },
    },
    async ({ country: countryInput, region, city, type, period, sortBy, order, minCount, limit }) => {
      const { country, data, error } = loadRegion(store, countryInput, region);
      if (error) return fail(error);
      const t = resolveType(country, data, type);
      if (!t) return fail(`種別が見つかりません: ${type}（選べる種別: ${data.types.join(', ')}）`);
      let cityCode = null;
      if (city) {
        cityCode = resolveCity(data, city);
        if (!cityCode) return fail(`${COUNTRIES[country].cityLabel}を特定できません: ${city}`);
      }
      const hasDistricts = Object.values(data.cities).some((c) => c.districts.length > 0);
      const level = hasDistricts ? 'district' : 'city';
      // ㎡単価がない国（英国）では価格で並べる
      const key = sortBy === 'medianUnitPrice' && !COUNTRIES[country].unitPrice ? 'medianPrice' : sortBy;
      // 既定は4四半期そろっている最新年（途中の年は件数が少なく偏るため）
      const years = data.periods.filter((p) => !p.includes('Q'));
      const target = period ?? [...years].reverse().find((y) => data.periods.includes(`${y}Q4`)) ?? years.at(-1);
      const rows = data.rows
        .filter(
          (r) => (hasDistricts ? r[1] !== '' : r[1] === '') && r[2] === t && r[3] === target && r[4] >= minCount && (!cityCode || r[0] === cityCode),
        )
        .map((r) => ({ ...toRow(r), cityName: data.cities[r[0]]?.name }))
        .filter((r) => r[key] != null)
        .sort((a, b) => (order === 'desc' ? b[key] - a[key] : a[key] - b[key]));
      if (rows.length === 0) return fail(`該当するデータがありません（period=${target}, type=${t}, minCount=${minCount}）`);
      return json({
        ...meta(country, data),
        type: t,
        period: target,
        level,
        sortBy: key,
        order,
        total: rows.length,
        rows: rows.slice(0, limit).map((r, i) => ({ rank: i + 1, ...r })),
      });
    },
  );

  server.registerTool(
    'list_indicators',
    {
      title: '指標一覧（世界の住宅価格・家賃指数）',
      description:
        '国別・地域別の時系列指標の一覧（BIS 住宅価格指数、OECD 住宅価格・家賃・価格/所得比など、米国 FHFA 住宅価格指数）。' +
        'country を指定すると、その国で使える指標と地域（米国の州・都市圏など）を返す。',
      inputSchema: { country: z.string().optional().describe('国コードまたは国名') },
    },
    async ({ country }) => {
      const series = allSeries(store);
      if (series.length === 0) return fail('指標データがまだありません（data/indicators/ 未生成）');
      const defs = indicatorDefs(store);
      if (!country) {
        const stats = {};
        for (const s of series.filter((x) => !x.area)) {
          const e = (stats[s.indicator] ??= { countries: new Set(), latest: '' });
          e.countries.add(s.country);
          const last = s.points.at(-1)?.[0] ?? '';
          if (last > e.latest) e.latest = last;
        }
        return json({
          indicators: Object.entries(defs).map(([id, d]) => ({
            id,
            ...d,
            countries: stats[id]?.countries.size ?? 0,
            latest: stats[id]?.latest || null,
          })),
        });
      }
      const match = countryMatcher([...new Set(series.map((s) => s.country))]);
      const code = match(country);
      if (!code) return fail(`指標データに ${country} はありません`);
      const own = series.filter((s) => s.country === code);
      return json({
        country: code,
        countryName: countryName(code),
        national: own.filter((s) => !s.area).map((s) => ({ indicator: s.indicator, ...defs[s.indicator], from: s.points[0]?.[0], to: s.points.at(-1)?.[0] })),
        areas: [...new Map(own.filter((s) => s.area).map((s) => [s.area, { area: s.area, name: s.areaName, level: s.level }])).values()],
      });
    },
  );

  server.registerTool(
    'country_trends',
    {
      title: '国別の推移（指標）',
      description:
        '複数の国（または米国の州・都市圏）の指標の推移を並べて返す。前年同期比（%）付き。' +
        '例: 日本・米国・英国の実質住宅価格、各国の家賃指数の比較。',
      inputSchema: {
        indicator: z.string().describe('指標 ID（list_indicators で確認）またはラベルの一部'),
        countries: z.array(z.string()).min(1).max(30).describe('国コードまたは国名の配列'),
        area: z.string().optional().describe('国内の地域（米国の州 "CA" や都市圏コードなど）。1か国指定時のみ'),
        granularity: z.enum(['year', 'quarter']).default('year').describe('year は四半期値の年平均'),
        from: z.number().int().optional(),
        to: z.number().int().optional(),
      },
    },
    async ({ indicator, countries, area, granularity, from, to }) => {
      const series = allSeries(store);
      const defs = indicatorDefs(store);
      const id = resolveIndicator(defs, indicator);
      if (!id) return fail(`指標を特定できません: ${indicator}。list_indicators で ID を確認してください`);
      const match = countryMatcher([...new Set(series.filter((s) => s.indicator === id).map((s) => s.country))]);
      const out = [];
      const missing = [];
      for (const input of countries) {
        const code = match(input);
        const s = code && series.find((x) => x.indicator === id && x.country === code && x.area === (area ?? ''));
        if (!s) {
          missing.push(input);
          continue;
        }
        let points = granularity === 'year' ? annualize(s.points) : s.points;
        const lag = granularity === 'year' ? 1 : 4;
        points = points.map(([p, v], i) => ({ period: p, value: v, yoyPct: pct(v, points[i - lag]?.[1]) }));
        out.push({ country: code, countryName: countryName(code), area: s.area || undefined, areaName: s.areaName || undefined, points: points.filter((x) => inRange(x.period, from, to)) });
      }
      if (out.length === 0) return fail(`該当する系列がありません（${missing.join(', ')}）`);
      return json({ indicator: id, ...defs[id], granularity, series: out, missing: missing.length ? missing : undefined });
    },
  );

  server.registerTool(
    'country_ranking',
    {
      title: '国別ランキング（指標）',
      description:
        '指標の値や変化率で国を並べる（例: 住宅価格の前年比上昇率が高い国、5年間の上昇率、価格/所得比の水準）。' +
        'country を指定するとその国の地域（米国の州・都市圏など）を並べる。',
      inputSchema: {
        indicator: z.string().describe('指標 ID またはラベルの一部'),
        metric: z.enum(['yoy', 'change', 'level']).default('yoy').describe('yoy: 前年同期比、change: years 年間の変化率、level: 値そのもの'),
        years: z.number().int().min(1).max(30).default(5).describe('metric=change の期間（年）'),
        period: z.string().regex(/^\d{4}Q[1-4]$/).optional().describe('基準の四半期。省略時は多くの国がそろう最新四半期'),
        country: z.string().optional().describe('国内の地域を並べる場合の国コード（例: "US"）'),
        level: z.string().optional().describe('country 指定時の地域の階層（米国は "State" や "MSA"）'),
        order: z.enum(['desc', 'asc']).default('desc'),
        limit: z.number().int().min(1).max(100).default(20),
      },
    },
    async ({ indicator, metric, years, period, country, level, order, limit }) => {
      const series = allSeries(store);
      const defs = indicatorDefs(store);
      const id = resolveIndicator(defs, indicator);
      if (!id) return fail(`指標を特定できません: ${indicator}`);
      let pool = series.filter((s) => s.indicator === id);
      if (country) {
        const code = countryMatcher([...new Set(pool.map((s) => s.country))])(country);
        pool = pool.filter((s) => s.country === code && s.area && (!level || norm(s.level) === norm(level)));
      } else {
        // 国の比較では、ユーロ圏や OECD 平均などの集計値（コードが国名に解決できないもの）を除く
        pool = pool.filter((s) => !s.area && safeName(enNames, s.country));
      }
      if (pool.length === 0) return fail('該当する系列がありません');

      let target = period;
      if (!target) {
        const counts = new Map();
        for (const s of pool) for (const [p] of s.points) if (p.includes('Q')) counts.set(p, (counts.get(p) ?? 0) + 1);
        target = [...counts].filter(([, n]) => n >= pool.length / 2).map(([p]) => p).sort().at(-1);
      }
      if (!target) return fail('比較できる四半期が見つかりません');
      const lag = metric === 'yoy' ? 4 : years * 4;
      const rows = [];
      for (const s of pool) {
        const v = valueAt(s.points, target);
        if (v == null) continue;
        const value = metric === 'level' ? v : pct(v, valueAt(s.points, shiftQuarter(target, lag)));
        if (value == null) continue;
        rows.push({ country: s.country, countryName: countryName(s.country), area: s.area || undefined, areaName: s.areaName || undefined, value });
      }
      rows.sort((a, b) => (order === 'desc' ? b.value - a.value : a.value - b.value));
      return json({
        indicator: id,
        ...defs[id],
        metric,
        valueUnit: metric === 'level' ? defs[id].unit : '%',
        period: target,
        comparedWith: metric === 'level' ? undefined : shiftQuarter(target, lag),
        total: rows.length,
        rows: rows.slice(0, limit).map((r, i) => ({ rank: i + 1, ...r })),
      });
    },
  );

  return server;
}

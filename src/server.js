import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import { TYPES } from './aggregate.js';
import { PREFECTURES, prefectureName, resolvePrefecture } from './prefectures.js';
import { Store } from './store.js';

const SOURCE = '出典: 国土交通省 不動産情報ライブラリ（不動産取引価格情報）を集計して作成';
const COLS = ['city', 'district', 'type', 'period', 'count', 'medianPrice', 'medianUnitPrice', 'unitPriceP25', 'unitPriceP75'];

const toRow = (r) => Object.fromEntries(COLS.map((k, i) => [k, r[i]]));
const json = (value) => ({ content: [{ type: 'text', text: JSON.stringify(value, null, 2) }] });
const fail = (message) => ({ content: [{ type: 'text', text: message }], isError: true });

const prefectureArg = z.string().describe('都道府県。コード ("13") または名称 ("東京都" / "東京")');
const typeArg = z.enum(TYPES).default('中古マンション等').describe('取引の種類');

function loadPrefecture(store, input) {
  const code = resolvePrefecture(input);
  if (!code) return { error: `都道府県を特定できません: ${input}` };
  const data = store.load(code);
  if (!data) return { error: `${prefectureName(code)} のデータがありません（data/${code}.json.gz 未生成）` };
  return { data };
}

// 市区町村コード ("13104") または名称 ("新宿区") を解決する
function resolveCity(data, input) {
  const s = String(input).trim();
  if (data.cities[s]) return s;
  const matches = Object.entries(data.cities).filter(([, c]) => c.name === s || c.name.endsWith(s));
  return matches.length === 1 ? matches[0][0] : null;
}

function inRange(period, from, to) {
  const year = period.slice(0, 4);
  return (!from || year >= String(from)) && (!to || year <= String(to));
}

export function createServer(store = new Store()) {
  const server = new McpServer({ name: 'jp-realestate-mcp', version: '0.1.0' });

  server.registerTool(
    'list_areas',
    {
      title: '収録エリア一覧',
      description:
        '収録済みの都道府県・市区町村・地区を一覧する。prefecture 省略時は都道府県一覧、' +
        'prefecture 指定で市区町村一覧、city も指定するとその地区一覧を返す。',
      inputSchema: {
        prefecture: prefectureArg.optional(),
        city: z.string().optional().describe('市区町村。コード ("13104") または名称 ("新宿区")'),
      },
    },
    async ({ prefecture, city }) => {
      if (!prefecture) {
        const index = store.index();
        const available = new Map(index.prefectures.map((p) => [p.code, p]));
        return json({
          updatedAt: index.updatedAt ?? null,
          prefectures: PREFECTURES.map(([code, name]) => {
            const p = available.get(code);
            return p
              ? { code, name, available: true, cities: p.cities, from: p.periods[0], to: p.periods.at(-1) }
              : { code, name, available: false };
          }),
        });
      }
      const { data, error } = loadPrefecture(store, prefecture);
      if (error) return fail(error);
      if (!city) {
        return json({
          prefecture: data.prefecture,
          cities: Object.entries(data.cities).map(([code, c]) => ({ code, name: c.name, districts: c.districts.length })),
        });
      }
      const code = resolveCity(data, city);
      if (!code) return fail(`市区町村を特定できません: ${city}`);
      return json({ prefecture: data.prefecture, city: { code, ...data.cities[code] } });
    },
  );

  server.registerTool(
    'market_stats',
    {
      title: '相場推移',
      description:
        '都道府県・市区町村・地区単位で、取引件数・価格中央値・㎡単価（中央値と25/75%点）の推移を返す。' +
        '売買の取引価格のみで、賃料は含まない。',
      inputSchema: {
        prefecture: prefectureArg,
        city: z.string().optional().describe('市区町村。省略すると市区町村ごとの一覧を返す'),
        district: z.string().optional().describe('地区名（例: "西新宿"）。city 指定時のみ有効'),
        type: typeArg,
        granularity: z.enum(['year', 'quarter']).default('year').describe('年単位か四半期単位か'),
        from: z.number().int().optional().describe('開始年（含む）'),
        to: z.number().int().optional().describe('終了年（含む）'),
      },
    },
    async ({ prefecture, city, district, type, granularity, from, to }) => {
      const { data, error } = loadPrefecture(store, prefecture);
      if (error) return fail(error);
      let cityCode = null;
      if (city) {
        cityCode = resolveCity(data, city);
        if (!cityCode) return fail(`市区町村を特定できません: ${city}`);
      } else if (district) {
        return fail('district を指定するときは city も指定してください');
      }
      const isQuarter = (p) => p.includes('Q');
      const rows = data.rows
        .filter(
          (r) =>
            r[2] === type &&
            r[1] === (district ?? '') &&
            (!cityCode || r[0] === cityCode) &&
            isQuarter(r[3]) === (granularity === 'quarter') &&
            inRange(r[3], from, to),
        )
        .map((r) => ({ ...toRow(r), cityName: data.cities[r[0]]?.name }));
      if (rows.length === 0) return fail('該当するデータがありません');
      return json({ prefecture: data.prefecture, type, granularity, unit: '円 / 円毎㎡', source: SOURCE, rows });
    },
  );

  server.registerTool(
    'district_stats',
    {
      title: '地区別ランキング',
      description:
        '指定した年（または四半期）の地区別統計を並べ替えて返す。city 省略時は都道府県内の全地区が対象。',
      inputSchema: {
        prefecture: prefectureArg,
        city: z.string().optional().describe('市区町村。省略すると都道府県全体'),
        type: typeArg,
        period: z
          .string()
          .regex(/^\d{4}(Q[1-4])?$/)
          .optional()
          .describe('"2024" または "2024Q3"。省略時は収録済みの最新年'),
        sortBy: z.enum(['medianUnitPrice', 'medianPrice', 'count']).default('medianUnitPrice'),
        order: z.enum(['desc', 'asc']).default('desc'),
        minCount: z.number().int().min(1).default(5).describe('これ未満の件数の地区は除外'),
        limit: z.number().int().min(1).max(200).default(20),
      },
    },
    async ({ prefecture, city, type, period, sortBy, order, minCount, limit }) => {
      const { data, error } = loadPrefecture(store, prefecture);
      if (error) return fail(error);
      let cityCode = null;
      if (city) {
        cityCode = resolveCity(data, city);
        if (!cityCode) return fail(`市区町村を特定できません: ${city}`);
      }
      const target = period ?? data.periods.filter((p) => !p.includes('Q')).at(-1);
      const rows = data.rows
        .filter((r) => r[1] !== '' && r[2] === type && r[3] === target && r[4] >= minCount && (!cityCode || r[0] === cityCode))
        .map((r) => ({ ...toRow(r), cityName: data.cities[r[0]]?.name }))
        .filter((r) => r[sortBy] != null)
        .sort((a, b) => (order === 'desc' ? b[sortBy] - a[sortBy] : a[sortBy] - b[sortBy]));
      if (rows.length === 0) return fail(`該当するデータがありません（period=${target}, minCount=${minCount}）`);
      return json({
        prefecture: data.prefecture,
        type,
        period: target,
        sortBy,
        order,
        total: rows.length,
        source: SOURCE,
        rows: rows.slice(0, limit).map((r, i) => ({ rank: i + 1, ...r })),
      });
    },
  );

  return server;
}

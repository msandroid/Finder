// 合成データ → 集計 → gzip 書き出し → MCP サーバー経由の問い合わせ までを通しで確認する
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { Accumulator } from '../src/aggregate.js';
import { writeDataset, writeIndicators } from '../src/write.js';
import * as jp from '../scripts/sources/jp.js';
import * as gb from '../scripts/sources/gb.js';
import * as sg from '../scripts/sources/sg.js';
import { parseBis, parseFhfa } from '../scripts/sources/indicators.js';

const jpRec = (district, city, cityName, price, period = '2024年第1四半期') => ({
  Type: '中古マンション等',
  MunicipalityCode: city,
  Municipality: cityName,
  DistrictName: district,
  TradePrice: String(price),
  Area: '50',
  UnitPrice: '',
  Period: period,
});

// 日本 10件: 価格中央値 5,550万円、全件 50㎡ なので㎡単価中央値 111万円
const JP_RECORDS = [
  ...[40, 45, 50, 52, 55].map((m) => jpRec('西新宿', '13104', '新宿区', m * 1e6)),
  ...[56, 60, 62, 70, 80].map((m) => jpRec('神宮前', '13113', '渋谷区', m * 1e6)),
  // 集計対象外: 期間不正・価格なし
  jpRec('西新宿', '13104', '新宿区', 1e8, '不明'),
  jpRec('西新宿', '13104', '新宿区', ''),
];

const gbLine = (price, district, postcode, date = '2024-03-01 00:00') =>
  `"{X}","${price}","${date}","${postcode}","F","N","L","1","","HIGH ST","","LONDON","${district}","GREATER LONDON","A","A"`;

let dir;
let client;

before(async () => {
  dir = mkdtempSync(join(tmpdir(), 'world-realestate-'));

  const jpAcc = new Accumulator({ country: 'JP', currency: 'JPY' });
  for (const r of JP_RECORDS) jpAcc.add(jp.toTransaction('13', '東京都', r) ?? {});
  for (const ds of jpAcc.datasets()) writeDataset(dir, ds);

  const gbAcc = new Accumulator({ country: 'GB', currency: 'GBP' });
  for (const [p, d, pc] of [
    [900000, 'KENSINGTON AND CHELSEA', 'SW3 1AA'],
    [1100000, 'KENSINGTON AND CHELSEA', 'SW3 2BB'],
    [400000, 'HACKNEY', 'E8 1AA'],
  ]) gbAcc.add(gb.toTransaction(gbLine(p, d, pc)));
  for (const ds of gbAcc.datasets()) writeDataset(dir, ds);

  const sgAcc = new Accumulator({ country: 'SG', currency: 'SGD' });
  for (const [town, price] of [['BISHAN', 800000], ['BISHAN', 820000], ['WOODLANDS', 500000]]) {
    sgAcc.add(sg.toTransaction({ month: '2024-02', town, flat_type: '4 ROOM', floor_area_sqm: '100', resale_price: String(price) }));
  }
  for (const ds of sgAcc.datasets()) writeDataset(dir, ds);

  const bisRows = [];
  for (const [area, v2023, v2024] of [['JP', 100, 106], ['US', 200, 208], ['DE', 150, 147], ['XM', 100, 101]]) {
    for (let q = 1; q <= 4; q++) bisRows.push(`Q,${area},N,628,2023-Q${q},${v2023}`, `Q,${area},N,628,2024-Q${q},${v2024}`);
  }
  writeIndicators(dir, parseBis(['FREQ,REF_AREA,VALUE,UNIT_MEASURE,TIME_PERIOD,OBS_VALUE', ...bisRows].join('\n')));
  writeIndicators(
    dir,
    parseFhfa(
      [
        'hpi_type,hpi_flavor,frequency,level,place_name,place_id,yr,period,index_nsa,index_sa',
        'traditional,all-transactions,quarterly,State,California,CA,2023,4,1000,',
        'traditional,all-transactions,quarterly,State,California,CA,2024,4,1050,',
        'traditional,all-transactions,quarterly,State,Texas,TX,2023,4,800,',
        'traditional,all-transactions,quarterly,State,Texas,TX,2024,4,880,',
      ].join('\n'),
    ),
  );

  client = new Client({ name: 'test', version: '0.0.0' });
  await client.connect(
    new StdioClientTransport({
      command: process.execPath,
      args: [join(import.meta.dirname, '..', 'src', 'index.js')],
      env: { ...process.env, REALESTATE_DATA_DIR: dir },
    }),
  );
});

after(async () => {
  await client?.close();
  rmSync(dir, { recursive: true, force: true });
});

const call = async (name, args) => {
  const res = await client.callTool({ name, arguments: args });
  return { ...res, body: res.isError ? res.content[0].text : JSON.parse(res.content[0].text) };
};

test('6つのツールが公開される', async () => {
  const { tools } = await client.listTools();
  assert.deepEqual(tools.map((t) => t.name).sort(), [
    'country_ranking',
    'country_trends',
    'district_stats',
    'list_areas',
    'list_indicators',
    'market_stats',
  ]);
});

test('list_areas: 国一覧・地域・市区町村', async () => {
  const all = await call('list_areas', {});
  const avail = Object.fromEntries(all.body.transactions.map((c) => [c.code, c.available]));
  assert.deepEqual(avail, { JP: true, GB: true, FR: false, SG: true, TW: false, KR: false });
  assert.deepEqual(all.body.indicators.map((c) => c.code), ['DE', 'JP', 'US', 'XM']);

  const prefs = await call('list_areas', { country: 'JP' });
  assert.equal(prefs.body.regions.length, 47);
  assert.equal(prefs.body.regions.find((r) => r.code === '13').available, true);

  const cities = await call('list_areas', { country: 'jp', region: '東京' });
  assert.deepEqual(cities.body.cities.map((c) => c.name), ['新宿区', '渋谷区']);

  const districts = await call('list_areas', { region: '13', city: '新宿区' });
  assert.deepEqual(districts.body.city.districts, ['西新宿']);

  const us = await call('list_areas', { country: 'US' });
  assert.deepEqual(us.body.areas.map((a) => a.area), ['CA', 'TX']);
});

test('market_stats: 日本（既定の国）', async () => {
  const shinjuku = await call('market_stats', { region: '13', city: '新宿区' });
  assert.equal(shinjuku.body.currency, 'JPY');
  assert.deepEqual(shinjuku.body.rows[0], {
    city: '13104',
    district: '',
    type: '中古マンション等',
    period: '2024',
    count: 5,
    medianPrice: 50_000_000,
    medianUnitPrice: 1_000_000,
    unitPriceP25: 900_000,
    unitPriceP75: 1_040_000,
    cityName: '新宿区',
  });

  // 10件全体の中央値は集計ロジックで直接確認する
  const acc = new Accumulator({ country: 'JP', currency: 'JPY' });
  for (const r of JP_RECORDS) acc.add(jp.toTransaction('13', '東京都', { ...r, MunicipalityCode: '13000' }) ?? {});
  const [ds] = [...acc.datasets()];
  const [, , , , n, price, unit] = ds.rows.find((r) => r[1] === '' && r[3] === '2024');
  assert.deepEqual([n, price, unit], [10, 55_500_000, 1_110_000]);
});

test('market_stats: 英国（ポンド建て・㎡単価なし）とシンガポール（地域の省略）', async () => {
  const gbRes = await call('market_stats', { country: 'UK', region: 'Greater London', city: 'Kensington and Chelsea', granularity: 'quarter' });
  assert.equal(gbRes.body.currency, 'GBP');
  assert.match(gbRes.body.unitPrice, /㎡単価はありません/);
  assert.deepEqual(gbRes.body.rows.map((r) => [r.period, r.count, r.medianPrice, r.medianUnitPrice]), [['2024Q1', 2, 1_000_000, null]]);

  const sgRes = await call('market_stats', { country: 'SG', city: 'Bishan' });
  assert.deepEqual(sgRes.body.rows.map((r) => [r.type, r.medianPrice, r.medianUnitPrice]), [['4 ROOM', 810_000, 8_100]]);

  const badType = await call('market_stats', { country: 'SG', type: 'mansion' });
  assert.match(badType.body, /選べる種別: 4 ROOM/);
});

test('district_stats: 日本は地区、英国は郵便番号地区、シンガポールはタウンで並べる', async () => {
  const jpRank = await call('district_stats', { region: '13', minCount: 1 });
  assert.equal(jpRank.body.level, 'district');
  assert.deepEqual(jpRank.body.rows.map((r) => [r.rank, r.district, r.medianUnitPrice]), [
    [1, '神宮前', 1_240_000],
    [2, '西新宿', 1_000_000],
  ]);

  const gbRank = await call('district_stats', { country: 'GB', region: 'greater-london', minCount: 1 });
  assert.equal(gbRank.body.sortBy, 'medianPrice'); // ㎡単価がないので価格順
  assert.deepEqual(gbRank.body.rows.map((r) => [r.district, r.cityName, r.medianPrice]), [
    ['SW3', 'Kensington And Chelsea', 1_000_000],
    ['E8', 'Hackney', 400_000],
  ]);

  const sgRank = await call('district_stats', { country: 'SG', minCount: 1 });
  assert.equal(sgRank.body.level, 'city');
  assert.deepEqual(sgRank.body.rows.map((r) => r.cityName), ['Bishan', 'Woodlands']);
});

test('list_indicators / country_trends / country_ranking', async () => {
  const list = await call('list_indicators', {});
  const nominal = list.body.indicators.find((i) => i.id === 'bis_nominal');
  assert.equal(nominal.countries, 4);
  assert.equal(nominal.latest, '2024Q4');

  const jpInd = await call('list_indicators', { country: '日本' });
  assert.deepEqual(jpInd.body.national.map((s) => s.indicator), ['bis_nominal']);

  const trends = await call('country_trends', { indicator: 'bis_nominal', countries: ['Japan', 'アメリカ合衆国', 'ZZ'] });
  assert.deepEqual(trends.body.series.map((s) => [s.country, s.points.at(-1)]), [
    ['JP', { period: '2024', value: 106, yoyPct: 6 }],
    ['US', { period: '2024', value: 208, yoyPct: 4 }],
  ]);
  assert.deepEqual(trends.body.missing, ['ZZ']);

  // ユーロ圏（XM）などの集計値は国の比較から除く
  const rank = await call('country_ranking', { indicator: '住宅価格指数（名目）', metric: 'yoy' });
  assert.equal(rank.body.period, '2024Q4');
  assert.deepEqual(rank.body.rows.map((r) => [r.country, r.value]), [['JP', 6], ['US', 4], ['DE', -2]]);

  const states = await call('country_ranking', { indicator: 'fhfa_all_transactions', country: 'US', level: 'State' });
  assert.deepEqual(states.body.rows.map((r) => [r.area, r.value]), [['TX', 10], ['CA', 5]]);
});

test('エラー: 未収録・不明な入力', async () => {
  assert.equal((await call('market_stats', { region: '北海道' })).isError, true);
  assert.equal((await call('market_stats', { region: '13', city: '存在しない市' })).isError, true);
  assert.equal((await call('market_stats', { country: 'FR', region: '75' })).isError, true);
  assert.match((await call('market_stats', { country: 'US', region: 'CA' })).body, /country_trends/);
  assert.equal((await call('district_stats', { region: '13', minCount: 6 })).isError, true);
});

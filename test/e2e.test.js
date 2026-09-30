// 合成データ → 集計 → gzip 書き出し → MCP サーバー経由の問い合わせ までを通しで確認する
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { aggregate, parsePeriod } from '../src/aggregate.js';
import { writeDataset } from '../src/write.js';

const rec = (district, city, cityName, price, area = '50', period = '2024年第1四半期') => ({
  Type: '中古マンション等',
  MunicipalityCode: city,
  Municipality: cityName,
  DistrictName: district,
  TradePrice: String(price),
  Area: area,
  UnitPrice: '',
  Period: period,
});

// 10件: 価格中央値 5,550万円、全件 50㎡ なので㎡単価中央値 111万円
const RECORDS = [
  ...[40, 45, 50, 52, 55].map((m) => rec('西新宿', '13104', '新宿区', m * 1e6)),
  ...[56, 60, 62, 70, 80].map((m) => rec('神宮前', '13113', '渋谷区', m * 1e6)),
  // 集計対象外: 期間不正・価格なし
  rec('西新宿', '13104', '新宿区', 1e8, '50', '不明'),
  rec('西新宿', '13104', '新宿区', '', '50'),
];

let dir;
let client;

before(async () => {
  dir = mkdtempSync(join(tmpdir(), 'jp-realestate-'));
  writeDataset(dir, aggregate('13', '東京都', RECORDS));
  client = new Client({ name: 'test', version: '0.0.0' });
  await client.connect(
    new StdioClientTransport({
      command: process.execPath,
      args: [join(import.meta.dirname, '..', 'src', 'index.js')],
      env: { ...process.env, JP_REALESTATE_DATA_DIR: dir },
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

test('parsePeriod は全角数字も扱う', () => {
  assert.deepEqual(parsePeriod('２０２４年第３四半期'), { year: 2024, quarter: 3 });
  assert.equal(parsePeriod('不明'), null);
});

test('3つのツールが公開される', async () => {
  const { tools } = await client.listTools();
  assert.deepEqual(tools.map((t) => t.name).sort(), ['district_stats', 'list_areas', 'market_stats']);
});

test('list_areas: 都道府県と市区町村', async () => {
  const all = await call('list_areas', {});
  const tokyo = all.body.prefectures.find((p) => p.code === '13');
  assert.equal(tokyo.available, true);
  assert.equal(tokyo.from, '2024Q1');
  assert.equal(all.body.prefectures.find((p) => p.code === '01').available, false);

  const cities = await call('list_areas', { prefecture: '東京' });
  assert.deepEqual(cities.body.cities.map((c) => c.name), ['新宿区', '渋谷区']);

  const districts = await call('list_areas', { prefecture: '13', city: '新宿区' });
  assert.deepEqual(districts.body.city.districts, ['西新宿']);
});

test('market_stats: 都道府県全体の10件を集計', async () => {
  const res = await call('market_stats', { prefecture: '東京都', granularity: 'quarter' });
  assert.equal(res.isError, undefined);
  // city 未指定は市区町村ごとの行
  assert.equal(res.body.rows.length, 2);
  const counts = res.body.rows.map((r) => r.count);
  assert.deepEqual(counts, [5, 5]);
});

test('market_stats: 中央値と㎡単価', async () => {
  const shinjuku = await call('market_stats', { prefecture: '13', city: '新宿区' });
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
  const all = aggregate('13', '東京都', RECORDS.map((r) => ({ ...r, MunicipalityCode: '13000', DistrictName: '' })));
  const [, , , , n, price, unit] = all.rows.find((r) => r[3] === '2024');
  assert.deepEqual([n, price, unit], [10, 55_500_000, 1_110_000]);
});

test('district_stats: ㎡単価の高い順', async () => {
  const res = await call('district_stats', { prefecture: '13', minCount: 1 });
  assert.equal(res.body.period, '2024');
  assert.deepEqual(
    res.body.rows.map((r) => [r.rank, r.district, r.medianUnitPrice]),
    [
      [1, '神宮前', 1_240_000],
      [2, '西新宿', 1_000_000],
    ],
  );
});

test('エラー: 未収録・不明な入力', async () => {
  assert.equal((await call('market_stats', { prefecture: '北海道' })).isError, true);
  assert.equal((await call('market_stats', { prefecture: '13', city: '存在しない市' })).isError, true);
  assert.equal((await call('district_stats', { prefecture: '13', minCount: 6 })).isError, true);
});

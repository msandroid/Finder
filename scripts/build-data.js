#!/usr/bin/env node
// 不動産情報ライブラリ API (XIT001) から取引価格を取得し、data/ に集計データを書き出す。
//
//   REINFOLIB_API_KEY=... node scripts/build-data.js [--prefs 13,14] [--from 2020] [--to 2026]
//
// 取得に失敗した都道府県は既存ファイルを残したまま、最後に exit 1 で終了する。

import { parseArgs } from 'node:util';
import { aggregate } from '../src/aggregate.js';
import { PREFECTURES } from '../src/prefectures.js';
import { DEFAULT_DATA_DIR } from '../src/store.js';
import { writeDataset } from '../src/write.js';

const ENDPOINT = 'https://www.reinfolib.mlit.go.jp/ex-api/external/XIT001';
const now = new Date();
const currentYear = now.getFullYear();

const { values } = parseArgs({
  options: {
    prefs: { type: 'string' },
    from: { type: 'string', default: String(currentYear - 5) },
    to: { type: 'string', default: String(currentYear) },
    out: { type: 'string', default: process.env.JP_REALESTATE_DATA_DIR || DEFAULT_DATA_DIR },
  },
});

const apiKey = process.env.REINFOLIB_API_KEY;
if (!apiKey) {
  console.error('REINFOLIB_API_KEY が未設定です。https://www.reinfolib.mlit.go.jp/ で API キーを申請してください。');
  process.exit(1);
}

const prefs = values.prefs
  ? PREFECTURES.filter(([code]) => values.prefs.split(',').map((s) => s.trim().padStart(2, '0')).includes(code))
  : PREFECTURES;
const fromYear = Number(values.from);
const toYear = Number(values.to);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function fetchQuarter(area, year, quarter) {
  const url = `${ENDPOINT}?year=${year}&quarter=${quarter}&area=${area}&priceClassification=01`;
  for (let attempt = 1; ; attempt++) {
    const res = await fetch(url, { headers: { 'Ocp-Apim-Subscription-Key': apiKey } });
    // 未公表の四半期などデータなしは 404 で返る
    if (res.status === 404) return [];
    if (res.ok) {
      const body = await res.json();
      return Array.isArray(body.data) ? body.data : [];
    }
    if (res.status === 401 || res.status === 403) {
      throw new Error(`認証エラー (${res.status})。REINFOLIB_API_KEY を確認してください`);
    }
    if (attempt >= 5) throw new Error(`HTTP ${res.status}: ${url}`);
    await sleep(2 ** attempt * 1000);
  }
}

const failures = [];
for (const [code, name] of prefs) {
  try {
    const records = [];
    for (let year = fromYear; year <= toYear; year++) {
      for (let quarter = 1; quarter <= 4; quarter++) {
        if (year === currentYear && (quarter - 1) * 3 > now.getMonth()) break;
        records.push(...(await fetchQuarter(code, year, quarter)));
        await sleep(200);
      }
    }
    if (records.length === 0) throw new Error('レコードが0件でした');
    const dataset = aggregate(code, name, records);
    writeDataset(values.out, dataset);
    console.log(`${code} ${name}: ${records.length} 件 → ${dataset.rows.length} 行`);
  } catch (err) {
    console.error(`${code} ${name}: 失敗 — ${err.message}`);
    failures.push(code);
    if (/認証エラー/.test(err.message)) break;
  }
}

if (failures.length) {
  console.error(`失敗した都道府県: ${failures.join(', ')}`);
  process.exit(1);
}

#!/usr/bin/env node
// 各国のオープンデータを取得・集計して data/ に書き出す。
//
//   node scripts/build-data.js <source> [--only a,b] [--from 2021] [--to 2026] [--out data]
//
//   source: jp gb fr sg tw kr indicators
//   jp は REINFOLIB_API_KEY、kr は KOREA_DATA_API_KEY が必要
//
// 一部の地域だけ失敗した場合は、取得できた分を書き出してから exit 1 で終了する（既存ファイルは残る）。

import { parseArgs } from 'node:util';
import { DEFAULT_DATA_DIR } from '../src/store.js';

const SOURCES = ['jp', 'gb', 'fr', 'sg', 'tw', 'kr', 'indicators'];
const year = new Date().getFullYear();

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    only: { type: 'string' },
    // 後方互換: 以前の --prefs は --only と同じ
    prefs: { type: 'string' },
    from: { type: 'string', default: String(year - 5) },
    to: { type: 'string', default: String(year) },
    out: { type: 'string', default: process.env.REALESTATE_DATA_DIR || process.env.JP_REALESTATE_DATA_DIR || DEFAULT_DATA_DIR },
  },
});

const source = (positionals[0] ?? 'jp').toLowerCase();
if (!SOURCES.includes(source)) {
  console.error(`source は ${SOURCES.join(' / ')} のいずれかです: ${source}`);
  process.exit(2);
}

const onlyArg = values.only ?? values.prefs;
const only = onlyArg ? onlyArg.split(',').map((s) => s.trim()).filter(Boolean).map((s) => (source === 'jp' ? s.padStart(2, '0') : s)) : null;

const mod = await import(`./sources/${source}.js`);
try {
  const { failures } = await mod.build({
    out: values.out,
    from: Number(values.from),
    to: Number(values.to),
    only,
    env: process.env,
    log: (msg) => console.log(msg),
  });
  if (failures.length) {
    console.error(`${source}: 失敗した地域: ${failures.join(', ')}`);
    process.exit(1);
  }
} catch (err) {
  console.error(`${source}: ${err.message}`);
  process.exit(1);
}

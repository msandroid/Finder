// シンガポール: HDB 公営住宅の再販価格（data.gov.sg「Resale flat prices based on registration date from Jan-2017 onwards」）
import { Accumulator } from '../../src/aggregate.js';
import { COUNTRIES } from '../../src/countries.js';
import { writeDataset } from '../../src/write.js';
import { csvRecords } from '../lib/csv.js';
import { fetchRetry } from '../lib/net.js';
import { slug } from './gb.js';

const DATASET = 'd_8b84c4ee58e3cfc0ece0d773c8ca6abc';
const API = `https://api-open.data.gov.sg/v1/public/api/datasets/${DATASET}`;
const titleCase = (s) => s.toLowerCase().replace(/\b[a-z]/g, (c) => c.toUpperCase());

/** CSV のレコード → Accumulator.add の引数 */
export function toTransaction(rec) {
  const m = String(rec.month ?? '').match(/^(\d{4})-(\d{2})/);
  if (!m || !rec.town || !rec.flat_type) return null;
  return {
    region: 'sg',
    regionName: 'Singapore',
    city: slug(rec.town),
    cityName: titleCase(rec.town),
    type: rec.flat_type,
    year: Number(m[1]),
    quarter: Math.ceil(Number(m[2]) / 3),
    price: rec.resale_price,
    area: rec.floor_area_sqm,
  };
}

// data.gov.sg の一括ダウンロードは「生成開始 → 完了までポーリング → 署名付き URL を取得」の手順
async function downloadUrl() {
  await fetchRetry(`${API}/initiate-download`);
  for (let i = 0; i < 20; i++) {
    const res = await fetchRetry(`${API}/poll-download`);
    const body = res ? await res.json() : null;
    if (body?.data?.url) return body.data.url;
    await new Promise((r) => setTimeout(r, 3000));
  }
  throw new Error('data.gov.sg のダウンロード URL を取得できませんでした');
}

export async function build({ out, from, to, log }) {
  const res = await fetchRetry(await downloadUrl());
  if (!res) throw new Error('SG: CSV を取得できませんでした');
  const text = await res.text();
  const acc = new Accumulator({ country: 'SG', currency: COUNTRIES.SG.currency });
  let count = 0;
  for (const rec of csvRecords(text.split(/\r?\n/))) {
    const t = toTransaction(rec);
    if (t && t.year >= from && t.year <= to && acc.add(t)) count++;
  }
  if (count === 0) throw new Error('SG: レコードが0件でした');
  for (const ds of acc.datasets()) writeDataset(out, ds);
  log(`SG: ${count} 件`);
  return { failures: [] };
}

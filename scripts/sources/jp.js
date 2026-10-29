// 日本: 国土交通省 不動産情報ライブラリ API (XIT001)。要 REINFOLIB_API_KEY
import { Accumulator } from '../../src/aggregate.js';
import { COUNTRIES } from '../../src/countries.js';
import { PREFECTURES } from '../../src/prefectures.js';
import { writeDataset } from '../../src/write.js';
import { fetchRetry, HttpError } from '../lib/net.js';

const ENDPOINT = 'https://www.reinfolib.mlit.go.jp/ex-api/external/XIT001';
export const TYPES = ['中古マンション等', '宅地(土地と建物)', '宅地(土地)', '農地', '林地'];

const toHalfWidth = (s) => String(s ?? '').replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0));

// "2024年第1四半期" -> { year: 2024, quarter: 1 }
export function parsePeriod(period) {
  const m = toHalfWidth(period).match(/(\d{4})年第(\d)四半期/);
  return m ? { year: Number(m[1]), quarter: Number(m[2]) } : null;
}

/** XIT001 のレコード → Accumulator.add の引数 */
export function toTransaction(prefCode, prefName, rec) {
  const period = parsePeriod(rec.Period);
  if (!period || !TYPES.includes(rec.Type)) return null;
  return {
    region: prefCode,
    regionName: prefName,
    city: rec.MunicipalityCode,
    cityName: rec.Municipality,
    district: rec.DistrictName,
    type: rec.Type,
    ...period,
    price: rec.TradePrice,
    area: rec.Area,
    unitPrice: rec.UnitPrice,
  };
}

export async function build({ out, from, to, only, env, log }) {
  const apiKey = env.REINFOLIB_API_KEY;
  if (!apiKey) throw new Error('REINFOLIB_API_KEY が未設定です（https://www.reinfolib.mlit.go.jp/ で申請）');
  const now = new Date();
  const prefs = only ? PREFECTURES.filter(([c]) => only.includes(c)) : PREFECTURES;
  const failures = [];

  for (const [code, name] of prefs) {
    try {
      const acc = new Accumulator({ country: 'JP', currency: COUNTRIES.JP.currency });
      let count = 0;
      for (let year = from; year <= to; year++) {
        for (let quarter = 1; quarter <= 4; quarter++) {
          if (year === now.getFullYear() && (quarter - 1) * 3 > now.getMonth()) break;
          const url = `${ENDPOINT}?year=${year}&quarter=${quarter}&area=${code}&priceClassification=01`;
          const res = await fetchRetry(url, { headers: { 'Ocp-Apim-Subscription-Key': apiKey } });
          const records = res ? ((await res.json()).data ?? []) : []; // 未公表の四半期は 404
          for (const rec of records) if (acc.add(toTransaction(code, name, rec) ?? {})) count++;
          await new Promise((r) => setTimeout(r, 200));
        }
      }
      if (count === 0) throw new Error('レコードが0件でした');
      for (const ds of acc.datasets()) writeDataset(out, ds);
      log(`JP ${code} ${name}: ${count} 件`);
    } catch (err) {
      if (err instanceof HttpError && (err.status === 401 || err.status === 403)) {
        throw new Error(`認証エラー (${err.status})。REINFOLIB_API_KEY を確認してください`);
      }
      log(`JP ${code} ${name}: 失敗 — ${err.message}`);
      failures.push(code);
    }
  }
  return { failures };
}

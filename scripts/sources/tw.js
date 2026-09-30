// 台湾: 內政部 實價登錄（不動產買賣）。四半期ごとの ZIP に県市別の CSV（<英字>_lvr_land_a.csv）が入っている
// https://plvr.land.moi.gov.tw/DownloadOpenData
import { Accumulator } from '../../src/aggregate.js';
import { COUNTRIES } from '../../src/countries.js';
import { writeDataset } from '../../src/write.js';
import { csvRecords } from '../lib/csv.js';
import { fetchRetry } from '../lib/net.js';
import { listZip, readZipEntry } from '../lib/zip.js';

const URL = (season) => `https://plvr.land.moi.gov.tw/DownloadSeason?season=${season}&type=zip&fileName=lvr_landcsv.zip`;

export const CITIES = {
  a: '臺北市', b: '臺中市', c: '基隆市', d: '臺南市', e: '高雄市', f: '新北市', g: '宜蘭縣', h: '桃園市',
  i: '嘉義市', j: '新竹縣', k: '苗栗縣', m: '南投縣', n: '彰化縣', o: '新竹市', p: '雲林縣', q: '嘉義縣',
  t: '屏東縣', u: '花蓮縣', v: '臺東縣', w: '金門縣', x: '澎湖縣', z: '連江縣',
};

// 親族間・関係者間の取引や瑕疵物件など、相場から外れる取引は除く
const SPECIAL = /親友|員工|特殊|關係人|瑕疵|凶宅|債權|急買急賣|協議價購|政府機關/;

/** "1130105"（民国暦）→ { year: 2024, quarter: 1 } */
export function parseRocDate(s) {
  const m = String(s ?? '').trim().match(/^(\d{2,3})(\d{2})(\d{2})$/);
  if (!m) return null;
  const month = Number(m[2]);
  if (month < 1 || month > 12) return null;
  return { year: Number(m[1]) + 1911, quarter: Math.ceil(month / 3) };
}

/** CSV のレコード（中国語ヘッダー）→ Accumulator.add の引数 */
export function toTransaction(letter, rec) {
  if (!String(rec['交易標的'] ?? '').includes('建物')) return null;
  if (SPECIAL.test(rec['備註'] ?? '')) return null;
  const date = parseRocDate(rec['交易年月日']);
  const type = String(rec['建物型態'] ?? '').split('(')[0].trim();
  if (!date || !type || !rec['鄉鎮市區']) return null;
  return {
    region: letter,
    regionName: CITIES[letter] ?? letter,
    city: rec['鄉鎮市區'],
    cityName: rec['鄉鎮市區'],
    type,
    ...date,
    price: rec['總價元'],
    unitPrice: rec['單價元平方公尺'],
    area: rec['建物移轉總面積平方公尺'],
  };
}

/** ZIP 内の県市別 CSV（不動產買賣 = *_a.csv）を読んで Accumulator に追加する */
export function addSeason(acc, zipBuf) {
  let count = 0;
  for (const entry of listZip(zipBuf)) {
    const m = entry.name.match(/(?:^|\/)([a-z])_lvr_land_a\.csv$/i);
    if (!m) continue;
    const letter = m[1].toLowerCase();
    // 2行目は英語の見出しなので飛ばす
    const rows = readZipEntry(zipBuf, entry).toString('utf8').split(/\r?\n/).filter((l, i) => i !== 1);
    for (const rec of csvRecords(rows)) if (acc.add(toTransaction(letter, rec) ?? {})) count++;
  }
  return count;
}

export async function build({ out, from, to, log }) {
  const acc = new Accumulator({ country: 'TW', currency: COUNTRIES.TW.currency });
  let count = 0;
  for (let year = from; year <= to; year++) {
    for (let q = 1; q <= 4; q++) {
      const season = `${year - 1911}S${q}`;
      const res = await fetchRetry(URL(season));
      const buf = res ? Buffer.from(await res.arrayBuffer()) : null;
      // 未公表の四半期は ZIP ではなくエラーページが返ることがある
      if (!buf || buf.readUInt32LE(0) !== 0x04034b50) continue;
      const n = addSeason(acc, buf);
      log(`TW ${season}: ${n} 件`);
      count += n;
    }
  }
  if (count === 0) throw new Error('TW: レコードが0件でした');
  for (const ds of acc.datasets()) writeDataset(out, ds);
  return { failures: [] };
}

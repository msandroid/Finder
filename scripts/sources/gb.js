// 英国（イングランド・ウェールズ）: HM Land Registry Price Paid Data（年別 CSV、ヘッダーなし）
// https://www.gov.uk/government/statistical-data-sets/price-paid-data-downloads
import { Accumulator } from '../../src/aggregate.js';
import { COUNTRIES } from '../../src/countries.js';
import { writeDataset } from '../../src/write.js';
import { parseCsvLine } from '../lib/csv.js';
import { fetchRetry, lines } from '../lib/net.js';

const URL = (year) => `http://prod.publicdata.landregistry.gov.uk.s3-website-eu-west-1.amazonaws.com/pp-${year}.csv`;
export const TYPES = { D: 'detached', S: 'semi-detached', T: 'terraced', F: 'flat', O: 'other' };

export const slug = (s) => s.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
const titleCase = (s) => s.toLowerCase().replace(/\b[a-z]/g, (c) => c.toUpperCase());

/** CSV 1行 → Accumulator.add の引数。標準的な売買（カテゴリ A）以外は null */
export function toTransaction(line) {
  const f = parseCsvLine(line);
  if (f.length < 16) return null;
  const [, price, date, postcode, type, , , , , , , , district, county, category, status] = f;
  if (category !== 'A' || status === 'D' || !TYPES[type]) return null;
  const m = date.match(/^(\d{4})-(\d{2})/);
  if (!m || !county || !district) return null;
  return {
    region: slug(county),
    regionName: titleCase(county),
    city: slug(district),
    cityName: titleCase(district),
    district: postcode.split(' ')[0] || '',
    type: TYPES[type],
    year: Number(m[1]),
    quarter: Math.ceil(Number(m[2]) / 3),
    price,
  };
}

export async function build({ out, from, to, log }) {
  const acc = new Accumulator({ country: 'GB', currency: COUNTRIES.GB.currency });
  let count = 0;
  for (let year = from; year <= to; year++) {
    const res = await fetchRetry(URL(year));
    if (!res) {
      log(`GB ${year}: ファイルなし（未公表）`);
      continue;
    }
    let n = 0;
    for await (const line of lines(res)) if (acc.add(toTransaction(line) ?? {})) n++;
    log(`GB ${year}: ${n} 件`);
    count += n;
  }
  if (count === 0) throw new Error('GB: レコードが0件でした');
  for (const ds of acc.datasets()) writeDataset(out, ds);
  return { failures: [] };
}

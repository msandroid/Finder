// 不動産情報ライブラリ XIT001 の生レコードを、都道府県ごとの集計データに変換する。

export const TYPES = ['中古マンション等', '宅地(土地と建物)', '宅地(土地)', '農地', '林地'];

const toHalfWidth = (s) => String(s ?? '').replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0));

// "2024年第1四半期" -> { year: 2024, quarter: 1 }
export function parsePeriod(period) {
  const m = toHalfWidth(period).match(/(\d{4})年第(\d)四半期/);
  return m ? { year: Number(m[1]), quarter: Number(m[2]) } : null;
}

// 数値だけの文字列を数値に。"2000㎡以上" のような打ち切り値は null
function toNumber(v) {
  const s = toHalfWidth(v).replace(/,/g, '').trim();
  return /^\d+(\.\d+)?$/.test(s) ? Number(s) : null;
}

// ㎡単価: 公表値 (UnitPrice) があればそれ、なければ 取引価格 ÷ 面積
function unitPriceOf(rec, price) {
  const unit = toNumber(rec.UnitPrice);
  if (unit) return unit;
  const area = toNumber(rec.Area);
  return area ? Math.round(price / area) : null;
}

function quantile(sorted, q) {
  if (sorted.length === 0) return null;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return Math.round(sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo));
}

function summarize(prices, units) {
  prices.sort((a, b) => a - b);
  units.sort((a, b) => a - b);
  return {
    n: prices.length,
    price: quantile(prices, 0.5),
    unit: quantile(units, 0.5),
    unit25: quantile(units, 0.25),
    unit75: quantile(units, 0.75),
  };
}

/**
 * @param {string} prefCode 2桁の都道府県コード
 * @param {Iterable<object>} records XIT001 のレコード
 * @returns 集計データ。rows は [市区町村コード, 地区名("" は市区町村全体), 種別, 期間, 件数,
 *          価格中央値, ㎡単価中央値, ㎡単価25%点, ㎡単価75%点]
 */
export function aggregate(prefCode, prefName, records) {
  const buckets = new Map();
  const cities = {};

  const push = (key, price, unit) => {
    let b = buckets.get(key);
    if (!b) buckets.set(key, (b = { prices: [], units: [] }));
    b.prices.push(price);
    if (unit) b.units.push(unit);
  };

  for (const rec of records) {
    const period = parsePeriod(rec.Period);
    const price = toNumber(rec.TradePrice);
    const city = rec.MunicipalityCode;
    if (!period || !price || !city || !TYPES.includes(rec.Type)) continue;

    const district = (rec.DistrictName ?? '').trim();
    const c = (cities[city] ??= { name: rec.Municipality, districts: new Set() });
    if (district) c.districts.add(district);

    const unit = unitPriceOf(rec, price);
    for (const p of [String(period.year), `${period.year}Q${period.quarter}`]) {
      push([city, '', rec.Type, p].join('\t'), price, unit);
      if (district) push([city, district, rec.Type, p].join('\t'), price, unit);
    }
  }

  const rows = [];
  const periods = new Set();
  for (const [key, b] of buckets) {
    const [city, district, type, period] = key.split('\t');
    const s = summarize(b.prices, b.units);
    periods.add(period);
    rows.push([city, district, type, period, s.n, s.price, s.unit, s.unit25, s.unit75]);
  }
  rows.sort((a, b) => a.slice(0, 4).join('\t').localeCompare(b.slice(0, 4).join('\t')));

  return {
    version: 1,
    prefecture: { code: prefCode, name: prefName },
    generatedAt: new Date().toISOString(),
    periods: [...periods].sort(),
    cities: Object.fromEntries(
      Object.entries(cities)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([code, c]) => [code, { name: c.name, districts: [...c.districts].sort() }]),
    ),
    rows,
  };
}

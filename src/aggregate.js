// 取引レコードを 地域(region) → 市区町村(city) → 地区(district) × 種別 × 期間 で集計する。
// 各国のデータ取得処理 (scripts/sources/*) が共通で使う。

const toHalfWidth = (s) => String(s ?? '').replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0));

// 数値だけの文字列を数値に。"2000㎡以上" のような打ち切り値や空欄は null
export function toNumber(v) {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  const s = toHalfWidth(v).replace(/[,\s]/g, '');
  return /^\d+(\.\d+)?$/.test(s) ? Number(s) : null;
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
  return [prices.length, quantile(prices, 0.5), quantile(units, 0.5), quantile(units, 0.25), quantile(units, 0.75)];
}

/**
 * rows の各行: [市区町村コード, 地区名("" は市区町村全体), 種別, 期間, 件数,
 *              価格中央値, ㎡単価中央値, ㎡単価25%点, ㎡単価75%点]
 * 期間は "2024"（年）と "2024Q1"（四半期）の両方を持つ。
 */
export class Accumulator {
  constructor({ country, currency }) {
    this.country = country;
    this.currency = currency;
    this.regions = new Map();
  }

  /**
   * @param {object} t
   * @param {string} t.region 地域コード（ファイル名になるので英数字とハイフンのみ）
   * @param {string} t.regionName
   * @param {string} t.city 市区町村コード（または一意な名称）
   * @param {string} t.cityName
   * @param {string} [t.district]
   * @param {string} t.type 物件種別
   * @param {number} t.year
   * @param {number} t.quarter 1-4
   * @param {number} t.price
   * @param {number|null} [t.area] 面積（㎡）。単価の計算に使う
   * @param {number|null} [t.unitPrice] 公表されている㎡単価。あれば area より優先
   */
  add(t) {
    const price = toNumber(t.price);
    if (!price || !t.region || !t.city || !t.type || !t.year || !(t.quarter >= 1 && t.quarter <= 4)) return false;
    const area = toNumber(t.area);
    const unit = toNumber(t.unitPrice) || (area ? Math.round(price / area) : null);
    const district = String(t.district ?? '').trim();

    let r = this.regions.get(t.region);
    if (!r) this.regions.set(t.region, (r = { name: t.regionName, cities: new Map(), buckets: new Map(), types: new Set() }));
    let c = r.cities.get(t.city);
    if (!c) r.cities.set(t.city, (c = { name: t.cityName ?? t.city, districts: new Set() }));
    if (district) c.districts.add(district);
    r.types.add(t.type);

    for (const p of [String(t.year), `${t.year}Q${t.quarter}`]) {
      this.#push(r, [t.city, '', t.type, p].join('\t'), price, unit);
      if (district) this.#push(r, [t.city, district, t.type, p].join('\t'), price, unit);
    }
    return true;
  }

  #push(r, key, price, unit) {
    let b = r.buckets.get(key);
    if (!b) r.buckets.set(key, (b = { prices: [], units: [] }));
    b.prices.push(price);
    if (unit) b.units.push(unit);
  }

  /** 地域ごとのデータセットを返す */
  *datasets(extra = {}) {
    const generatedAt = new Date().toISOString();
    for (const [code, r] of [...this.regions].sort(([a], [b]) => a.localeCompare(b))) {
      const rows = [];
      const periods = new Set();
      for (const [key, b] of r.buckets) {
        const [city, district, type, period] = key.split('\t');
        periods.add(period);
        rows.push([city, district, type, period, ...summarize(b.prices, b.units)]);
      }
      rows.sort((a, b) => a.slice(0, 4).join('\t').localeCompare(b.slice(0, 4).join('\t')));
      yield {
        version: 2,
        country: this.country,
        currency: this.currency,
        region: { code, name: r.name },
        generatedAt,
        ...extra,
        types: [...r.types].sort(),
        periods: [...periods].sort(),
        cities: Object.fromEntries(
          [...r.cities]
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([cc, c]) => [cc, { name: c.name, districts: [...c.districts].sort() }]),
        ),
        rows,
      };
    }
  }
}

import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const DEFAULT_DATA_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', 'data');
const MAX_CACHED = 3;

const readJson = (file) => JSON.parse(readFileSync(file, 'utf8'));
const readGz = (file) => JSON.parse(gunzipSync(readFileSync(file)).toString('utf8'));

// data/<国>/<地域>.json.gz と data/indicators/<出典>.json.gz を必要な分だけ読み込み、
// 地域データは最近使った3件だけ保持する
export class Store {
  constructor(dataDir = process.env.REALESTATE_DATA_DIR || process.env.JP_REALESTATE_DATA_DIR || DEFAULT_DATA_DIR) {
    this.dataDir = dataDir;
    this.cache = new Map();
    this.indicatorCache = new Map();
  }

  /** 国ごとの data/<国>/index.json */
  countryIndex(country) {
    const file = join(this.dataDir, country.toLowerCase(), 'index.json');
    return existsSync(file) ? readJson(file) : null;
  }

  /** 取引データを収録済みの国コード一覧 */
  countries() {
    if (!existsSync(this.dataDir)) return [];
    return readdirSync(this.dataDir, { withFileTypes: true })
      .filter((d) => d.isDirectory() && d.name !== 'indicators' && existsSync(join(this.dataDir, d.name, 'index.json')))
      .map((d) => d.name.toUpperCase())
      .sort();
  }

  load(country, region) {
    const key = `${country}/${region}`;
    if (this.cache.has(key)) {
      const hit = this.cache.get(key);
      this.cache.delete(key);
      this.cache.set(key, hit);
      return hit;
    }
    const file = join(this.dataDir, country.toLowerCase(), `${region}.json.gz`);
    if (!existsSync(file)) return null;
    const data = readGz(file);
    this.cache.set(key, data);
    if (this.cache.size > MAX_CACHED) this.cache.delete(this.cache.keys().next().value);
    return data;
  }

  /** 収録済みの指標データセット（BIS・OECD・FHFA など）をすべて返す */
  indicatorSets() {
    const dir = join(this.dataDir, 'indicators');
    if (!existsSync(dir)) return [];
    return readdirSync(dir)
      .filter((f) => f.endsWith('.json.gz'))
      .sort()
      .map((f) => {
        const file = join(dir, f);
        if (!this.indicatorCache.has(file)) this.indicatorCache.set(file, readGz(file));
        return this.indicatorCache.get(file);
      });
  }
}

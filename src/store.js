import { existsSync, readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const DEFAULT_DATA_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', 'data');
const MAX_CACHED = 3;

// data/<都道府県コード>.json.gz を必要な分だけ読み込み、最近使った3件だけ保持する
export class Store {
  constructor(dataDir = process.env.JP_REALESTATE_DATA_DIR || DEFAULT_DATA_DIR) {
    this.dataDir = dataDir;
    this.cache = new Map();
  }

  index() {
    const file = join(this.dataDir, 'index.json');
    return existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : { prefectures: [] };
  }

  load(prefCode) {
    if (this.cache.has(prefCode)) {
      const hit = this.cache.get(prefCode);
      this.cache.delete(prefCode);
      this.cache.set(prefCode, hit);
      return hit;
    }
    const file = join(this.dataDir, `${prefCode}.json.gz`);
    if (!existsSync(file)) return null;
    const data = JSON.parse(gunzipSync(readFileSync(file)).toString('utf8'));
    this.cache.set(prefCode, data);
    if (this.cache.size > MAX_CACHED) this.cache.delete(this.cache.keys().next().value);
    return data;
  }
}

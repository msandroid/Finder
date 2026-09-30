import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { join } from 'node:path';

const gz = (value) => gzipSync(JSON.stringify(value), { level: 9 });

// 地域の集計データを data/<国>/<地域>.json.gz に書き出し、data/<国>/index.json を更新する
export function writeDataset(dataDir, dataset) {
  const dir = join(dataDir, dataset.country.toLowerCase());
  mkdirSync(dir, { recursive: true });
  const { code, name } = dataset.region;
  writeFileSync(join(dir, `${code}.json.gz`), gz(dataset));

  const indexFile = join(dir, 'index.json');
  const index = existsSync(indexFile) ? JSON.parse(readFileSync(indexFile, 'utf8')) : { regions: [] };
  const quarters = dataset.periods.filter((p) => p.includes('Q'));
  const entry = {
    code,
    name,
    cities: Object.keys(dataset.cities).length,
    types: dataset.types,
    from: quarters[0] ?? null,
    to: quarters.at(-1) ?? null,
    generatedAt: dataset.generatedAt,
  };
  index.country = dataset.country;
  index.currency = dataset.currency;
  index.regions = [...index.regions.filter((r) => r.code !== code), entry].sort((a, b) => a.code.localeCompare(b.code));
  index.updatedAt = dataset.generatedAt;
  writeFileSync(indexFile, JSON.stringify(index, null, 2) + '\n');
}

// 指標データセットを data/indicators/<出典>.json.gz に書き出す
export function writeIndicators(dataDir, set) {
  const dir = join(dataDir, 'indicators');
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, `${set.source}.json.gz`), gz(set));
}

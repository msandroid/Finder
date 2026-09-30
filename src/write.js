import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { join } from 'node:path';

// 集計データを data/<コード>.json.gz に書き出し、data/index.json を更新する
export function writeDataset(dataDir, dataset) {
  mkdirSync(dataDir, { recursive: true });
  const { code, name } = dataset.prefecture;
  writeFileSync(join(dataDir, `${code}.json.gz`), gzipSync(JSON.stringify(dataset), { level: 9 }));

  const indexFile = join(dataDir, 'index.json');
  const index = existsSync(indexFile) ? JSON.parse(readFileSync(indexFile, 'utf8')) : { prefectures: [] };
  const entry = {
    code,
    name,
    periods: dataset.periods.filter((p) => p.includes('Q')),
    cities: Object.keys(dataset.cities).length,
    generatedAt: dataset.generatedAt,
  };
  index.prefectures = [...index.prefectures.filter((p) => p.code !== code), entry].sort((a, b) =>
    a.code.localeCompare(b.code),
  );
  index.updatedAt = dataset.generatedAt;
  writeFileSync(indexFile, JSON.stringify(index, null, 2) + '\n');
}

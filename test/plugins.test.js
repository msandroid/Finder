// 配布用バンドル (dist/) と各クライアント向けプラグイン定義の確認
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { aggregate } from '../src/aggregate.js';
import { writeDataset } from '../src/write.js';

const ROOT = join(import.meta.dirname, '..');
const BUNDLE = 'dist/jp-realestate-mcp.mjs';
const readJson = (p) => JSON.parse(readFileSync(join(ROOT, p), 'utf8'));

test('各プラグイン定義がバンドルを指している', () => {
  const version = readJson('package.json').version;
  const expected = {
    '.claude-plugin/plugin.json': `\${CLAUDE_PLUGIN_ROOT}/${BUNDLE}`,
    '.cursor-plugin/plugin.json': `\${CURSOR_PLUGIN_ROOT}/${BUNDLE}`,
    '.codex-plugin/plugin.json': `./${BUNDLE}`,
  };
  for (const [file, arg] of Object.entries(expected)) {
    const manifest = readJson(file);
    assert.equal(manifest.name, 'jp-realestate', file);
    assert.equal(manifest.version, version, `${file} の version が package.json と一致しない`);
    assert.deepEqual(manifest.mcpServers['jp-realestate'].args, [arg], file);
  }
  assert.equal(readJson('.claude-plugin/marketplace.json').plugins[0].source, './');
  assert.equal(readJson('.agents/plugins/marketplace.json').plugins[0].source.path, './');
  assert.ok(existsSync(join(ROOT, BUNDLE)), 'npm run bundle で dist/ を生成してください');
});

test('バンドルは node_modules なしで起動し、同じ結果を返す', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'jp-realestate-bundle-'));
  const rec = (district, city, name, m) => ({
    Type: '中古マンション等', MunicipalityCode: city, Municipality: name, DistrictName: district,
    TradePrice: String(m * 1e6), Area: '50', Period: '2024年第1四半期',
  });
  writeDataset(dir, aggregate('13', '東京都', [rec('西新宿', '13104', '新宿区', 50), rec('神宮前', '13113', '渋谷区', 60)]));

  // 外部パッケージを import していない（node: 組み込みだけ）ことを確認する
  const imports = [...readFileSync(join(ROOT, BUNDLE), 'utf8').matchAll(/\bfrom\s*["']([^"']+)["']/g)].map((m) => m[1]);
  assert.deepEqual(imports.filter((s) => !s.startsWith('node:')), []);

  const client = new Client({ name: 'test', version: '0.0.0' });
  await client.connect(
    new StdioClientTransport({
      command: process.execPath,
      args: [join(ROOT, BUNDLE)],
      env: { PATH: process.env.PATH, JP_REALESTATE_DATA_DIR: dir },
    }),
  );
  try {
    const { tools } = await client.listTools();
    assert.equal(tools.length, 3);
    const res = await client.callTool({ name: 'district_stats', arguments: { prefecture: '13', minCount: 1 } });
    assert.deepEqual(JSON.parse(res.content[0].text).rows.map((r) => r.district), ['神宮前', '西新宿']);
  } finally {
    await client.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

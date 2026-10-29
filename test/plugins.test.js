// 配布用バンドル (dist/) と各クライアント向けプラグイン定義の確認
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { Accumulator } from '../src/aggregate.js';
import * as jp from '../scripts/sources/jp.js';
import { writeDataset } from '../src/write.js';

const ROOT = join(import.meta.dirname, '..');
const BUNDLE = 'dist/world-realestate-mcp.mjs';
const readJson = (p) => JSON.parse(readFileSync(join(ROOT, p), 'utf8'));
const marketplacePlugin = (file, name) => readJson(file).plugins.find((p) => p.name === name);

test('各プラグイン定義がバンドルを指している', () => {
  const version = readJson('package.json').version;
  const expected = {
    '.claude-plugin/plugin.json': `\${CLAUDE_PLUGIN_ROOT}/${BUNDLE}`,
    '.cursor-plugin/plugin.json': `\${CURSOR_PLUGIN_ROOT}/${BUNDLE}`,
    '.codex-plugin/plugin.json': `./${BUNDLE}`,
  };
  for (const [file, arg] of Object.entries(expected)) {
    const manifest = readJson(file);
    assert.equal(manifest.name, 'world-realestate', file);
    assert.equal(manifest.version, version, `${file} の version が package.json と一致しない`);
    assert.deepEqual(manifest.mcpServers['world-realestate'].args, [arg], file);
  }
  assert.equal(marketplacePlugin('.claude-plugin/marketplace.json', 'world-realestate').source, './');
  assert.equal(marketplacePlugin('.agents/plugins/marketplace.json', 'world-realestate').source.path, './');
  assert.ok(existsSync(join(ROOT, BUNDLE)), 'npm run bundle で dist/ を生成してください');
});

test('japan-prop のプラグイン定義が MCP サーバーとスキルを指している', () => {
  const claude = readJson('japan-prop/.claude-plugin/plugin.json');
  const codex = readJson('japan-prop/.codex-plugin/plugin.json');
  for (const manifest of [claude, codex]) {
    assert.equal(manifest.name, 'japan-prop');
    assert.ok(manifest.mcpServers['jp-property-finder'].args.includes('mcp_server.py'));
  }
  assert.ok(claude.mcpServers['jp-property-finder'].args.includes('${CLAUDE_PLUGIN_ROOT}'));
  for (const skill of ['japan-rental-search', 'property-research']) {
    assert.ok(existsSync(join(ROOT, 'japan-prop', codex.skills, skill, 'SKILL.md')), skill);
  }
  assert.ok(existsSync(join(ROOT, 'japan-prop/mcp_server.py')));
  const agent = readFileSync(join(ROOT, 'japan-prop/agents/property-researcher.md'), 'utf8');
  assert.match(agent, /^model: haiku$/m, 'property-researcher は Haiku で動く');

  const entry = marketplacePlugin('.claude-plugin/marketplace.json', 'japan-prop');
  assert.equal(entry.source, './japan-prop');
  assert.equal(entry.version, claude.version);
  assert.equal(marketplacePlugin('.agents/plugins/marketplace.json', 'japan-prop').source.path, './japan-prop');
});

test('バンドルは node_modules なしで起動し、同じ結果を返す', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'world-realestate-bundle-'));
  const rec = (district, city, name, m) => ({
    Type: '中古マンション等', MunicipalityCode: city, Municipality: name, DistrictName: district,
    TradePrice: String(m * 1e6), Area: '50', Period: '2024年第1四半期',
  });
  const acc = new Accumulator({ country: 'JP', currency: 'JPY' });
  for (const r of [rec('西新宿', '13104', '新宿区', 50), rec('神宮前', '13113', '渋谷区', 60)]) acc.add(jp.toTransaction('13', '東京都', r));
  for (const ds of acc.datasets()) writeDataset(dir, ds);

  // 外部パッケージを import していない（node: 組み込みだけ）ことを確認する
  const imports = [...readFileSync(join(ROOT, BUNDLE), 'utf8').matchAll(/\bfrom\s*["']([^"']+)["']/g)].map((m) => m[1]);
  assert.deepEqual(imports.filter((s) => !s.startsWith('node:')), []);

  const client = new Client({ name: 'test', version: '0.0.0' });
  await client.connect(
    new StdioClientTransport({
      command: process.execPath,
      args: [join(ROOT, BUNDLE)],
      env: { PATH: process.env.PATH, REALESTATE_DATA_DIR: dir },
    }),
  );
  try {
    const { tools } = await client.listTools();
    assert.equal(tools.length, 6);
    const res = await client.callTool({ name: 'district_stats', arguments: { prefecture: '13', minCount: 1 } });
    assert.deepEqual(JSON.parse(res.content[0].text).rows.map((r) => r.district), ['神宮前', '西新宿']);
  } finally {
    await client.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

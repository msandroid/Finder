# jp-realestate-mcp

国土交通省「[不動産情報ライブラリ](https://www.reinfolib.mlit.go.jp/)」の不動産取引価格を集計し、そのデータを同梱した MCP サーバーです。
LLM から市区町村・地区ごとの売買相場の推移や、地区別のランキングを問い合わせられます。

- 実行時にネットワークも API キーも要りません。集計済みデータ（`data/*.json.gz`）を読むだけです。
- 読み込むのは問い合わせた都道府県のファイルだけで、同時に保持するのは最大3件です。
- 依存パッケージは `@modelcontextprotocol/sdk` と `zod` だけで、配布用に1ファイルにまとめてあります。
- Claude Code・Codex・Cursor のプラグインとして入れられるほか、任意の MCP クライアントにも登録できます。
- データは GitHub Actions が毎月5日に自動で更新します。

> **注意**: 収録しているのは**売買の取引価格**だけです。賃料は含みません（後述）。

## ツール

| ツール | 内容 |
| --- | --- |
| `list_areas` | 収録済みの都道府県、市区町村、地区の一覧 |
| `market_stats` | 件数・価格中央値・㎡単価（中央値と25/75%点）の年別または四半期別推移。市区町村・地区で絞り込み可 |
| `district_stats` | 指定した年・四半期の地区別ランキング（㎡単価・価格・件数で並べ替え） |

共通の引数:

- `prefecture`: `"13"`、`"東京都"`、`"東京"` のどれでも指定できます。
- `city`: 市区町村コード（`"13104"`）か名称（`"新宿区"`）。
- `type`: `中古マンション等`（既定）、`宅地(土地と建物)`、`宅地(土地)`、`農地`、`林地`。

㎡単価は、公表値（`UnitPrice`）があればその値を使い、なければ「取引価格 ÷ 面積」で計算します。`宅地(土地と建物)` の面積は土地面積です。「2000㎡以上」のように面積が打ち切られている取引は、㎡単価の計算から除外します。

## インストール

サーバー本体は依存パッケージを同梱した単一ファイル `dist/jp-realestate-mcp.mjs` です。Node.js 20 以上があれば `npm install` なしで動きます。
以下は Claude Code・Codex・Cursor のプラグインとして入れる方法と、任意の MCP クライアントに直接登録する方法です。

### Claude Code（プラグイン）

```bash
claude plugin marketplace add msandroid/Finder
claude plugin install jp-realestate@jp-realestate
```

セッション内で `/plugin marketplace add msandroid/Finder` から入れることもできます。定義は `.claude-plugin/` にあります。

### Codex（プラグイン）

```bash
codex plugin marketplace add msandroid/Finder
```

追加後、Codex で `/plugins` を開き、`jp-realestate` をインストールします。定義は `.codex-plugin/plugin.json` と `.agents/plugins/marketplace.json` にあります。

### Cursor（プラグイン）

定義は `.cursor-plugin/plugin.json` にあります。ローカルで使う場合は、リポジトリを `~/.cursor/plugins/local/` に置いて Cursor を再読み込み（**Developer: Reload Window**）します。

```bash
git clone https://github.com/msandroid/Finder ~/.cursor/plugins/local/jp-realestate
```

チームで配布する場合は、Cursor の team marketplace にこのリポジトリを登録します。

### MCP サーバーとして直接登録する

リポジトリを clone し、各クライアントの設定に `dist/jp-realestate-mcp.mjs` を登録します。

```bash
git clone https://github.com/msandroid/Finder jp-realestate-mcp
```

**Claude Code**

```bash
claude mcp add jp-realestate -- node /path/to/jp-realestate-mcp/dist/jp-realestate-mcp.mjs
```

**Claude Desktop**（`claude_desktop_config.json`）と **Cursor**（`~/.cursor/mcp.json` またはプロジェクトの `.cursor/mcp.json`）

```json
{
  "mcpServers": {
    "jp-realestate": {
      "command": "node",
      "args": ["/path/to/jp-realestate-mcp/dist/jp-realestate-mcp.mjs"]
    }
  }
}
```

**Codex**

```bash
codex mcp add jp-realestate -- node /path/to/jp-realestate-mcp/dist/jp-realestate-mcp.mjs
```

または `~/.codex/config.toml` に次を追加します。

```toml
[mcp_servers.jp-realestate]
command = "node"
args = ["/path/to/jp-realestate-mcp/dist/jp-realestate-mcp.mjs"]
```

### データの更新を取り込む

データは毎月このリポジトリにコミットされます。取り込み方はインストール方法によって異なります。

- プラグインで入れた場合：各クライアントの marketplace 更新（例: `claude plugin marketplace update jp-realestate`、`codex plugin marketplace upgrade`）
- clone した場合：`git pull`

### 開発者向け

`src/` を変更したら `npm run bundle` で `dist/` を作り直してコミットしてください。`npm test` も先にバンドルを作ります。CI（`ci.yml`）は、`dist/` がソースと一致しない場合に失敗します。

## データ

`data/<都道府県コード>.json.gz` は都道府県ごとの gzip 圧縮 JSON です。`rows` の各行は次の形式です。

```
[市区町村コード, 地区名, 種別, 期間, 件数, 価格中央値, ㎡単価中央値, ㎡単価25%点, ㎡単価75%点]
```

- 地区名が `""` の行は市区町村全体の集計です。
- 期間は `"2024"`（年）または `"2024Q1"`（四半期）です。年の値は四半期の値から計算したものではなく、その年の全取引から直接集計しています。
- `data/index.json` には、収録済みの都道府県、期間、生成日時が入っています。

### 自動更新

`.github/workflows/refresh-data.yml` が毎月5日 12:00（日本時間）に実行され、直近5年分を取得し直して `data/` の変更をコミットします。

初回のセットアップ:

1. 不動産情報ライブラリで API キーを申請します（無料）。
2. リポジトリの **Settings → Secrets and variables → Actions** に `REINFOLIB_API_KEY` を登録します。
3. **Actions → refresh-data → Run workflow** で手動実行します。都道府県コードを指定すれば、一部の都道府県だけ試すこともできます。

一部の都道府県で取得に失敗した場合、その都道府県の既存ファイルはそのまま残ります。取得に成功した分だけがコミットされ、ワークフローは失敗として終わります。

### 手元での生成

```bash
REINFOLIB_API_KEY=xxxx npm run build-data -- --prefs 13,14 --from 2021
```

| オプション | 既定値 | 内容 |
| --- | --- | --- |
| `--prefs` | 全47都道府県 | 都道府県コード（カンマ区切り） |
| `--from` / `--to` | 5年前 / 今年 | 取得する年の範囲 |
| `--out` | `data/` | 出力先 |

出力先は環境変数 `JP_REALESTATE_DATA_DIR` でも変えられます。この変数はサーバーの読み込み先にも使います。

## テスト

```bash
npm test
```

合成データ10件を使い、集計、gzip の書き出し、MCP サーバー（stdio）経由の問い合わせまでを通しで確認します。
あわせて、配布用バンドルが外部パッケージなしで起動すること、各プラグイン定義がバンドルを正しく指していることも確認します。

## 賃貸相場について

賃料は収録していません。賃料の取引単位のデータは公的なオープンデータとして公開されていません。また、不動産ポータルサイトのスクレイピングは利用規約上行わない方針です。
公的統計（住宅・土地統計調査の家賃集計など）を市区町村単位で追加することは検討しています。

## 出典

このサービスは、国土交通省 不動産情報ライブラリの API 機能を使用していますが、提供情報の最新性、正確性、完全性等が保証されたものではありません。

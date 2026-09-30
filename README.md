# world-realestate-mcp

世界の不動産相場を LLM から調べられる MCP サーバーです。各国の公的オープンデータを集計したデータを同梱しています。

- **取引データ**（6か国）: 地域・市区町村・地区ごとの取引件数、価格中央値、㎡単価（中央値と25/75%点）の推移とランキング
- **指標データ**（約60か国）: 住宅価格指数（名目・実質）、家賃指数、価格/所得比など。国どうしの比較やランキングに使えます。米国は州・都市圏ごとの価格指数もあります。

特徴:

- 実行時にネットワークも API キーも要りません。集計済みのデータ（`data/`）を読むだけです。
- 依存パッケージ（`@modelcontextprotocol/sdk` と `zod`）を1ファイルにまとめてあります。Node.js 20 以上があれば `npm install` なしで動きます。
- Claude Code・Codex・Cursor のプラグインとして入れられ、任意の MCP クライアントにも登録できます。
- データは GitHub Actions が毎月自動で更新します。

## 収録データ

### 取引データ

| 国 | 出典 | 地域 / 市区町村 / 地区 | 種別 | ㎡単価 | API キー |
| --- | --- | --- | --- | --- | --- |
| 日本 | 国土交通省 不動産情報ライブラリ | 都道府県 / 市区町村 / 地区 | 中古マンション等、土地と建物、土地 など | あり | `REINFOLIB_API_KEY` |
| 英国（イングランド・ウェールズ） | HM Land Registry Price Paid Data | カウンティ / 地方自治体 / 郵便番号地区（SW1A など） | flat, terraced, semi-detached, detached | なし（面積の公表なし） | 不要 |
| フランス | DVF（DGFiP / Etalab） | 県 / コミューン（パリは区） | Appartement, Maison | あり | 不要 |
| シンガポール | HDB 公営住宅の再販価格（data.gov.sg） | 国 / タウン | 3 ROOM, 4 ROOM, EXECUTIVE など | あり | 不要 |
| 台湾 | 內政部 實價登錄 | 直轄市・県市 / 郷鎮市区 | 住宅大樓、華廈、公寓、透天厝 など | あり（公表値） | 不要 |
| 韓国 | 국토교통부 아파트 매매 실거래가 | 広域市 / 区 / 法定洞 | 아파트（マンション） | あり（専有面積） | `KOREA_DATA_API_KEY` |

- 期間は既定で直近5年分です。
- 価格は各国の通貨建てです（円、ポンド、ユーロ、シンガポールドル、台湾ドル、ウォン）。
- 集計の前に、次の取引を除いています。
  - 英国: 標準的な売買でない取引（PPD カテゴリ B）
  - フランス: 複数戸の一括売買や、店舗を含む取引
  - 台湾: 親族間など特殊な事情のある取引
  - 韓国: 解除された取引

### 指標データ（国別・四半期）

| 出典 | 内容 | 対象 |
| --- | --- | --- |
| BIS Selected residential property prices | 住宅価格指数（名目・実質、2010=100） | 約60か国・地域 |
| OECD Analytical house price indicators | 住宅価格指数（名目・実質）、**家賃指数**、価格/所得比、価格/家賃比 | OECD 加盟国と主要な非加盟国 |
| FHFA House Price Index | 住宅価格指数（全取引・購入のみ） | 米国の全米・州・都市圏 |

米国は、全国規模の取引データが公開されていないため指標だけです。

## ツール

| ツール | 内容 |
| --- | --- |
| `list_areas` | 収録国の一覧。国を指定すると地域、地域を指定すると市区町村と物件種別、市区町村を指定すると地区を返す |
| `market_stats` | 取引データの推移（年別または四半期別）。市区町村・地区で絞り込める |
| `district_stats` | 取引データのエリア別ランキング（㎡単価・価格・件数で並べ替え） |
| `list_indicators` | 指標の一覧。国を指定すると、その国で使える指標と地域（米国の州など） |
| `country_trends` | 複数の国の指標の推移を並べて返す（前年比付き） |
| `country_ranking` | 前年比、N年間の変化率、または値そのもので国（または米国の州・都市圏）を並べる |

引数の例:

- `country`: `"JP"`、`"GB"`（`"UK"` も可）、`"FR"`、`"SG"`、`"TW"`、`"KR"`。取引データのツールで省略すると日本になります。
- `region` / `city`: コードでも名称でも指定できます（例: `"東京都"`、`"Greater London"`、`"75"`、`"臺北市"`）。
- 指標ツールの国は、コード（`"JP"`）でも国名（`"日本"`、`"Japan"`）でも指定できます。

LLM への質問の例:

- 「東京都の区別の中古マンション㎡単価ランキング」
- 「ロンドンのハックニーのテラスハウス価格の推移」
- 「日本・米国・英国・ドイツの実質住宅価格を2010年から比較して」
- 「家賃が直近1年でいちばん上がった国は？」
- 「米国で住宅価格の5年上昇率が高い州」

## インストール

サーバー本体は `dist/world-realestate-mcp.mjs` の1ファイルです。

### Claude Code（プラグイン）

```bash
claude plugin marketplace add msandroid/Finder
claude plugin install world-realestate@world-realestate
```

セッション内で `/plugin marketplace add msandroid/Finder` から入れることもできます。定義は `.claude-plugin/` にあります。

### Codex（プラグイン）

```bash
codex plugin marketplace add msandroid/Finder
```

追加したら Codex で `/plugins` を開き、`world-realestate` をインストールします。定義は `.codex-plugin/plugin.json` と `.agents/plugins/marketplace.json` にあります。

### Cursor（プラグイン）

定義は `.cursor-plugin/plugin.json` にあります。ローカルで使う場合は、リポジトリを `~/.cursor/plugins/local/` に置いて Cursor を再読み込みします（**Developer: Reload Window**）。

```bash
git clone https://github.com/msandroid/Finder ~/.cursor/plugins/local/world-realestate
```

チームで配布する場合は、Cursor の team marketplace にこのリポジトリを登録します。

### MCP サーバーとして直接登録する

```bash
git clone https://github.com/msandroid/Finder world-realestate-mcp
```

**Claude Code**

```bash
claude mcp add world-realestate -- node /path/to/world-realestate-mcp/dist/world-realestate-mcp.mjs
```

**Claude Desktop**（`claude_desktop_config.json`）と **Cursor**（`~/.cursor/mcp.json` またはプロジェクトの `.cursor/mcp.json`）

```json
{
  "mcpServers": {
    "world-realestate": {
      "command": "node",
      "args": ["/path/to/world-realestate-mcp/dist/world-realestate-mcp.mjs"]
    }
  }
}
```

**Codex**

```bash
codex mcp add world-realestate -- node /path/to/world-realestate-mcp/dist/world-realestate-mcp.mjs
```

または `~/.codex/config.toml` に次を追加します。

```toml
[mcp_servers.world-realestate]
command = "node"
args = ["/path/to/world-realestate-mcp/dist/world-realestate-mcp.mjs"]
```

### データの更新を取り込む

データは毎月このリポジトリにコミットされます。

- プラグインで入れた場合: 各クライアントで marketplace を更新します（例: `claude plugin marketplace update world-realestate`、`codex plugin marketplace upgrade`）。
- clone した場合: `git pull` します。

## データ

```
data/
  <国コード>/index.json          収録地域の一覧
  <国コード>/<地域>.json.gz      地域ごとの集計データ
  indicators/<出典>.json.gz      指標（bis / oecd / fhfa）
```

地域ファイルの `rows` の各行は次の形式です。

```
[市区町村コード, 地区名, 種別, 期間, 件数, 価格中央値, ㎡単価中央値, ㎡単価25%点, ㎡単価75%点]
```

- 地区名が `""` の行は、市区町村全体の集計です。
- 期間は `"2024"`（年）または `"2024Q1"`（四半期）です。年の値は四半期の値から計算しているのではなく、その年の全取引から直接集計しています。

### 自動更新

`.github/workflows/refresh-data.yml` は毎月5日 12:00（日本時間）に全データを取得し直し、`data/` の変更をコミットします。

- 一部のデータの取得に失敗しても、残りの取得とコミットは続けます。失敗した地域の既存ファイルは残り、ワークフローは最後に失敗として終わります。
- API キーが要る日本と韓国は、Secrets が未登録ならスキップします。

Secrets の登録（**Settings → Secrets and variables → Actions**）:

| Secret | 用途 | 申請先 |
| --- | --- | --- |
| `REINFOLIB_API_KEY` | 日本 | [不動産情報ライブラリ](https://www.reinfolib.mlit.go.jp/)（無料） |
| `KOREA_DATA_API_KEY` | 韓国 | [공공데이터포털](https://www.data.go.kr/data/15126469/openapi.do)「국토교통부_아파트 매매 실거래가 자료」（無料） |

**Actions → refresh-data → Run workflow** で手動実行もできます。`sources` に `gb,indicators` のように書くと、一部のデータだけ更新できます。

### 手元での生成

```bash
node scripts/build-data.js <source> [--only a,b] [--from 2021] [--to 2026] [--out data]
```

| source | 内容 | `--only` の例 |
| --- | --- | --- |
| `jp` | 日本（要 `REINFOLIB_API_KEY`） | 都道府県コード `13,14` |
| `gb` | 英国 | なし（全国を1回で処理） |
| `fr` | フランス | 県コード `75,92,93,94` |
| `sg` | シンガポール | なし |
| `tw` | 台湾 | なし |
| `kr` | 韓国（要 `KOREA_DATA_API_KEY`） | 市郡区コード `11680,11650`。既定はソウル・釜山・仁川 |
| `indicators` | BIS・OECD・FHFA | `bis,oecd` |

出力先は環境変数 `REALESTATE_DATA_DIR` でも変えられます。この変数はサーバーの読み込み先にも使います。

## 開発

```bash
npm ci
npm test
```

- `npm test` は `dist/` を作り直してからテストします。
- テスト内容:
  - 各国のデータ形式に合わせたサンプルでのパーサーの確認
  - 合成データでの集計、gzip の書き出し、MCP サーバー（stdio）経由の問い合わせ
  - バンドルが外部パッケージなしで起動すること
  - 各プラグイン定義がバンドルを指していること
- `src/` を変更したら `npm run bundle` で `dist/` を作り直してコミットしてください。CI（`ci.yml`）は、`dist/` がソースと食い違うと失敗します。

## 注意点

- 取引データの中央値は、その期間に売れた物件の構成に左右されます。件数の少ない地区は値が大きくぶれます。`district_stats` は既定で5件未満のエリアを除きます。
- 国ごとに定義が違うため、取引データの価格や㎡単価を国の間でそのまま比べるのは適切ではありません。
  - 日本は取引価格、台湾は登記された実価格、シンガポールは公営住宅のみ、韓国はマンションのみです。
  - 国どうしの比較には指標データを使ってください。
- 家賃の水準（金額）は収録していません。家賃の変化は OECD の家賃指数で国単位で見られます。ポータルサイトのスクレイピングは利用規約上行わない方針です。
- 取引データの対象外:
  - 英国: スコットランドと北アイルランド
  - フランス: アルザス・モゼル地方とマヨット（DVF が収録していないため）

## 出典とライセンス

データは各出典のライセンスに従います。

| 出典 | ライセンス |
| --- | --- |
| 国土交通省 不動産情報ライブラリ | このサービスは、国土交通省 不動産情報ライブラリの API 機能を使用していますが、提供情報の最新性、正確性、完全性等が保証されたものではありません |
| HM Land Registry Price Paid Data | Contains HM Land Registry data © Crown copyright and database right. Open Government Licence v3.0 |
| DVF | Licence Ouverte / Open Licence 2.0（DGFiP） |
| HDB Resale flat prices | Singapore Open Data Licence（data.gov.sg） |
| 實價登錄 | 政府資料開放授權條款 第1版（內政部） |
| 국토교통부 실거래가 | 공공누리 제1유형（출처표시） |
| BIS | BIS の利用規約（出典明記） |
| OECD | CC BY 4.0 |
| FHFA | 米国政府著作物（パブリックドメイン） |

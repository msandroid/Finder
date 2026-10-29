# Finder: 日本の不動産・賃貸探し

LLM から日本の賃貸・売買物件を探せる、Claude Code と Codex のプラグインです。同じマーケットプレイスに2つのプラグインがあります。

| プラグイン | 内容 | 場所 |
| --- | --- | --- |
| **`japan-prop`**（メイン） | 日本の賃貸・売買物件をポータルサイト横断で探し、災害リスクや周辺環境まで調べる | `japan-prop/` |
| `world-realestate` | 公的オープンデータによる世界の不動産相場（取引価格・住宅価格指数・家賃指数） | リポジトリ直下 |

## 日本の不動産・賃貸探し（japan-prop）

- スーモ・ヤフー不動産・ニフティ不動産の賃貸・売買物件（賃貸、マンション、戸建て、売地、空き家、収益物件）を横断検索し、重複掲載をまとめる
- 家賃・価格、駅徒歩、面積、周辺相場との乖離（割安度）で絞り込み・並べ替え
- 国土地理院の標高と、国土交通省ハザードマップ（洪水・津波・土砂災害）による災害リスクの確認
- 半径800m以内の駅・スーパー・病院・学校・公園までの距離
- 物件どうしの比較表、ウォッチリスト
- 売買物件向けに、建築ボリュームと投資利回りの試算、2D 平面図・3D モデルの生成

賃貸探し用のスキル（`japan-rental-search`）も入っています。希望条件の聞き取りから、検索、災害リスクの確認、比較までを順に進めます。

> **非公開物件について**
>
> 日本では、ポータルサイトに載っている物件がすべてではありません。不動産事業者（宅地建物取引業者）しか閲覧できない**非公開物件**があります。
>
> - 業者向けの物件情報システム（レインズなど）にだけ登録されている物件
> - 貸主・売主の意向で広告に出さない物件
> - 管理会社や地元の不動産会社が直接扱う物件
>
> このプラグインで検索できるのはポータルサイトに掲載された物件だけです。非公開物件も含めて探したいときは、希望エリアの不動産会社に条件を伝えて紹介してもらってください。

LLM への質問の例:

- 「品川区か目黒区で、家賃12万円以下、駅徒歩10分以内の1LDKを探して」
- 「この3件を、浸水リスクと周辺施設も含めて比べて」
- 「港区の売地を、周辺相場より割安な順に」

### 必要なもの

- [uv](https://docs.astral.sh/uv/)（Python 3.11 以上は uv が用意します）
- 実行時のネットワーク接続（ポータルサイトと国土地理院などの API を使います）

### Claude Code

```bash
claude plugin marketplace add msandroid/Finder
claude plugin install japan-prop@world-realestate
```

セッション内では `/plugin marketplace add msandroid/Finder` のあと `/plugin install japan-prop@world-realestate` でも入れられます。定義は `japan-prop/.claude-plugin/plugin.json` にあります。

### Codex

```bash
codex plugin marketplace add msandroid/Finder
```

追加したら Codex で `/plugins` を開き、`japan-prop` をインストールします。定義は `japan-prop/.codex-plugin/plugin.json` と `.agents/plugins/marketplace.json` にあります。

### プラグインを使わずに登録する

```bash
git clone https://github.com/msandroid/Finder
claude mcp add jp-property-finder -- uv run --directory /path/to/Finder/japan-prop python mcp_server.py
```

### Web UI・CLI

```bash
cd japan-prop
uv run python start.py   # Web UI（http://127.0.0.1:8000）。先に frontend/ で npm install && npm run build
uv run python cli.py --help
uv run pytest -v
```

詳しくは [japan-prop/README.md](japan-prop/README.md) を見てください。元のリポジトリは [msandroid/japan-prop](https://github.com/msandroid/japan-prop) で、履歴ごと取り込んであります。

## 世界の不動産相場（world-realestate）

世界の不動産相場を LLM から調べられる MCP サーバーです。各国の公的オープンデータを集計したデータを同梱しています。

- **取引データ**（6か国）: 地域・市区町村・地区ごとの取引件数、価格中央値、㎡単価（中央値と25/75%点）の推移とランキング
- **指標データ**（約60か国）: 住宅価格指数（名目・実質）、家賃指数、価格/所得比など。国どうしの比較やランキングに使えます。米国は州・都市圏ごとの価格指数もあります。

特徴:

- 実行時にネットワークも API キーも要りません。集計済みのデータ（`data/`）を読むだけです。
- 依存パッケージ（`@modelcontextprotocol/sdk` と `zod`）を1ファイルにまとめてあります。Node.js 20 以上があれば `npm install` なしで動きます。
- Claude Code・Codex・Cursor のプラグインとして入れられ、任意の MCP クライアントにも登録できます。
- データは GitHub Actions が毎月自動で更新します。

### 収録データ

#### 取引データ

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

#### 指標データ（国別・四半期）

| 出典 | 内容 | 対象 |
| --- | --- | --- |
| BIS Selected residential property prices | 住宅価格指数（名目・実質、2010=100） | 約60か国・地域 |
| OECD Analytical house price indicators | 住宅価格指数（名目・実質）、**家賃指数**、価格/所得比、価格/家賃比 | OECD 加盟国と主要な非加盟国 |
| FHFA House Price Index | 住宅価格指数（全取引・購入のみ） | 米国の全米・州・都市圏 |

米国は、全国規模の取引データが公開されていないため指標だけです。

### ツール

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

### インストール

サーバー本体は `dist/world-realestate-mcp.mjs` の1ファイルです。

#### Claude Code（プラグイン）

```bash
claude plugin marketplace add msandroid/Finder
claude plugin install world-realestate@world-realestate
```

セッション内で `/plugin marketplace add msandroid/Finder` から入れることもできます。定義は `.claude-plugin/` にあります。

#### Codex（プラグイン）

```bash
codex plugin marketplace add msandroid/Finder
```

追加したら Codex で `/plugins` を開き、`world-realestate` をインストールします。定義は `.codex-plugin/plugin.json` と `.agents/plugins/marketplace.json` にあります。

#### Cursor（プラグイン）

定義は `.cursor-plugin/plugin.json` にあります。ローカルで使う場合は、リポジトリを `~/.cursor/plugins/local/` に置いて Cursor を再読み込みします（**Developer: Reload Window**）。

```bash
git clone https://github.com/msandroid/Finder ~/.cursor/plugins/local/world-realestate
```

チームで配布する場合は、Cursor の team marketplace にこのリポジトリを登録します。

#### MCP サーバーとして直接登録する

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

#### データの更新を取り込む

データは毎月このリポジトリにコミットされます。

- プラグインで入れた場合: 各クライアントで marketplace を更新します（例: `claude plugin marketplace update world-realestate`、`codex plugin marketplace upgrade`）。
- clone した場合: `git pull` します。

### データ

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

#### 自動更新

`.github/workflows/refresh-data.yml` は毎月5日 12:00（日本時間）に全データを取得し直し、`data/` の変更をコミットします。

- 一部のデータの取得に失敗しても、残りの取得とコミットは続けます。失敗した地域の既存ファイルは残り、ワークフローは最後に失敗として終わります。
- API キーが要る日本と韓国は、Secrets が未登録ならスキップします。

Secrets の登録（**Settings → Secrets and variables → Actions**）:

| Secret | 用途 | 申請先 |
| --- | --- | --- |
| `REINFOLIB_API_KEY` | 日本 | [不動産情報ライブラリ](https://www.reinfolib.mlit.go.jp/)（無料） |
| `KOREA_DATA_API_KEY` | 韓国 | [공공데이터포털](https://www.data.go.kr/data/15126469/openapi.do)「국토교통부_아파트 매매 실거래가 자료」（無料） |

**Actions → refresh-data → Run workflow** で手動実行もできます。`sources` に `gb,indicators` のように書くと、一部のデータだけ更新できます。

#### 手元での生成

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
- 家賃の水準（金額）は収録していません。家賃の変化は OECD の家賃指数で国単位で見られます。
- world-realestate-mcp は公的オープンデータだけを使い、ポータルサイトのスクレイピングは行いません。
- `japan-prop/` は、スーモ・ヤフー不動産・ニフティ不動産の検索結果を実行時に取得します。使う前に各サイトの利用規約を確認し、アクセスの頻度を抑えてください。取得した物件情報の再配布や商用利用は、各サイトの規約に従ってください。
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

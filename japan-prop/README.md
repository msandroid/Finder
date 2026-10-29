# japan-prop: 全国不動産・土地網羅検索プラットフォーム

日本全国47都道府県・約1,900市区町村を対象に、不動産・土地（売地・戸建て・マンション・空き家・収益物件・賃貸）を複数ポータルからリアルタイムに横断検索・名寄せ統合し、国土地理院の標高・ハザードマップ・周辺施設解析および建築ボリューム・投資利回り試算を行う統合プラットフォームです。

**Claude（Claude Code / Claude Desktop）、Codex（Codex CLI）、Antigravity** と MCP（Model Context Protocol）・CLI・WebSocket を介してネイティブに双方向連携します。

## 主な機能

1. **複数ポータル横断リアルタイム検索・自動名寄せ統合**
   - スーモ（売地・中古戸建・新築戸建・中古マンション・賃貸）、ヤフー不動産、ニフティ不動産（LIFULL HOME'S・アットホーム等統合）を並列検索。
   - 町丁目・面積・価格帯に基づく正規化ハッシュにより、複数サイトに重複掲載された同一物件を自動名寄せし、価格変動履歴を記録。
   - 市区町村×種別ごとの平均単価から各物件の「周辺相場乖離率（割安度）」を自動算出。
2. **国土地理院・国土交通省ハザードマップ・周辺環境解析**
   - 国土地理院 AddressSearch API によるジオコーディングと、1m/5m レーザ測量 DEM による精密標高取得。
   - 国土交通省ハザードマップポータルサイト（洪水浸水想定・津波浸水想定・土砂災害警戒区域・土石流）のリアルタイム重ね合わせおよび判定。
   - OpenStreetMap Overpass API による半径 800m 圏内の生活利便施設（駅・スーパー・病院・学校・公園）の実距離算出。
3. **建築基準法ボリューム試算・不動産投資収支シミュレーター**
   - 前面道路幅員制限（住居系40%・商業系60%）および角地緩和を考慮した実効建蔽率・実効容積率・最大建築面積・最大延床面積・構造別概算建築費の算出。
   - 表面利回り・実質利回り・NOI（純営業収益）・元利均等返済額・税引前キャッシュフロー・CCR（自己資金配当率）の試算。
4. **日本の詳細な建築2D平面図・都市等高線図および3Dモデル生成（SVG / DXF / OBJ / glTF 2.0 出力対応）**
   - **個別敷地・建物スケール**: 敷地境界・セットバック・前面道路・寸法線・方位・各階間取り（壁厚・柱・窓記号・扉開閉円弧・帖数/平米表記）を備えた2D CAD建築平面図と、道路斜線・北側斜線3D制限枠および季節（冬至・春分・夏至）×時刻（8時〜16時）別の日影投影を備えた対話型3D建物モデルを自動生成。
   - **日本全国・都市地形スケール**: 国土地理院の数値標高モデル（DEM5A / DEM10B）から生成する3D標高メッシュ・マーチングスクエア法による等高線と、国土地理院公式ベクトルタイル（`experimental_bvmap`）から抽出する実在建物フットプリント・道路網を統合した2D都市平面図＆3D都市地形モデルを生成。
   - 2Dベクター図面（`.svg`）、AutoCAD等対応設計図面（`.dxf`）、Wavefront 3Dメッシュ（`.obj`）、標準3Dモデル（`.gltf`）として即時ファイル出力。
5. **Claude / Codex / Antigravity ネイティブ連携**
   - MCPサーバー（`mcp_server.py` / `.mcp.json`）および CLI（`cli.py`）を標準装備。
   - Web UI とエージェント間で選択物件・地図範囲・多角形（ポリゴン）検索範囲・評価スコア（100点満点）・2D平面図/3Dモデルをリアルタイム共有。

## Claude Code・Codex のプラグインとして使う

このフォルダは [msandroid/Finder](https://github.com/msandroid/Finder) のマーケットプレイスに `japan-prop` として登録されています。[uv](https://docs.astral.sh/uv/) が必要です。

```bash
# Claude Code
claude plugin marketplace add msandroid/Finder
claude plugin install japan-prop@world-realestate

# Codex（追加後に /plugins から japan-prop をインストール）
codex plugin marketplace add msandroid/Finder
```

Claude Code のセッション内では `/plugin install japan-prop --marketplace msandroid/Finder` でも入れられます。MCP サーバー `jp-property-finder` と、賃貸探し用のスキル `japan-rental-search` が使えるようになります。

## 非公開物件について

日本では、ポータルサイトに載っている物件がすべてではありません。不動産事業者（宅地建物取引業者）しか閲覧できない**非公開物件**があります。たとえば、業者向けの物件情報システム（レインズなど）にだけ登録されている物件、貸主・売主の意向で広告に出さない物件、管理会社や地元の不動産会社が直接扱う物件です。

このツールで検索できるのはポータルサイトに掲載された物件だけです。非公開物件も含めて探したいときは、希望エリアの不動産会社に条件を伝えて紹介してもらってください。

## セットアップと起動方法

### 1. フロントエンドのビルド（初回のみ）

```powershell
cd frontend
npm install
npm run build
cd ..
```

### 2. サーバーおよび Web UI の起動

```powershell
uv run python start.py
```

ブラウザで `http://127.0.0.1:8000` を開くと Web UI が利用できます。

### 3. CLI からの実行例

```powershell
# リアルタイム横断検索＆DB名寄せ統合
uv run python cli.py search --prefecture 東京都 --municipality 港区 --category 売地 --agent antigravity

# ローカルDBの高速フィルタ検索（割安物件のみ・JSON出力）
uv run python cli.py query --prefecture 東京都 --category 売地 --only-undervalued --sort-by deviation_asc --format json

# 国土地理院標高・ハザードマップ・周辺施設の空間解析
uv run python cli.py analyze --property-id 4 --agent claude

# エージェントによる物件評価スコアと所見の登録
uv run python cli.py evaluate --property-id 4 --agent codex --score 92 --recommendation 強く推奨 --pros "3ポータル共通掲載・駅近" --cons "前面道路幅員の実測確認推奨"

# 個別敷地・建物の2D詳細平面図および3Dモデル生成（SVG / DXF / OBJ / glTF 出力）
uv run python cli.py generate-cad3d --property-id 4 --stories 3 --floor-plan 4LDK --agent antigravity

# 国土地理院DEM標高メッシュ・等高線・実在街区3D都市モデル生成
uv run python cli.py generate-urban3d --property-id 4 --radius 240 --agent claude
```

### 4. テストの実行

```powershell
uv run pytest -v
```

# 全国不動産・土地網羅検索プラットフォーム（Claude / Codex / Antigravity ネイティブ連携ガイド）

このリポジトリは、日本全国47都道府県・約1,900市区町村を対象に、不動産・土地（売地・戸建て・マンション・空き家・収益物件・賃貸）をリアルタイムに横断検索・名寄せ統合し、国土地理院の標高・ハザードマップ・周辺施設解析および建築ボリューム・投資利回り試算を行う統合プラットフォームです。

## 1. MCPサーバー経由でのネイティブ呼び出し

`.mcp.json` に `jp-property-finder` が定義されており、以下のMCPツールを直接呼び出せます。

- `live_search_japan_properties`: 指定した都道府県・市区町村・物件種別について、スーモ・ヤフー不動産・ニフティ不動産を並列検索し、国土地理院APIで座標と標高を付与してSQLiteへ名寄せ保存します。
- `query_local_properties`: ローカルDBに蓄積された物件を価格・面積・駅徒歩・周辺相場割安度・並び順で高速検索します。
- `get_property_detail`: 物件番号（ID）の全詳細、各ポータルURL、価格推移履歴、各エージェントの評価履歴を取得します。
- `analyze_property_hazard_and_surroundings`: 物件座標の精密標高（国土地理院DEM）、国土交通省ハザードマップ（洪水・津波・土砂災害・土石流）指定有無、半径800m以内の生活利便施設（OpenStreetMap Overpass）をリアルタイム解析します。
- `simulate_land_building_volume_tool`: 建築基準法（前面道路幅員による容積率制限・角地緩和）に基づく最大建築面積・最大延床面積・構造別概算建築費を計算します。
- `simulate_investment_yield_tool`: 表面利回り・実質利回り・NOI・元利均等返済額・税引前キャッシュフロー・CCRを試算します。
- `record_agent_property_evaluation`: `claude` / `codex` / `antigravity` として物件の評価スコア（0〜100点）、推奨判定、強み・懸念点・所見をDBおよびWeb UIへ記録します。
- `manage_watchlist_tool`: 重点調査案件リストへの物件追加・一覧取得を行います。
- `compare_properties_tool`: 複数物件の横断比較レポート（Markdown表＋JSON）を生成します。
- `sync_with_web_ui`: 人間がWeb UIで選択中の物件や地図範囲を読み取り、またはエージェント側の注目物件へWeb UIの地図フォーカスを移動します。

## 2. CLI経由でのネイティブ呼び出し（Codex CLI / Claude Code / Antigravity ターミナル実行用）

```powershell
# リアルタイム横断検索＆DB名寄せ統合
uv run python cli.py search --prefecture 東京都 --municipality 港区 --category 売地 --agent codex

# ローカルDBの高速フィルタ検索（割安物件のみ・JSON出力）
uv run python cli.py query --prefecture 東京都 --category 売地 --only-undervalued --sort-by deviation_asc --format json

# 国土地理院標高・ハザードマップ・周辺施設の空間解析
uv run python cli.py analyze --property-id 4 --agent codex

# エージェントによる物件評価スコアと所見の登録
uv run python cli.py evaluate --property-id 4 --agent antigravity --score 92 --recommendation 強く推奨 --pros "3ポータル共通掲載・駅近" --cons "前面道路幅員の実測確認推奨"

# 複数物件の比較レポート生成（Markdown出力）
uv run python cli.py compare --ids 1 4 17 --format markdown

# 建築ボリューム試算
uv run python cli.py simulate-volume --land-area 120 --coverage 60 --far 200 --road-width 5.4 --structure 重量鉄骨造

# 投資利回り・キャッシュフロー試算
uv run python cli.py simulate-yield --price 85000000 --monthly-rent 480000 --down-payment 15000000 --interest 1.8 --years 30

# Web UIの現在の選択状態・ハイライト連動
uv run python cli.py ui-context --highlight-ids 4 17 --agent codex --message "港区の注目売地2件をピックアップしました"
```

## 3. Web UI / API サーバーの起動

```powershell
uv run python start.py
```
ブラウザで `http://127.0.0.1:8000` を開くと、国土地理院地図・ハザードマップ重ね合わせ・リアルタイム同期対応のWeb UIが利用できます。

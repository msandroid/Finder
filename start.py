import asyncio
import sys
import uvicorn

from backend.database import get_database_stats, init_db
from backend.connectors.aggregator import live_search_and_sync


async def ensure_initial_real_data() -> None:
    init_db()
    stats = get_database_stats()
    if stats["total_properties"] == 0:
        print("[情報] 初回起動のため、東京都港区（売地・戸建て）の実物件データを各ポータルから収集しています...")
        await live_search_and_sync(
            prefecture="東京都",
            municipality="港区",
            category="売地",
            agent_name="antigravity",
        )
        await live_search_and_sync(
            prefecture="東京都",
            municipality="港区",
            category="戸建て",
            agent_name="antigravity",
        )
        new_stats = get_database_stats()
        print(f"[成功] 初期実データ収集完了：合計 {new_stats['total_properties']} 件の物件を登録しました。")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(ensure_initial_real_data())
    print("[情報] 全国不動産・土地網羅検索システムを起動します: http://127.0.0.1:8000")
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()

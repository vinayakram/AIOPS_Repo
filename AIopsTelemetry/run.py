"""
Entry point for starting the AIops Telemetry Server via uvicorn.
Parses --host, --port, and --reload command-line arguments, defaulting to values from
the server settings module, and launches the FastAPI application defined in server.main.
Intended as a lightweight development and production launcher for the telemetry backend.

uvicornを使ってAIops Telemetryサーバーを起動するエントリーポイント。
--host、--port、--reloadのコマンドライン引数を解析し、デフォルト値はserverのsettingsから取得する。
server.mainに定義されたFastAPIアプリケーションを起動する薄いラッパー。
テレメトリバックエンドの開発・本番両環境でのシンプルな起動手段として使用する。
"""
import argparse
import uvicorn
from server.config import settings

if __name__ == "__main__":
    # 開発時に `python run.py --port ...` で簡単に起動できる薄いラッパー。
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=settings.HOST)
    parser.add_argument("--port", type=int, default=settings.PORT)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    # 実体の FastAPI アプリは `server.main:app` に集約している。
    uvicorn.run(
        "server.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )

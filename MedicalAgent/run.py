"""
Uvicorn entry point for the MedicalAgent backend server. Binds to all interfaces on port
8002 and supports optional hot-reload mode controlled by the MEDICAL_AGENT_RELOAD environment
variable, excluding virtual environment and cache directories from reload watching.

MedicalAgentバックエンドサーバのuvicornエントリポイント。全インターフェースのポート8002に
バインドし、MEDICAL_AGENT_RELOAD環境変数で制御されるオプションのホットリロードモードを
サポートする。仮想環境およびキャッシュディレクトリはリロード監視から除外される。
"""
import os

import uvicorn


if __name__ == "__main__":
    reload_enabled = os.getenv("MEDICAL_AGENT_RELOAD", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8002,
        reload=reload_enabled,
        reload_dirs=["backend", "frontend"] if reload_enabled else None,
        reload_excludes=[".venv/*", ".git/*", ".pytest_cache/*", "__pycache__/*"]
        if reload_enabled
        else None,
    )

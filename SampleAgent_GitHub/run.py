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

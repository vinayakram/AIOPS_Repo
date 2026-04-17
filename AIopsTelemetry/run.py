"""
Start the AIops Telemetry Server.

Usage:
    python run.py
    python run.py --port 7001
"""
import argparse
import uvicorn
from server.config import settings

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=settings.HOST)
    parser.add_argument("--port", type=int, default=settings.PORT)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run(
        "server.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )

"""One-command launcher for the Democracy Platform prototype.

Usage:
    python run.py          # starts the app on http://localhost:8400
"""
import os

import uvicorn

if __name__ == "__main__":
    # The background queue worker is on by default when running the app
    # (export RUN_WORKER=0 to disable). Tests never set it, so the worker
    # thread stays off there and the suite remains deterministic.
    os.environ.setdefault("RUN_WORKER", "1")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8400, reload=False)

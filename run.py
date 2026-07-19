"""One-command launcher for the Democracy Platform prototype.

Usage:
    python run.py          # starts the app on http://localhost:8400
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8400, reload=False)

"""兼容入口：`python main.py`。"""
from __future__ import annotations

from src.app import app, print_banner

if __name__ == "__main__":
    print_banner()
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False, threaded=True)

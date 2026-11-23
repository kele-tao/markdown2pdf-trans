"""Flask 应用入口。

用法（三种均可）：
- `python -m src.app`      （推荐，包内相对导入正常）
- `python main.py`
- `python src/app.py`      （脚本直跑；下方会自动把项目根加入 sys.path 并切到绝对导入）

生产环境：`waitress-serve --port 5000 src.app:app`
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in (None, ""):  # 直接以脚本方式运行（python src/app.py）
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "src"  # noqa: A001

from .app_factory import create_app, get_manager  # noqa: E402,F401
from .config import APP_VERSION  # noqa: E402
from .core.settings_store import get_settings  # noqa: E402

app = create_app()


def print_banner() -> None:
    """启动检查横幅：依赖缺失只警告不阻止启动。"""
    from .api.health import _pandoc_ok, _weasyprint_ok
    from .rendering.fonts import font_status

    print("=" * 22 + " 启动环境检查 " + "=" * 22)
    print(("[OK]  " if _pandoc_ok() else "[WARN]") + " Pandoc 可用")
    print(("[OK]  " if _weasyprint_ok() else "[WARN]") + " WeasyPrint 可用")
    fonts = font_status()
    print(f"[OK]  本地字体 {fonts['count']} 个（{', '.join(fonts['files'][:4]) or '使用系统字体'}）")
    settings = get_settings()
    mode = "离线演示（mock）" if settings.is_mock() else f"真实 AI（{settings.model}）"
    print(f"[INFO] 翻译引擎：{mode}")
    print(f"[INFO] 版本 {APP_VERSION}")
    print("=" * 62)
    print("  访问地址: http://127.0.0.1:5000")
    print("  健康检查: http://127.0.0.1:5000/api/health")
    print("=" * 62)


if __name__ == "__main__":
    print_banner()
    port = int(os.environ.get("M2P_PORT", "5000"))
    app.run(host=os.environ.get("M2P_HOST", "0.0.0.0"), port=port, debug=False, use_reloader=False, threaded=True)

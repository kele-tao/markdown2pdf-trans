"""
全局配置：从环境变量加载 AI 服务参数、限流参数、路径参数。
优先级：显式环境变量 > .env 文件 > 内置默认值（仅对非敏感项）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# 尝试加载 .env（可选依赖）
try:
    from dotenv import load_dotenv

    # 优先加载项目根目录的 .env，再加载 CWD 的
    for candidate in (Path(__file__).resolve().parent.parent / ".env", Path.cwd() / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
except ImportError:  # 没装 python-dotenv 也照常工作
    pass


# -----------------------------------------------------------------------------
# 路径
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(os.environ.get("M2P_OUTPUT_DIR", BASE_DIR / "output")).resolve()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TEMPLATE_DIR = BASE_DIR / "src" / "templates"
STATIC_DIR = BASE_DIR / "src" / "static"

MAX_UPLOAD_BYTES = int(os.environ.get("M2P_MAX_UPLOAD_MB", "100")) * 1024 * 1024


# -----------------------------------------------------------------------------
# AI 服务
# -----------------------------------------------------------------------------
def _require_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise RuntimeError(
            f"环境变量 {key} 未配置。请在 .env 中设置后重启服务。\n"
            f"参考：cp .env.example .env  # 然后填入你的 AI_API_KEY 等"
        )
    return val


class AIConfig:
    """AI 服务配置（单例）。任何调用都走这里，便于测试时 monkeypatch。"""

    def __init__(self) -> None:
        # 启动时不强校验，懒加载。给前端一个机会展示"未配置"状态而不是直接 500。
        self.api_url: str = os.environ.get("AI_API_URL", "").strip()
        self.api_key: str = os.environ.get("AI_API_KEY", "").strip()
        self.model: str = os.environ.get("AI_MODEL", "").strip()
        self.timeout: int = int(os.environ.get("AI_TIMEOUT", "180"))
        self.max_retries: int = int(os.environ.get("AI_MAX_RETRIES", "3"))
        self.retry_base_delay: float = float(os.environ.get("AI_RETRY_BASE_DELAY", "1.5"))
        self.max_concurrency: int = int(os.environ.get("AI_MAX_CONCURRENCY", "3"))

    def is_configured(self) -> bool:
        # 每次实时读 env，方便测试和热切换
        url = os.environ.get("AI_API_URL", "").strip() or self.api_url
        key = os.environ.get("AI_API_KEY", "").strip() or self.api_key
        model = os.environ.get("AI_MODEL", "").strip() or self.model
        return bool(url and key and model)

    def require(self) -> None:
        """真实调用前必须通过；缺失则抛清晰异常。"""
        missing = [
            name for name, val in (
                ("AI_API_URL", os.environ.get("AI_API_URL", "").strip() or self.api_url),
                ("AI_API_KEY", os.environ.get("AI_API_KEY", "").strip() or self.api_key),
                ("AI_MODEL", os.environ.get("AI_MODEL", "").strip() or self.model),
            ) if not val
        ]
        if missing:
            raise RuntimeError(
                "AI 服务未配置。缺失环境变量: " + ", ".join(missing)
                + "。复制 .env.example 为 .env 并填入即可。"
            )


ai_config = AIConfig()


# -----------------------------------------------------------------------------
# 翻译缓存 / 任务 生命周期
# -----------------------------------------------------------------------------
CACHE_MAX_ENTRIES = int(os.environ.get("M2P_CACHE_MAX_ENTRIES", "2000"))
CACHE_TTL_SECONDS = int(os.environ.get("M2P_CACHE_TTL_SECONDS", str(7 * 24 * 3600)))  # 7 天
TASK_TTL_SECONDS = int(os.environ.get("M2P_TASK_TTL_SECONDS", str(24 * 3600)))  # 1 天
TASK_CLEANUP_INTERVAL = int(os.environ.get("M2P_TASK_CLEANUP_INTERVAL", "1800"))  # 30 分钟


# -----------------------------------------------------------------------------
# PDF 样式预设（UI 主题用）
# -----------------------------------------------------------------------------
THEME_PRESETS: dict[str, dict[str, Any]] = {
    "default": {
        "name": "默认",
        "text_color": "#333333",
        "heading_color": "#000000",
        "link_color": "#0d6efd",
        "quote_bg_color": "#f9f9f9",
        "quote_border_color": "#cccccc",
        "font_family": '"Times New Roman", "Microsoft YaHei", serif',
        "font_size": "12pt",
        "line_height": "1.7",
        "text_align": "justify",
        "heading_weight": "700",
        "page_margin": "2.5cm",
        "page_orientation": "portrait",
        "code_theme": "kate",
        "code_font_size": "85%",
    },
    "academic": {
        "name": "学术",
        "text_color": "#1a1a1a",
        "heading_color": "#0a0a0a",
        "link_color": "#0b5394",
        "quote_bg_color": "#f0f4f8",
        "quote_border_color": "#5b9bd5",
        "font_family": '"Times New Roman", "Source Han Serif SC", serif',
        "font_size": "11pt",
        "line_height": "1.6",
        "text_align": "justify",
        "heading_weight": "700",
        "page_margin": "2cm",
        "page_orientation": "portrait",
        "code_theme": "tango",
        "code_font_size": "85%",
    },
    "business": {
        "name": "商务",
        "text_color": "#212529",
        "heading_color": "#0d6efd",
        "link_color": "#0d6efd",
        "quote_bg_color": "#e7f1ff",
        "quote_border_color": "#0d6efd",
        "font_family": '"Helvetica", "Arial", "Microsoft YaHei", sans-serif',
        "font_size": "11pt",
        "line_height": "1.5",
        "text_align": "left",
        "heading_weight": "700",
        "page_margin": "2cm",
        "page_orientation": "portrait",
        "code_theme": "pygments",
        "code_font_size": "85%",
    },
    "dark": {
        "name": "暗黑",
        "text_color": "#e6e6e6",
        "heading_color": "#ffffff",
        "link_color": "#66b2ff",
        "quote_bg_color": "#2a2a2a",
        "quote_border_color": "#555555",
        "font_family": '"Helvetica", "Arial", "Microsoft YaHei", sans-serif',
        "font_size": "11pt",
        "line_height": "1.6",
        "text_align": "left",
        "heading_weight": "700",
        "page_margin": "2cm",
        "page_orientation": "portrait",
        "code_theme": "monokai",
        "code_font_size": "90%",
    },
}


# -----------------------------------------------------------------------------
# 翻译 Prompt
# -----------------------------------------------------------------------------
TRANSLATION_PROMPT = """
You are a professional linguist and expert document translator. Your task is to translate the following Markdown text into {target_language}.

**Critical Instructions:**
1.  **Preserve Formatting:** You MUST preserve all original Markdown formatting perfectly. This includes, but is not limited to:
    * Headings (`#`, `##`, etc.)
    * Lists (ordered, unordered, and nested)
    * Bold (`**text**`) and Italic (`*text*`) styling
    * Blockquotes (`>`)
    * Tables (`| Header | ...`)
    * Image links (`![alt text](url)`)
    * Hyperlinks (`[link text](url)`)
2.  **Code Blocks:** DO NOT translate any content inside code blocks (```...```) or inline code (`...`). Leave the code as it is.
3.  **Accuracy and Tone:** Translate the textual content with high accuracy, maintaining the original tone and context. The translation should be grammatically flawless and natural-sounding in {target_language}.
4.  **Output ONLY Translation:** Your output must ONLY be the translated Markdown text. Do not add any extra explanations, apologies, or comments like "Here is the translation:".
5.  **Respectful Language:** Ensure the translation is professional and respectful, completely free of any insulting or offensive language.

Translate the following Markdown content:
"""

FILENAME_TRANSLATION_PROMPT = """
You are an expert file name translator. Translate the following text to {target_language} to be used as a valid file name.
**Critical Instructions:**
1.  Provide a concise and accurate translation.
2.  Replace spaces with underscores (_).
3.  Do not include any special characters that are invalid for file names (e.g., /\\:*?"<>|).
4.  Output ONLY the translated text. Do not add any explanation.

Translate the following text:
"""


# -----------------------------------------------------------------------------
# 语言选项
# -----------------------------------------------------------------------------
LANGUAGES: list[tuple[str, str]] = [
    ("English", "英语 (English)"),
    ("Chinese", "中文 (Chinese)"),
    ("French", "法语 (French)"),
    ("Russian", "俄语 (Russian)"),
    ("Spanish", "西班牙语 (Spanish)"),
    ("Arabic", "阿拉伯语 (Arabic)"),
]

EXPORT_MODES: list[tuple[str, str]] = [
    ("translated", "仅译文 (文件名将翻译)"),
    ("original", "仅原文"),
    ("bilingual", "双语对照 + 单独译文"),
]

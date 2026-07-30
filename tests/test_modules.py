"""
单元测试：覆盖最容易出问题的关键路径。
跑法: pytest tests/
"""
import time
from pathlib import Path
import pytest

from src.file_utils import sanitize_filename, read_text_with_fallback
from src.translation_cache import TranslationCache
from src.biligual_align import split_markdown_to_blocks, align_blocks, Block, BlockType
from src.uploads import safe_join, UnsafePathError, unzip_safely
from src.config import THEME_PRESETS


# --- file_utils ---
class TestSanitizeFilename:
    def test_basic(self):
        assert sanitize_filename("hello world") == "hello_world"
    def test_invalid_chars(self):
        assert sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"
    def test_empty(self):
        assert sanitize_filename("") == "untitled"
    def test_dots(self):
        assert sanitize_filename("..") == "untitled"


class TestReadTextWithFallback:
    def test_utf8(self, tmp_path: Path):
        p = tmp_path / "a.md"
        p.write_text("你好", encoding="utf-8")
        assert read_text_with_fallback(p) == "你好"
    def test_utf8_bom(self, tmp_path: Path):
        p = tmp_path / "a.md"
        p.write_text("\ufeff你好", encoding="utf-8")
        assert read_text_with_fallback(p) == "你好"


# --- translation_cache ---
class TestTranslationCache:
    def test_put_get(self):
        c = TranslationCache(max_entries=10, ttl_seconds=60)
        c.put("k1", "v1")
        assert c.get("k1") == "v1"
    def test_lru_eviction(self):
        c = TranslationCache(max_entries=2, ttl_seconds=60)
        c.put("a", "1"); c.put("b", "2"); c.put("c", "3")
        assert c.get("a") is None
        assert c.get("b") == "2"
        assert c.get("c") == "3"
    def test_ttl(self):
        c = TranslationCache(max_entries=10, ttl_seconds=0)
        c.put("k", "v")
        time.sleep(0.05)
        assert c.get("k") is None
    def test_key_includes_model(self):
        k1 = TranslationCache.make_key("x", "en", "p", "model-A")
        k2 = TranslationCache.make_key("x", "en", "p", "model-B")
        assert k1 != k2


# --- biligual_align ---
class TestSplitMarkdown:
    def test_basic_split(self):
        md = "# H1\n\nPara1\n\nPara2\n\n- list item"
        blocks = split_markdown_to_blocks(md)
        types = [b.type for b in blocks]
        assert BlockType.HEADING in types
        assert BlockType.PARAGRAPH in types
        assert BlockType.LIST in types
    def test_code_block_preserved(self):
        md = "```python\nprint('hi')\n```\n\nPara"
        blocks = split_markdown_to_blocks(md)
        code = next(b for b in blocks if b.type == BlockType.CODE)
        assert "print('hi')" in code.content
    def test_blank_dedup(self):
        md = "A\n\n\n\n\nB"
        blocks = split_markdown_to_blocks(md)
        blanks = [b for b in blocks if b.type == BlockType.BLANK]
        assert len(blanks) <= 1


class TestAlignBlocks:
    def test_perfect_match(self):
        ob = [Block(BlockType.HEADING, "# T"), Block(BlockType.PARAGRAPH, "P1")]
        tb = [Block(BlockType.HEADING, "# 译"), Block(BlockType.PARAGRAPH, "译1")]
        aligned = align_blocks(ob, tb)
        assert len(aligned) == 2
        assert aligned[0][1].content == "# 译"
    def test_translation_missing_block(self):
        ob = [Block(BlockType.HEADING, "# T"), Block(BlockType.PARAGRAPH, "P1")]
        tb = [Block(BlockType.PARAGRAPH, "译1")]  # 少一个 heading
        aligned = align_blocks(ob, tb)
        assert len(aligned) == 2
        # 第一个应该被补空
        assert aligned[0][0].type == BlockType.HEADING
        assert aligned[0][1].content == ""


# --- uploads ---
class TestSafeJoin:
    def test_normal(self, tmp_path: Path):
        p = safe_join(tmp_path, "a", "b.txt")
        assert p == (tmp_path / "a" / "b.txt").resolve()
    def test_traversal_blocked(self, tmp_path: Path):
        with pytest.raises(UnsafePathError):
            safe_join(tmp_path, "..", "outside.txt")


class TestUnzipSafely:
    def test_zip_with_traversal_blocked(self, tmp_path: Path):
        import zipfile
        zpath = tmp_path / "test.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("../evil.txt", "pwned")
            zf.writestr("good.txt", "ok")
        out = tmp_path / "out"
        saved, skipped = unzip_safely(zpath, out)
        # 危险条目被跳过，且被记录
        assert not (out.parent / "evil.txt").exists()
        assert (out / "good.txt").is_file()
        assert any("evil" in s for s in skipped)
        assert "good.txt" in saved


# --- theme presets ---
def test_theme_presets_complete():
    for key, theme in THEME_PRESETS.items():
        for field in ("text_color", "heading_color", "link_color",
                      "font_family", "font_size", "line_height"):
            assert field in theme, f"主题 {key} 缺少字段 {field}"

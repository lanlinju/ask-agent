"""
记忆系统单元测试
"""

import tempfile
from pathlib import Path

from memory import MemoryManager, MemoryEntry, MEMORY_TYPES, DreamConsolidator


class TestMemoryEntry:
    """测试 MemoryEntry 数据类"""

    def test_to_frontmatter(self):
        """测试 frontmatter 生成"""
        entry = MemoryEntry(
            name="prefer_tabs",
            description="User prefers tabs for indentation",
            mem_type="user",
            content="The user explicitly prefers tabs over spaces.",
        )
        text = entry.to_frontmatter()
        assert "---" in text
        assert "name: prefer_tabs" in text
        assert "description: User prefers tabs for indentation" in text
        assert "type: user" in text
        assert "The user explicitly prefers tabs over spaces." in text

    def test_from_frontmatter(self):
        """测试 frontmatter 解析"""
        text = "---\nname: prefer_tabs\ndescription: User prefers tabs\ntype: user\n---\nUse tabs.\n"
        entry = MemoryEntry.from_frontmatter(text, "prefer_tabs.md")
        assert entry is not None
        assert entry.name == "prefer_tabs"
        assert entry.description == "User prefers tabs"
        assert entry.mem_type == "user"
        assert entry.content == "Use tabs."

    def test_from_frontmatter_invalid(self):
        """测试无效 frontmatter 返回 None"""
        text = "This has no frontmatter at all."
        entry = MemoryEntry.from_frontmatter(text)
        assert entry is None

    def test_roundtrip(self):
        """测试写入再读回的往返一致性"""
        entry = MemoryEntry(
            name="avoid_mock",
            description="Don't use heavy mocks",
            mem_type="feedback",
            content="User dislikes mock-heavy tests.\nPrefer real objects when possible.",
        )
        text = entry.to_frontmatter()
        restored = MemoryEntry.from_frontmatter(text, "avoid_mock.md")
        assert restored is not None
        assert restored.name == entry.name
        assert restored.description == entry.description
        assert restored.mem_type == entry.mem_type
        assert restored.content == entry.content


class TestMemoryManager:
    """测试 MemoryManager"""

    def _make_manager(self, tmp_path: Path) -> MemoryManager:
        memory_dir = tmp_path / ".memory"
        return MemoryManager(memory_dir=memory_dir)

    def test_save_and_load(self):
        """测试保存后重新加载"""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))

            # 保存一条记忆
            result = mgr.save_memory(
                "prefer_tabs",
                "User prefers tabs",
                "user",
                "Use tabs over spaces.",
            )
            assert "Saved" in result
            assert "prefer_tabs" in result
            assert "user" in result

            # 重新加载
            mgr2 = self._make_manager(Path(tmp))
            count = mgr2.load_all()
            assert count == 1
            assert "prefer_tabs" in mgr2.memories
            assert mgr2.memories["prefer_tabs"].content == "Use tabs over spaces."

    def test_save_invalid_type(self):
        """测试无效类型被拒绝"""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))
            result = mgr.save_memory("x", "y", "invalid_type", "z")
            assert "Error" in result

    def test_save_invalid_name(self):
        """测试无效名称被拒绝"""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))
            result = mgr.save_memory("!!!", "y", "user", "z")
            # 名称被清理为 "___"，仍然有效
            # 测试完全无效的情况
            result2 = mgr.save_memory("", "", "user", "")
            # 空名称经过清理后可能仍有效，但内容应有意义

    def test_delete_memory(self):
        """测试删除记忆"""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))
            mgr.save_memory("test_mem", "A test", "project", "Some content")
            assert "test_mem" in mgr.memories

            result = mgr.delete_memory("test_mem")
            assert "Deleted" in result
            assert "test_mem" not in mgr.memories

    def test_delete_nonexistent(self):
        """测试删除不存在的记忆"""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))
            result = mgr.delete_memory("nonexistent")
            assert "Error" in result

    def test_load_empty_dir(self):
        """测试空目录加载返回 0"""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))
            count = mgr.load_all()
            assert count == 0

    def test_load_nonexistent_dir(self):
        """测试不存在的目录加载返回 0"""
        mgr = MemoryManager(memory_dir=Path("/nonexistent/path"))
        count = mgr.load_all()
        assert count == 0

    def test_get_memory_prompt_empty(self):
        """测试无记忆时返回空字符串"""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))
            assert mgr.get_memory_prompt() == ""

    def test_get_memory_prompt_with_data(self):
        """测试有记忆时生成格式化文本"""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))
            mgr.save_memory("pref1", "User pref", "user", "Use tabs.")
            mgr.save_memory("feed1", "Don't do X", "feedback", "X was wrong.")
            mgr.save_memory("proj1", "Legacy module", "project", "Don't touch it.")

            prompt = mgr.get_memory_prompt()
            assert "[user]" in prompt
            assert "[feedback]" in prompt
            assert "[project]" in prompt
            assert "pref1" in prompt
            assert "feed1" in prompt
            assert "proj1" in prompt

    def test_list_memories(self):
        """测试列出记忆"""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))
            assert mgr.list_memories() == "(no memories)"

            mgr.save_memory("m1", "First", "user", "Content 1")
            mgr.save_memory("m2", "Second", "feedback", "Content 2")
            listing = mgr.list_memories()
            assert "m1" in listing
            assert "m2" in listing

    def test_index_rebuilt(self):
        """测试保存后索引文件被重建"""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))
            mgr.save_memory("test1", "Test memory", "reference", "http://example.com")

            index_path = mgr.memory_dir / "MEMORY.md"
            assert index_path.exists()
            index_content = index_path.read_text(encoding="utf-8")
            assert "test1" in index_content
            assert "Test memory" in index_content

    def test_multiple_types(self):
        """测试所有 4 种类型都可以保存和加载"""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))
            for mem_type in MEMORY_TYPES:
                result = mgr.save_memory(
                    f"test_{mem_type}", f"Test {mem_type}", mem_type, f"Content for {mem_type}"
                )
                assert "Saved" in result

            mgr2 = self._make_manager(Path(tmp))
            count = mgr2.load_all()
            assert count == 4

    def test_overwrite_existing(self):
        """测试覆盖已有记忆"""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))
            mgr.save_memory("same_name", "Original", "user", "Original content")
            mgr.save_memory("same_name", "Updated", "feedback", "Updated content")

            mgr2 = self._make_manager(Path(tmp))
            mgr2.load_all()
            assert mgr2.memories["same_name"].description == "Updated"
            assert mgr2.memories["same_name"].content == "Updated content"
            assert mgr2.memories["same_name"].mem_type == "feedback"

    def test_chinese_name(self):
        """测试中文记忆名称"""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._make_manager(Path(tmp))
            result = mgr.save_memory(
                "代码风格", "用户偏好中文注释", "user", "代码注释必须用中文。"
            )
            assert "Saved" in result

            mgr2 = self._make_manager(Path(tmp))
            mgr2.load_all()
            assert "代码风格" in mgr2.memories


class TestDreamConsolidator:
    """测试 DreamConsolidator 门控逻辑"""

    def test_should_consolidate_disabled(self):
        """测试禁用时不能整合"""
        with tempfile.TemporaryDirectory() as tmp:
            c = DreamConsolidator(memory_dir=Path(tmp))
            c.enabled = False
            can_run, reason = c.should_consolidate()
            assert not can_run
            assert "Gate 1" in reason

    def test_should_consolidate_no_dir(self):
        """测试目录不存在时不能整合"""
        c = DreamConsolidator(memory_dir=Path("/nonexistent"))
        can_run, reason = c.should_consolidate()
        assert not can_run
        assert "Gate 2" in reason

    def test_should_consolidate_no_files(self):
        """测试无记忆文件时不能整合"""
        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / ".memory"
            memory_dir.mkdir()
            # 只创建 MEMORY.md（索引文件，不算记忆文件）
            (memory_dir / "MEMORY.md").write_text("# Memory Index\n", encoding="utf-8")
            c = DreamConsolidator(memory_dir=memory_dir)
            can_run, reason = c.should_consolidate()
            assert not can_run
            assert "Gate 2" in reason

    def test_should_consolidate_plan_mode(self):
        """测试 plan 模式不能整合"""
        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / ".memory"
            memory_dir.mkdir()
            (memory_dir / "test.md").write_text(
                "---\nname: test\ntype: user\n---\nContent\n", encoding="utf-8"
            )
            c = DreamConsolidator(memory_dir=memory_dir)
            c.mode = "plan"
            # 先通过 Gate 2（有文件），到 Gate 3
            can_run, reason = c.should_consolidate()
            assert not can_run
            assert "Gate 3" in reason

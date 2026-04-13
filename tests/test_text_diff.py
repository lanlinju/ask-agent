"""测试 text_diff 模块。"""

import pytest
from util.text_diff import diff, format_diff, DiffEntry, Op


class TestDiffEntry:
    """测试 DiffEntry 类"""

    def test_str_keep(self):
        """测试 KEEP 操作的普通输出"""
        entry = DiffEntry(Op.KEEP, "hello")
        assert str(entry) == "  hello"

    def test_str_delete(self):
        """测试 DELETE 操作的普通输出"""
        entry = DiffEntry(Op.DELETE, "hello")
        assert str(entry) == "- hello"

    def test_str_insert(self):
        """测试 INSERT 操作的普通输出"""
        entry = DiffEntry(Op.INSERT, "hello")
        assert str(entry) == "+ hello"

    def test_colored_string_keep(self):
        """测试 KEEP 操作的彩色输出"""
        entry = DiffEntry(Op.KEEP, "hello")
        assert entry.to_colored_string() == "  hello"

    def test_colored_string_delete(self):
        """测试 DELETE 操作的彩色输出"""
        entry = DiffEntry(Op.DELETE, "hello")
        result = entry.to_colored_string()
        assert "\033[31m" in result  # RED
        assert "\033[0m" in result   # RESET
        assert "- hello" in result

    def test_colored_string_insert(self):
        """测试 INSERT 操作的彩色输出"""
        entry = DiffEntry(Op.INSERT, "hello")
        result = entry.to_colored_string()
        assert "\033[32m" in result  # GREEN
        assert "\033[0m" in result   # RESET
        assert "+ hello" in result


class TestDiff:
    """测试 diff 函数"""

    def test_diff_identical(self):
        """测试完全相同的文本"""
        original = ["a", "b", "c"]
        revised = ["a", "b", "c"]
        result = diff(original, revised)
        assert len(result) == 3
        assert all(e.op == Op.KEEP for e in result)

    def test_diff_all_deleted(self):
        """测试全部删除"""
        original = ["a", "b", "c"]
        revised = []
        result = diff(original, revised)
        assert len(result) == 3
        assert all(e.op == Op.DELETE for e in result)

    def test_diff_all_inserted(self):
        """测试全部插入"""
        original = []
        revised = ["a", "b", "c"]
        result = diff(original, revised)
        assert len(result) == 3
        assert all(e.op == Op.INSERT for e in result)

    def test_diff_mixed(self):
        """测试混合修改"""
        original = ["a", "b", "c", "d"]
        revised = ["a", "x", "c", "y"]
        result = diff(original, revised)
        
        # 检查结果包含正确的操作
        ops = [e.op for e in result]
        assert Op.KEEP in ops  # a, c 保持
        assert Op.DELETE in ops  # b, d 删除
        assert Op.INSERT in ops  # x, y 插入

    def test_diff_example(self):
        """测试 Java 示例中的场景"""
        original = [
            "Hello world",
            "This is line two",
            "This line will be removed",
            "Common line",
            "End"
        ]
        revised = [
            "Hello world",
            "This is line two (modified)",
            "Common line",
            "A brand new line",
            "End"
        ]
        result = diff(original, revised)
        
        # 检查行数
        assert len(result) == 7  # 2 keep + 1 delete + 1 insert + 1 keep + 1 insert + 1 keep
        
        # 检查第一行保持
        assert result[0].op == Op.KEEP
        assert result[0].line == "Hello world"


class TestFormatDiff:
    """测试 format_diff 函数"""

    def test_format_diff_colored(self):
        """测试带颜色的格式化输出"""
        original = "a\nb\nc"
        revised = "a\nx\nc"
        result = format_diff(original, revised, colored=True)
        
        assert "  a" in result  # 保持行
        assert "\033[31m" in result  # 红色（删除）
        assert "\033[32m" in result  # 绿色（插入）

    def test_format_diff_no_color(self):
        """测试不带颜色的格式化输出"""
        original = "a\nb\nc"
        revised = "a\nx\nc"
        result = format_diff(original, revised, colored=False)
        
        assert "  a" in result  # 保持行
        assert "- b" in result  # 删除行
        assert "+ x" in result  # 插入行
        assert "\033[" not in result  # 无 ANSI 转义码

    def test_format_diff_empty(self):
        """测试空文本比较"""
        result = format_diff("", "", colored=False)
        assert result == ""

    def test_format_diff_single_line(self):
        """测试单行文本比较"""
        result = format_diff("hello", "world", colored=False)
        assert "- hello" in result
        assert "+ world" in result

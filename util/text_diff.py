"""基于 LCS 算法的文本差异比较工具。"""

from enum import Enum
from typing import List


class Op(Enum):
    """差异操作类型"""
    KEEP = "keep"       # 行相同
    DELETE = "delete"   # 行仅在原文本中存在
    INSERT = "insert"   # 行仅在新文本中存在


# ANSI 颜色代码
_ANSI_RESET = "\033[0m"
_ANSI_RED = "\033[31m"
_ANSI_GREEN = "\033[32m"


class DiffEntry:
    """差异条目：操作类型 + 行内容"""

    def __init__(self, op: Op, line: str):
        self.op = op
        self.line = line

    def __str__(self) -> str:
        """普通无颜色输出（用于文件或非终端环境）"""
        if self.op == Op.KEEP:
            return "  " + self.line
        elif self.op == Op.DELETE:
            return "- " + self.line
        elif self.op == Op.INSERT:
            return "+ " + self.line
        return "? " + self.line

    def to_colored_string(self) -> str:
        """带 ANSI 颜色的字符串（整行着色）"""
        if self.op == Op.KEEP:
            return "  " + self.line
        elif self.op == Op.DELETE:
            return f"{_ANSI_RED}- {self.line}{_ANSI_RESET}"
        elif self.op == Op.INSERT:
            return f"{_ANSI_GREEN}+ {self.line}{_ANSI_RESET}"
        return "? " + self.line

    def to_prefix_colored_string(self) -> str:
        """仅前缀着色"""
        if self.op == Op.KEEP:
            return "  " + self.line
        elif self.op == Op.DELETE:
            return f"{_ANSI_RED}- {_ANSI_RESET}{self.line}"
        elif self.op == Op.INSERT:
            return f"{_ANSI_GREEN}+ {_ANSI_RESET}{self.line}"
        return "? " + self.line


def diff(original: List[str], revised: List[str]) -> List[DiffEntry]:
    """计算两个文本之间的差异（基于 LCS 算法，按行比较）

    Args:
        original: 原始文本行列表
        revised: 修改后的文本行列表

    Returns:
        差异条目列表（保持原顺序）
    """
    m = len(original)
    n = len(revised)

    # dp[i][j] 表示 original[0..i-1] 与 revised[0..j-1] 的 LCS 长度
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if original[i - 1] == revised[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    # 回溯构造 diff 操作列表
    result: List[DiffEntry] = []
    i, j = m, n
    while i > 0 and j > 0:
        if original[i - 1] == revised[j - 1]: # 行相同，保留
            result.append(DiffEntry(Op.KEEP, original[i - 1]))
            i -= 1
            j -= 1
        elif dp[i - 1][j] > dp[i][j - 1]:
            result.append(DiffEntry(Op.DELETE, original[i - 1]))  # 原文件中的行被删除
            i -= 1
        else:
            result.append(DiffEntry(Op.INSERT, revised[j - 1]))  # 新文件中的行被插入
            j -= 1

    # 处理剩余行
    while i > 0:
        result.append(DiffEntry(Op.DELETE, original[i - 1]))
        i -= 1
    while j > 0:
        result.append(DiffEntry(Op.INSERT, revised[j - 1]))
        j -= 1

    # 回溯时是从后向前添加的，需要反转顺序
    result.reverse()
    return result


def format_diff(original: str, revised: str, colored: bool = True) -> str:
    """比较两段文本并返回格式化的 diff 字符串

    Args:
        original: 原始文本
        revised: 修改后的文本
        colored: 是否使用 ANSI 颜色

    Returns:
        格式化的 diff 字符串
    """
    orig_lines = original.splitlines()
    rev_lines = revised.splitlines()
    entries = diff(orig_lines, rev_lines)

    formatter = DiffEntry.to_colored_string if colored else str
    return "\n".join(formatter(entry) for entry in entries)

def main():
    """示例：比较两个简单的文本"""
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
    
    diff_result = diff(original, revised)
    
    print("========== 普通输出 ==========")
    for entry in diff_result:
        print(entry)
    
    print("\n========== 带 ANSI 颜色输出 ==========")
    for entry in diff_result:
        print(entry.to_colored_string())


if __name__ == "__main__":
    import sys
    import io
    # 设置 stdout 为 utf-8 编码，解决 Windows 终端中文显示问题
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()
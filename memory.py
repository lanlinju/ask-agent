"""
记忆系统 — 跨会话持久化记忆管理

只有跨会话仍然有价值、且不能从当前代码轻易重新推导的信息，
才值得进入 memory。

支持的类型:
  - user: 用户偏好（代码风格、回答格式、工具链偏好等）
  - feedback: 用户明确纠正过的地方（"不要这样改"、"之前判断方式有误"等）
  - project: 不容易从代码直接看出来的项目约定或背景
  - reference: 外部资源指针（看板、监控面板、文档 URL 等）

不该存的东西:
  - 文件结构、函数签名、目录布局（可以重新读代码得到）
  - 当前任务进度（属于 task/plan，不属于 memory）
  - 临时分支名、当前 PR 号（很快会过时）
  - 密钥、密码、凭证（安全风险）

存储结构:
  .memory/
    MEMORY.md          # 索引文件
    prefer_tabs.md     # 单条记忆文件（带 frontmatter）
    feedback_tests.md
    incident_board.md
"""

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 记忆类型常量
MEMORY_TYPES = ("user", "feedback", "project", "reference")

# 索引文件最大行数
MAX_INDEX_LINES = 200

# 记忆指导文本，注入系统提示词
MEMORY_GUIDANCE = """
## Memory Guidelines (save_memory tool)

When to save memories:
- User states a preference ("I like tabs", "always use pytest") -> type: user
- User corrects you ("don't do X", "that was wrong because...") -> type: feedback
- You learn a project fact not obvious from code (compliance rule, legacy module must stay) -> type: project
- You learn where an external resource lives (ticket board, dashboard, docs URL) -> type: reference

When NOT to save:
- Anything easily derivable from code (function signatures, file structure, directory layout)
- Temporary task state (current branch, open PR numbers, current TODOs)
- Secrets or credentials (API keys, passwords)

Important: memory provides direction, not absolute truth. If memory conflicts with
current observed state, trust the current state. Verify paths/names before referencing them.
"""


def _get_memory_dir() -> Path:
    """获取记忆目录路径，优先使用当前目录下的 .memory/，回退到 ~/.ask-agent/memory/"""
    cwd_memory = Path.cwd() / ".memory"
    if cwd_memory.exists():
        return cwd_memory
    user_memory = Path.home() / ".ask-agent" / "memory"
    return user_memory


@dataclass
class MemoryEntry:
    """单条记忆条目"""

    name: str
    description: str
    mem_type: str
    content: str
    file_name: str = ""

    def to_frontmatter(self) -> str:
        """生成带 frontmatter 的 Markdown 文本"""
        return f"---\nname: {self.name}\ndescription: {self.description}\ntype: {self.mem_type}\n---\n{self.content}\n"

    @classmethod
    def from_frontmatter(cls, text: str, file_name: str = "") -> Optional["MemoryEntry"]:
        """从 frontmatter 格式的文本解析 MemoryEntry"""
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
        if not match:
            return None
        header, body = match.group(1), match.group(2)
        result = {"content": body.strip()}
        for line in header.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip()] = value.strip()
        name = result.get("name", Path(file_name).stem if file_name else "unknown")
        return cls(
            name=name,
            description=result.get("description", ""),
            mem_type=result.get("type", "project"),
            content=result.get("content", ""),
            file_name=file_name,
        )


class MemoryManager:
    """
    加载、构建和保存跨会话的持久化记忆。

    每条记忆一个 Markdown 文件（带 frontmatter），外加一个紧凑的索引文件。
    """

    def __init__(self, memory_dir: Path | None = None):
        self.memory_dir = memory_dir or _get_memory_dir()
        self.memories: Dict[str, MemoryEntry] = {}

    def load_all(self) -> int:
        """
        加载所有记忆文件。

        Returns:
            加载的记忆条数
        """
        self.memories = {}
        if not self.memory_dir.exists():
            return 0

        for md_file in sorted(self.memory_dir.glob("*.md")):
            if md_file.name == "MEMORY.md":
                continue
            try:
                text = md_file.read_text(encoding="utf-8")
                entry = MemoryEntry.from_frontmatter(text, md_file.name)
                if entry:
                    self.memories[entry.name] = entry
            except Exception as e:
                logger.warning("加载记忆文件失败 %s: %s", md_file.name, e)

        count = len(self.memories)
        if count > 0:
            logger.info("加载了 %d 条记忆 (来自 %s)", count, self.memory_dir)
        return count

    def get_memory_prompt(self) -> str:
        """
        构建记忆区域文本，用于注入系统提示词。

        按类型分组，便于阅读。
        """
        if not self.memories:
            return ""

        sections = ["# Memories (persistent across sessions)", ""]
        for mem_type in MEMORY_TYPES:
            typed = {k: v for k, v in self.memories.items() if v.mem_type == mem_type}
            if not typed:
                continue
            sections.append(f"## [{mem_type}]")
            for name, entry in typed.items():
                sections.append(f"### {name}: {entry.description}")
                if entry.content.strip():
                    sections.append(entry.content.strip())
                sections.append("")

        return "\n".join(sections)

    def save_memory(
        self, name: str, description: str, mem_type: str, content: str
    ) -> str:
        """
        保存一条记忆到磁盘并更新索引。

        Args:
            name: 记忆标识符（如 prefer_tabs, db_schema）
            description: 一行摘要
            mem_type: 记忆类型 (user/feedback/project/reference)
            content: 完整记忆内容（可多行）

        Returns:
            状态消息
        """
        if mem_type not in MEMORY_TYPES:
            return f"Error: type must be one of {MEMORY_TYPES}"

        # 清理 name 作为文件名
        safe_name = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]", "_", name.lower())
        if not safe_name:
            return "Error: invalid memory name"

        self.memory_dir.mkdir(parents=True, exist_ok=True)

        entry = MemoryEntry(
            name=name,
            description=description,
            mem_type=mem_type,
            content=content,
            file_name=f"{safe_name}.md",
        )

        # 写入单个记忆文件
        file_path = self.memory_dir / entry.file_name
        file_path.write_text(entry.to_frontmatter(), encoding="utf-8")

        # 更新内存中的存储
        self.memories[name] = entry

        # 重建索引
        self._rebuild_index()

        rel_path = file_path.relative_to(Path.cwd()) if file_path.is_relative_to(Path.cwd()) else file_path
        logger.info("保存记忆 '%s' [%s] -> %s", name, mem_type, rel_path)
        return f"Saved memory '{name}' [{mem_type}] to {rel_path}"

    def delete_memory(self, name: str) -> str:
        """
        删除一条记忆。

        Args:
            name: 要删除的记忆标识符

        Returns:
            状态消息
        """
        if name not in self.memories:
            return f"Error: memory '{name}' not found"

        entry = self.memories[name]
        file_path = self.memory_dir / entry.file_name
        try:
            if file_path.exists():
                file_path.unlink()
        except OSError as e:
            logger.warning("删除记忆文件失败: %s", e)

        del self.memories[name]
        self._rebuild_index()
        logger.info("删除记忆 '%s'", name)
        return f"Deleted memory '{name}'"

    def list_memories(self) -> str:
        """列出所有记忆的简要信息"""
        if not self.memories:
            return "(no memories)"

        lines = []
        for name, entry in self.memories.items():
            lines.append(f"  [{entry.mem_type}] {name}: {entry.description}")
        return "\n".join(lines)

    def _rebuild_index(self):
        """从当前内存状态重建 MEMORY.md 索引，限制在 MAX_INDEX_LINES 行"""
        lines = ["# Memory Index", ""]
        for name, entry in self.memories.items():
            lines.append(f"- {name}: {entry.description} [{entry.mem_type}]")
            if len(lines) >= MAX_INDEX_LINES:
                lines.append(f"... (truncated at {MAX_INDEX_LINES} lines)")
                break

        self.memory_dir.mkdir(parents=True, exist_ok=True)
        index_path = self.memory_dir / "MEMORY.md"
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class DreamConsolidator:
    """
    会话间记忆自动整合（"梦"）。

    这是可选的后期特性。它的作用是防止记忆存储变成噪音堆，
    通过合并、去重和修剪条目来维护记忆质量。

    7 道门控全部通过才会执行整合。
    """

    COOLDOWN_SECONDS = 86400  # 两次整合之间至少 24 小时
    SCAN_THROTTLE_SECONDS = 600  # 两次扫描之间至少 10 分钟
    MIN_SESSION_COUNT = 5  # 需要足够的会话数据
    LOCK_STALE_SECONDS = 3600  # PID 锁超过 1 小时视为过期

    PHASES = [
        "Orient: 扫描 MEMORY.md 索引，了解结构和分类",
        "Gather: 读取各记忆文件的完整内容",
        "Consolidate: 合并相关记忆，移除过时条目",
        "Prune: 确保 MEMORY.md 索引不超过 200 行",
    ]

    def __init__(self, memory_dir: Path | None = None):
        self.memory_dir = memory_dir or _get_memory_dir()
        self.lock_file = self.memory_dir / ".dream_lock"
        self.enabled = True
        self.mode = "default"
        self.last_consolidation_time = 0.0
        self.last_scan_time = 0.0
        self.session_count = 0

    def should_consolidate(self) -> Tuple[bool, str]:
        """
        检查 7 道门控，全部通过才能执行整合。

        Returns:
            (can_run, reason) 其中 reason 解释第一道未通过的门控
        """
        now = time.time()

        # 门控 1: 启用标志
        if not self.enabled:
            return False, "Gate 1: consolidation is disabled"

        # 门控 2: 记忆目录存在且有记忆文件
        if not self.memory_dir.exists():
            return False, "Gate 2: memory directory does not exist"

        memory_files = [
            f for f in self.memory_dir.glob("*.md") if f.name != "MEMORY.md"
        ]
        if not memory_files:
            return False, "Gate 2: no memory files found"

        # 门控 3: 不在 plan 模式
        if self.mode == "plan":
            return False, "Gate 3: plan mode does not allow consolidation"

        # 门控 4: 24 小时冷却
        time_since_last = now - self.last_consolidation_time
        if time_since_last < self.COOLDOWN_SECONDS:
            remaining = int(self.COOLDOWN_SECONDS - time_since_last)
            return False, f"Gate 4: cooldown active, {remaining}s remaining"

        # 门控 5: 10 分钟扫描节流
        time_since_scan = now - self.last_scan_time
        if time_since_scan < self.SCAN_THROTTLE_SECONDS:
            remaining = int(self.SCAN_THROTTLE_SECONDS - time_since_scan)
            return False, f"Gate 5: scan throttle active, {remaining}s remaining"

        # 门控 6: 至少 5 次会话
        if self.session_count < self.MIN_SESSION_COUNT:
            return False, (
                f"Gate 6: only {self.session_count} sessions, "
                f"need {self.MIN_SESSION_COUNT}"
            )

        # 门控 7: 没有活跃的锁文件
        if not self._acquire_lock():
            return False, "Gate 7: lock held by another process"

        return True, "All 7 gates passed"

    def consolidate(self) -> List[str]:
        """
        执行 4 阶段整合流程。

        Returns:
            完成的阶段描述列表
        """
        can_run, reason = self.should_consolidate()
        if not can_run:
            logger.info("[Dream] 无法整合: %s", reason)
            return []

        logger.info("[Dream] 开始整合...")
        self.last_scan_time = time.time()

        completed_phases = []
        for i, phase in enumerate(self.PHASES, 1):
            logger.info("[Dream] Phase %d/4: %s", i, phase)
            completed_phases.append(phase)

        self.last_consolidation_time = time.time()
        self._release_lock()
        logger.info("[Dream] 整合完成: %d 个阶段", len(completed_phases))
        return completed_phases

    def _acquire_lock(self) -> bool:
        """
        获取基于 PID 的锁文件。

        Returns:
            是否成功获取锁
        """
        if self.lock_file.exists():
            try:
                lock_data = self.lock_file.read_text(encoding="utf-8").strip()
                pid_str, timestamp_str = lock_data.split(":", 1)
                pid = int(pid_str)
                lock_time = float(timestamp_str)

                # 检查锁是否过期
                if (time.time() - lock_time) > self.LOCK_STALE_SECONDS:
                    logger.info("[Dream] 移除过期锁 (PID %d)", pid)
                    self.lock_file.unlink()
                else:
                    # 检查持有锁的进程是否还活着
                    try:
                        os.kill(pid, 0)
                        return False  # 进程仍活着，锁有效
                    except OSError:
                        logger.info("[Dream] 移除死进程锁 (PID %d)", pid)
                        self.lock_file.unlink()
            except (ValueError, OSError):
                # 损坏的锁文件，移除
                self.lock_file.unlink(missing_ok=True)

        # 写入新锁
        try:
            self.memory_dir.mkdir(parents=True, exist_ok=True)
            self.lock_file.write_text(
                f"{os.getpid()}:{time.time()}", encoding="utf-8"
            )
            return True
        except OSError:
            return False

    def _release_lock(self):
        """释放锁文件（仅当我们持有它时）"""
        try:
            if self.lock_file.exists():
                lock_data = self.lock_file.read_text(encoding="utf-8").strip()
                pid_str = lock_data.split(":")[0]
                if int(pid_str) == os.getpid():
                    self.lock_file.unlink()
        except (ValueError, OSError):
            pass

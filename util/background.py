"""后台任务管理器：线程池执行 + 通知队列。

慢命令在后台线程中执行，主循环继续推进。
后台任务完成后写入通知队列，下一轮模型调用前排空注入 messages。
"""

import json
import logging
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)


class BackgroundManager:
    """后台任务管理器。

    核心设计：
    - tasks: 任务表，登记每个后台任务的运行状态
    - _notifications: 通知队列，后台线程完成后推送摘要
    - 主循环排空通知后注入 messages，模型即可感知后台结果
    """

    STALL_THRESHOLD_S = 45  # 超过此秒数视为卡住

    def __init__(self, runtime_dir: Path, workdir: Path | None = None):
        self.dir = runtime_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.workdir = workdir or Path.cwd()
        self.tasks: Dict[str, Dict] = {}  # task_id -> record
        self._notifications: List[Dict] = []
        self._lock = threading.Lock()

    def _record_path(self, task_id: str) -> Path:
        return self.dir / f"{task_id}.json"

    def _output_path(self, task_id: str) -> Path:
        return self.dir / f"{task_id}.log"

    def _persist_task(self, task_id: str):
        record = dict(self.tasks[task_id])
        self._record_path(task_id).write_text(
            json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _preview(self, output: str, limit: int = 500) -> str:
        compact = " ".join((output or "(no output)").split())
        return compact[:limit]

    def _relative_or_abs(self, path: Path) -> str:
        """返回相对于 workdir 的路径，如果不可达则返回绝对路径。"""
        try:
            return str(path.relative_to(self.workdir))
        except ValueError:
            return str(path)

    def run(self, command: str, timeout: int = 300) -> str:
        """启动后台线程执行命令，立刻返回 task_id。"""
        task_id = str(uuid.uuid4())[:8]
        output_file = self._output_path(task_id)
        self.tasks[task_id] = {
            "id": task_id,
            "status": "running",
            "command": command,
            "started_at": time.time(),
            "finished_at": None,
            "result_preview": "",
            "output_file": self._relative_or_abs(output_file),
        }
        self._persist_task(task_id)
        logger.info("Background %s: %s", task_id, command[:80])
        print(f"\033[34m⏳ Background {task_id}: {command[:80]}\033[0m")

        thread = threading.Thread(
            target=self._execute, args=(task_id, command, timeout), daemon=True
        )
        thread.start()
        return (
            f"Background task {task_id} started: {command[:80]} "
            f"(output_file={self._relative_or_abs(output_file)})"
        )

    def _execute(self, task_id: str, command: str, timeout: int):
        """线程目标：执行子进程，捕获输出，推送通知。"""
        try:
            r = subprocess.run(
                command,
                shell=True,
                cwd=self.workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            output = (r.stdout + r.stderr).strip()[:50000]
            status = "completed"
        except subprocess.TimeoutExpired:
            output = f"Error: Timeout ({timeout}s)"
            status = "timeout"
        except Exception as e:
            output = f"Error: {e}"
            status = "error"

        final_output = output or "(no output)"
        preview = self._preview(final_output)
        output_path = self._output_path(task_id)
        output_path.write_text(final_output, encoding="utf-8")

        self.tasks[task_id]["status"] = status
        self.tasks[task_id]["finished_at"] = time.time()
        self.tasks[task_id]["result_preview"] = preview
        self._persist_task(task_id)

        with self._lock:
            self._notifications.append(
                {
                    "task_id": task_id,
                    "status": status,
                    "command": self.tasks[task_id]["command"][:80],
                    "preview": preview,
                    "output_file": self._relative_or_abs(output_path),
                }
            )

    def check(self, task_id: str | None = None) -> str:
        """查看单个任务状态或列出所有任务。"""
        if task_id:
            t = self.tasks.get(task_id)
            if not t:
                return f"Error: Unknown task {task_id}"
            visible = {
                "id": t["id"],
                "status": t["status"],
                "command": t["command"],
                "result_preview": t.get("result_preview", ""),
                "output_file": t.get("output_file", ""),
            }
            return json.dumps(visible, indent=2, ensure_ascii=False)

        lines = []
        for tid, t in self.tasks.items():
            lines.append(
                f"{tid}: [{t['status']}] {t['command'][:60]} "
                f"-> {t.get('result_preview') or '(running)'}"
            )
        return "\n".join(lines) if lines else "No background tasks."

    def drain_notifications(self) -> List[Dict]:
        """返回并清空所有待处理的通知。"""
        with self._lock:
            notifs = list(self._notifications)
            self._notifications.clear()
        return notifs

    def detect_stalled(self) -> List[str]:
        """返回运行时间超过阈值的任务ID列表。"""
        now = time.time()
        stalled = []
        for task_id, info in self.tasks.items():
            if info["status"] != "running":
                continue
            elapsed = now - info.get("started_at", now)
            if elapsed > self.STALL_THRESHOLD_S:
                stalled.append(task_id)
        return stalled


def before_model_call(messages: list, bg: BackgroundManager):
    """模型调用前的标准前置步骤：排空后台通知并注入 messages。

    Args:
        messages: 对话消息列表
        bg: 后台任务管理器实例
    """
    notifs = bg.drain_notifications()
    if not notifs:
        return

    notif_text = "\n".join(
        f"[bg:{n['task_id']}] {n['status']}: {n['preview']} "
        f"(output_file={n['output_file']})"
        for n in notifs
    )
    messages.append(
        {
            "role": "user",
            "content": f"<background-results>\n{notif_text}\n</background-results>",
        }
    )

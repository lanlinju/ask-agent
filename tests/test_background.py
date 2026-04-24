"""
后台任务系统单元测试
"""

import json
import re
import time
import tempfile
from pathlib import Path

from util.background import BackgroundManager, before_model_call


class TestBackgroundManager:
    """测试 BackgroundManager"""

    def setup_method(self):
        """每个测试前创建临时目录"""
        self.tmpdir = Path(tempfile.mkdtemp())
        self.bg = BackgroundManager(self.tmpdir, workdir=self.tmpdir)

    def _extract_task_id(self, result: str) -> str:
        """从 run() 返回值中提取 task_id"""
        match = re.search(r"Background task (\w+) started", result)
        return match.group(1) if match else ""

    def test_run_returns_task_id(self):
        """run() 应立刻返回包含 task_id 的信息"""
        result = self.bg.run("echo hello")
        assert "started" in result
        assert len(self.bg.tasks) == 1

    def test_task_completes_and_notifies(self):
        """后台任务完成后应有通知"""
        self.bg.run("echo hello")
        time.sleep(1)
        notifs = self.bg.drain_notifications()
        assert len(notifs) == 1
        assert notifs[0]["status"] == "completed"

    def test_task_status_persisted(self):
        """任务状态应持久化到 json 文件"""
        result = self.bg.run("echo test")
        task_id = self._extract_task_id(result)
        record_path = self.tmpdir / f"{task_id}.json"
        assert record_path.exists()
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert record["id"] == task_id

    def test_check_single_task(self):
        """check(task_id) 应返回该任务的状态"""
        result = self.bg.run("echo ok")
        task_id = self._extract_task_id(result)
        time.sleep(1)
        status = self.bg.check(task_id)
        data = json.loads(status)
        assert data["id"] == task_id
        assert data["status"] == "completed"

    def test_check_unknown_task(self):
        """check(unknown_id) 应返回错误"""
        result = self.bg.check("nonexistent")
        assert "Unknown" in result

    def test_check_list_all(self):
        """check() 不带参数应列出所有任务"""
        self.bg.run("echo a")
        self.bg.run("echo b")
        time.sleep(1)
        result = self.bg.check()
        assert "echo a" in result
        assert "echo b" in result

    def test_check_empty(self):
        """无任务时 check() 返回提示"""
        result = self.bg.check()
        assert "No background tasks" in result

    def test_drain_notifications_clears_queue(self):
        """drain_notifications 应清空通知队列"""
        self.bg.run("echo x")
        time.sleep(1)
        first = self.bg.drain_notifications()
        assert len(first) == 1
        second = self.bg.drain_notifications()
        assert len(second) == 0

    def test_timeout_task(self):
        """超时任务应标记为 timeout"""
        self.bg.run("sleep 10", timeout=1)
        time.sleep(2)
        notifs = self.bg.drain_notifications()
        assert len(notifs) == 1
        assert notifs[0]["status"] == "timeout"

    def test_detect_stalled(self):
        """detect_stalled 应返回运行时间过长的任务"""
        self.bg.tasks["stalled1"] = {
            "id": "stalled1",
            "status": "running",
            "command": "sleep 9999",
            "started_at": time.time() - 100,
        }
        stalled = self.bg.detect_stalled()
        assert "stalled1" in stalled

    def test_detect_not_stalled(self):
        """刚启动的任务不应被判定为卡住"""
        self.bg.tasks["fresh1"] = {
            "id": "fresh1",
            "status": "running",
            "command": "echo hi",
            "started_at": time.time(),
        }
        stalled = self.bg.detect_stalled()
        assert "fresh1" not in stalled

    def test_output_file_written(self):
        """完成后应写入完整输出文件"""
        self.bg.run("echo hello_world")
        time.sleep(1)
        notifs = self.bg.drain_notifications()
        assert len(notifs) == 1
        output_path = self.tmpdir / f"{notifs[0]['task_id']}.log"
        assert output_path.exists()
        content = output_path.read_text(encoding="utf-8")
        assert "hello_world" in content

    def test_preview_truncated(self):
        """长输出应截断 preview"""
        self.bg.run("python3 -c \"print('x' * 1000)\"")
        time.sleep(1)
        notifs = self.bg.drain_notifications()
        assert len(notifs) == 1
        assert len(notifs[0]["preview"]) <= 500

    def test_error_task(self):
        """执行失败的命令应标记为 error 或 completed(退出码非0)"""
        self.bg.run("nonexistent_command_xyz_12345")
        time.sleep(1)
        notifs = self.bg.drain_notifications()
        assert len(notifs) == 1
        assert notifs[0]["status"] in ("completed", "error")


class TestBeforeModelCall:
    """测试 before_model_call 函数"""

    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.bg = BackgroundManager(self.tmpdir, workdir=self.tmpdir)

    def test_no_notifications_no_injection(self):
        """无通知时不注入消息"""
        messages = [{"role": "user", "content": "hello"}]
        before_model_call(messages, self.bg)
        assert len(messages) == 1

    def test_injects_notification_as_user_message(self):
        """有通知时注入 <background-results> 消息"""
        self.bg.run("echo done")
        time.sleep(1)
        messages = [{"role": "user", "content": "hello"}]
        before_model_call(messages, self.bg)
        assert len(messages) == 2
        assert messages[1]["role"] == "user"
        assert "<background-results>" in messages[1]["content"]
        assert "[bg:" in messages[1]["content"]

    def test_drains_only_once(self):
        """排空后再次调用不应重复注入"""
        self.bg.run("echo once")
        time.sleep(1)
        messages = []
        before_model_call(messages, self.bg)
        assert len(messages) == 1
        before_model_call(messages, self.bg)
        assert len(messages) == 1

"""Agent Team -- persistent named teammates with file-based JSONL inboxes.

Each teammate runs its own agent loop in a separate thread.
Communication happens through append-only inbox files.

    Subagent (Task):   spawn -> execute -> return summary -> destroyed
    Teammate (Team):   spawn -> work -> idle -> work -> ... -> shutdown

    .team/config.json                   .team/inbox/
    +----------------------------+      +------------------+
    | {"team_name": "default",   |      | alice.jsonl      |
    |  "members": [              |      | bob.jsonl        |
    |    {"name":"alice",        |      | lead.jsonl       |
    |     "role":"coder",        |      +------------------+
    |     "status":"idle"}       |
    |  ]}                        |      send_message("alice", "fix bug"):
    +----------------------------+        open("alice.jsonl", "a").write(msg)
                                        read_inbox("alice"):
    spawn_teammate("alice","coder",...)   msgs = [json.loads(l) for l in ...]
         |                                open("alice.jsonl", "w").close()
         v                                return msgs  # drain
    Thread: alice             Thread: bob
    +------------------+      +------------------+
    | agent_loop       |      | agent_loop       |
    | status: working  |      | status: idle     |
    | ... runs tools   |      | ... waits ...    |
    | status -> idle   |      |                  |
    +------------------+      +------------------+

Key idea: teammates have names, inboxes, and independent loops.
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

VALID_MSG_TYPES = {
    "message",
    "broadcast",
    "shutdown_request",
    "shutdown_response",
}


class MessageBus:
    """JSONL-based inbox per teammate.

    Each teammate has a file at ``<inbox_dir>/<name>.jsonl``.
    Sending appends one JSON line; reading drains the whole file.
    """

    def __init__(self, inbox_dir: Path):
        self.dir = inbox_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._inbox_events: Dict[str, threading.Event] = {}

    def send(
        self,
        sender: str,
        to: str,
        content: str,
        msg_type: str = "message",
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Append a message envelope to *to*'s inbox file."""
        if msg_type not in VALID_MSG_TYPES:
            return f"Error: Invalid type '{msg_type}'. Valid: {VALID_MSG_TYPES}"

        msg: Dict[str, Any] = {
            "type": msg_type,
            "from": sender,
            "content": content,
            "timestamp": time.time(),
        }
        if extra:
            msg.update(extra)

        inbox_path = self.dir / f"{to}.jsonl"
        with open(inbox_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        # 唤醒接收方队友
        if to in self._inbox_events:
            self._inbox_events[to].set()
                
        return f"Sent {msg_type} to {to}: {content}"

    def read_inbox(self, name: str) -> List[Dict[str, Any]]:
        """Read and drain *name*'s inbox. Returns list of message dicts."""
        inbox_path = self.dir / f"{name}.jsonl"
        if not inbox_path.exists():
            return []

        messages: List[Dict[str, Any]] = []
        for line in inbox_path.read_text(encoding="utf-8").strip().splitlines():
            if line:
                messages.append(json.loads(line))

        # Drain: truncate the file after reading
        inbox_path.write_text("", encoding="utf-8")
        return messages

    def broadcast(
        self, sender: str, content: str, teammates: List[str]
    ) -> str:
        """Send a broadcast message to all teammates except sender."""
        count = 0
        for name in teammates:
            if name != sender:
                self.send(sender, name, content, "broadcast")
                count += 1
        return f"Broadcast to {count} teammates"


class TeammateManager:
    """Persistent teammate registry and worker-loop launcher.

    Manages a roster of named agents stored in ``.team/config.json``.
    Each teammate runs an independent agent loop in its own thread,
    checking its inbox for new messages before each LLM call.

    Args:
        team_dir: Directory for team config and inbox files.
        llm_caller: Callback ``(messages, tools, silent) -> (content, reasoning, tool_calls)``
        tool_executor: Callback ``(name, args) -> output_str``
    """

    MAX_TURNS = 50

    def __init__(
        self,
        team_dir: Path,
        llm_caller: Callable[..., tuple[str, str, List]],
        tool_executor: Callable[[str, dict], str],
    ):
        self.dir = team_dir
        self.dir.mkdir(exist_ok=True)
        self.config_path = self.dir / "config.json"
        self.config = self._load_config()
        self.threads: Dict[str, threading.Thread] = {}
        self.bus = MessageBus(self.dir / "inbox")
        self._llm_caller = llm_caller
        self._tool_executor = tool_executor
        # 保护 config 并发修改的锁
        self._config_lock = threading.Lock()

    # -- Config persistence --

    def _load_config(self) -> dict:
        if self.config_path.exists():
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        return {"team_name": "default", "members": []}

    def _save_config(self) -> None:
        self.config_path.write_text(
            json.dumps(self.config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _find_member(self, name: str) -> Optional[dict]:
        for m in self.config["members"]:
            if m["name"] == name:
                return m
        return None

    # -- Spawn / list --

    def spawn(self, name: str, role: str, prompt: str) -> str:
        """Create or re-activate a persistent teammate.

        If *name* already exists and is idle/shutdown, re-use the slot.
        Otherwise return an error.
        """
        member = self._find_member(name)
        if member:
            if member["status"] not in ("idle", "shutdown"):
                return f"Error: '{name}' is currently {member['status']}"
            member["status"] = "working"
            member["role"] = role
        else:
            member = {"name": name, "role": role, "status": "working"}
            self.config["members"].append(member)

        self._save_config()

        # 创建新的 Event 通知机制
        event = threading.Event()
        self.bus._inbox_events[name] = event

        thread = threading.Thread(
            target=self._teammate_loop,
            args=(name, role, prompt, event),
            daemon=True,
        )
        self.threads[name] = thread
        thread.start()

        return f"Spawned '{name}' (role: {role}): {prompt}"

    def list_all(self) -> str:
        """Return a human-readable roster of all teammates."""
        if not self.config["members"]:
            return "No teammates."
        lines = [f"Team: {self.config['team_name']}"]
        for m in self.config["members"]:
            lines.append(f"  {m['name']} ({m['role']}): {m['status']}")
        return "\n".join(lines)

    def member_names(self) -> List[str]:
        return [m["name"] for m in self.config["members"]]

    def shutdown_all(self) -> str:
        """Request all active teammates to shut down."""
        count = 0
        for m in self.config["members"]:
            if m["status"] == "working":
                self.bus.send("lead", m["name"], "Please shut down.", "shutdown_request")
                count += 1
        return f"Shutdown request sent to {count} teammates"

    # -- Teammate tools (subset available to teammates) --

    @staticmethod
    def _teammate_tools() -> List[dict]:
        """OpenAI-format tool definitions available to each teammate."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "Run a shell command.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read file contents.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write content to file.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_file",
                    "description": "Replace exact text in file.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old_text": {"type": "string"},
                            "new_text": {"type": "string"},
                        },
                        "required": ["path", "old_text", "new_text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "send_message",
                    "description": "Send message to a teammate's inbox.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "to": {"type": "string"},
                            "content": {"type": "string"},
                            "msg_type": {
                                "type": "string",
                                "enum": list(VALID_MSG_TYPES),
                            },
                        },
                        "required": ["to", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_inbox",
                    "description": "Read and drain your inbox.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_teammates",
                    "description": "List all teammates with name, role, status.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    # -- Teammate agent loop --

    def _teammate_loop(self, name: str, role: str, prompt: str, event: threading.Event) -> None:
        """The per-teammate agent loop running in its own thread.

        Each iteration:
        1. Drain inbox -> append to messages
        2. Call LLM
        3. If tool_use -> execute and loop
        4. If text-only -> break
        """
        # sys_prompt = (
        #     f"You are '{name}', role: {role}. "
        #     f"Complete your assigned task. "
        #     f"When you finish, send a brief summary of what you did to 'lead' "
        #     f"using the send_message tool (to='lead'). "
        #     f"If you encounter errors, report them to 'lead' as well."
        # )
        
        sys_prompt = (
            f"You are '{name}', role: {role}. "
            f"Use send_message to communicate. Complete your task."
            f"When you finish, send a brief summary of what you did."
        )

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt},
        ]
        tools = self._teammate_tools()
        shutdown_now = False
        
        while True:
            # 1. Drain inbox
            inbox = self.bus.read_inbox(name)
            
            if not inbox:
                # 没有消息，等待被唤醒（新消息或 shutdown）
                logger.debug("[team:%s] idle, waiting for inbox event", name)
                event.wait()
                event.clear()
                inbox = self.bus.read_inbox(name)   # 被唤醒后再读一次 inbox
                if not inbox:
                    continue    # 可能是虚假唤醒，继续循环
                
            for msg in inbox:
                if msg.get("type") == "shutdown_request":
                    shutdown_now = True
                    self._set_status(name, "shutdown")
                    return
                messages.append({
                    "role": "user",
                    "content": json.dumps(msg, ensure_ascii=False),
                })
                
            if shutdown_now:
                break
                
            sub_turn = 1
            for _ in range(self.MAX_TURNS):

                # 2. Call LLM
                try:
                    content, reasoning_content, tool_calls = self._llm_caller(
                        messages, tools, True
                    )
                except Exception as e:
                    logger.error("[team:%s] LLM call failed: %s", name, e)
                    break

                # 3. Build assistant message (include reasoning_content)
                assistant_msg: Dict[str, Any] = {"role": "assistant", "content": content}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                    assistant_msg["reasoning_content"] = reasoning_content
                if sub_turn > 1 and not tool_calls:
                    assistant_msg["reasoning_content"] = reasoning_content
                messages.append(assistant_msg)

                # 4. If no tools, we're done
                if not tool_calls:
                    break

                # 5. Execute tools
                results = []
                for tc in tool_calls:
                    fn_name = tc["function"]["name"]
                    fn_args = json.loads(tc["function"]["arguments"])

                    # Route send_message/read_inbox through bus
                    if fn_name == "send_message":
                        output = self.bus.send(
                            name,
                            fn_args["to"],
                            fn_args["content"],
                            fn_args.get("msg_type", "message"),
                        )
                    elif fn_name == "read_inbox":
                        output = json.dumps(
                            self.bus.read_inbox(name), ensure_ascii=False
                        )
                    elif fn_name == "list_teammates":
                        output = self.list_all()
                    else:
                        output = self._tool_executor(fn_name, fn_args)

                    logger.info("[team:%s] %s: %s", name, fn_name, str(output)[:120])
                    results.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": str(output),
                    })

                messages.extend(results)
                sub_turn += 1

            # Mark idle when loop ends
            self._set_status(name, "idle")

    def _set_status(self, name: str, status: str) -> None:
        member = self._find_member(name)
        if member and member["status"] != status:
            member["status"] = status
            self._save_config()

    # -- Teammate tools definition for lead agent --

    @staticmethod
    def lead_tools() -> List[dict]:
        """OpenAI-format tool definitions for the lead agent to manage the team."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "spawn_teammate",
                    "description": "Spawn a persistent teammate that runs in its own thread.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Unique teammate name"},
                            "role": {"type": "string", "description": "Role description (e.g. coder, tester)"},
                            "prompt": {"type": "string", "description": "Initial task prompt for the teammate"},
                        },
                        "required": ["name", "role", "prompt"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_teammates",
                    "description": "List all teammates with name, role, status.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "send_message",
                    "description": "Send a message to a teammate's inbox.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "to": {"type": "string", "description": "Teammate name"},
                            "content": {"type": "string", "description": "Message content"},
                            "msg_type": {
                                "type": "string",
                                "enum": list(VALID_MSG_TYPES),
                            },
                        },
                        "required": ["to", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_inbox",
                    "description": "Read and drain the lead's inbox.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "broadcast",
                    "description": "Send a message to all teammates.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                        },
                        "required": ["content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "shutdown_teammate",
                    "description": "Request a teammate to shut down.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                },
            },
        ]

    def execute_lead_tool(self, name: str, args: dict) -> str:
        """Dispatch a lead-level team tool call."""
        if name == "spawn_teammate":
            return self.spawn(args["name"], args["role"], args["prompt"])
        if name == "list_teammates":
            return self.list_all()
        if name == "send_message":
            return self.bus.send("lead", args["to"], args["content"], args.get("msg_type", "message"))
        if name == "read_inbox":
            return json.dumps(self.bus.read_inbox("lead"), indent=2, ensure_ascii=False)
        if name == "broadcast":
            return self.bus.broadcast("lead", args["content"], self.member_names())
        if name == "shutdown_teammate":
            return self.bus.send("lead", args["name"], "Please shut down.", "shutdown_request")
        return f"Unknown team tool: {name}"

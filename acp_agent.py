"""ACP (Agent Client Protocol) agent implementation for ask-agent.

Wraps ask-agent's core agent loop as an ACP-compatible agent,
allowing it to run inside Zed, JetBrains, and other ACP clients.

Usage: ag --acp
"""

import asyncio
import json
import logging
import platform
import subprocess
import uuid
from pathlib import Path
from typing import Any, Literal, cast

from acp import (
    PROTOCOL_VERSION,
    Agent,
    InitializeResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PromptResponse,
    SetSessionModeResponse,
    SetSessionModelResponse,
    start_tool_call,
    text_block,
    tool_content,
    update_agent_message,
    update_agent_thought,
    update_tool_call,
)
from acp.interfaces import Client
from acp.schema import (
    AgentCapabilities,
    ClientCapabilities,
    ForkSessionResponse,
    Implementation,
    ListSessionsResponse,
    ModelInfo,
    PromptCapabilities,
    ResumeSessionResponse,
    SessionInfo,
    SessionMode,
    SessionModeState,
    SessionModelState,
)
from util import format_range_info

logger = logging.getLogger(__name__)

_acp_log_path = Path.home() / ".ask-agent" / "log-acp.txt"
_acp_log_path.parent.mkdir(parents=True, exist_ok=True)
_acp_debug = logging.getLogger("acp_debug")
_file_handler = logging.FileHandler(str(_acp_log_path), encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
_acp_debug.addHandler(_file_handler)
_acp_debug.propagate = False

TOOL_KIND = Literal[
    "read",
    "edit",
    "delete",
    "move",
    "search",
    "execute",
    "think",
    "fetch",
    "switch_mode",
    "other",
]

_TOOL_KIND_MAP: dict[str, str] = {
    "bash": "execute",
    "read_file": "read",
    "glob": "read",
    "grep": "read",
    "write_file": "edit",
    "edit_file": "edit",
    "webfetch": "fetch",
    "Task": "think",
    "Skill": "other",
    "MCP": "other",
    "TodoWrite": "other",
}

_TOOL_TITLE_MAP: dict[str, str] = {
    "TodoWrite": "Update todos",
}


def _get_tool_kind(name: str) -> str:
    if name.startswith("mcp_"):
        return "execute"
    return _TOOL_KIND_MAP.get(name, "other")


def _tool_title(name: str, args: dict) -> str:
    if name == "bash":
        return f"$ {args.get('command', '')[:60]}"
    if name == "read_file":
        path = args.get("path", "")
        return f"→ Read {path}{format_range_info(args)}"
    if name == "write_file":
        return f"→ Write {args.get('path', '')}"
    if name == "edit_file":
        return f"→ Edit {args.get('path', '')}"
    if name == "glob":
        path = args.get("path", "")
        path_info = f" in {path}" if path else ""
        return f"✱ Glob {args.get('pattern', '')}{path_info}"
    if name == "grep":
        path = args.get("path", "")
        path_info = f" in {path}" if path else ""
        return f'✱ Grep "{args.get("pattern", "")}"{path_info}'
    if name == "webfetch":
        return f"% Fetch {args.get('url', '')[:50]}"
    if name == "Task":
        return f"Task: {args.get('description', '')}"
    if name == "Skill":
        return f"Load skill: {args.get('skill', '')}"
    if name == "MCP":
        return f"→ Connect MCP: {args.get('server', '')}"
    return _TOOL_TITLE_MAP.get(name, name)


PLAN_MODE = SessionMode(
    id="plan",
    name="Plan",
    description="Read-only planning mode. Analyze code, design strategies, no file changes.",
)
BUILD_MODE = SessionMode(
    id="build",
    name="Build",
    description="Full build mode. Execute commands, read/write files, all tools available.",
)


class AskAgentACP(Agent):
    """ACP agent wrapping ask-agent's core capabilities."""

    def __init__(self) -> None:
        self._conn: Client  # set by on_connect
        self._sessions: dict[str, list[dict]] = {}
        self._session_models: dict[str, str] = {}
        self._session_modes: dict[str, str] = {}
        self._client_caps: dict[str, bool] = {}
        self._cancelled: set[str] = set()
        self._current_process: subprocess.Popen | None = None
        self._process_lock = asyncio.Lock()

    # ── Helpers ──────────────────────────────────────────────────────

    def _get_available_models(self) -> tuple[list[ModelInfo], str]:
        from ask import PROVIDER_CONFIG, list_models

        models = list_models()
        model_infos = [
            ModelInfo(
                model_id=mid,
                name=f"{info.name} ({info.provider_id})"
                if (info := PROVIDER_CONFIG.get_model_info(mid))
                else mid,
            )
            for mid in models
        ]
        default = PROVIDER_CONFIG.default_model or (models[0] if models else "")
        return model_infos, default

    def _apply_model(self, model_id: str) -> None:
        import ask as _ask

        api_config = _ask.PROVIDER_CONFIG.get_api_config(model_id)
        if api_config:
            _ask.DEEPSEEK_API_URL = api_config["base_url"].rstrip("/v1")
            _ask.DEEPSEEK_API_KEY = api_config["api_key"]
            _ask.DEEPSEEK_MODEL = api_config["model"]

    def _set_cwd(self, cwd: str) -> None:
        if cwd:
            import ask as _ask

            _ask.WORKDIR = Path(cwd)

    def on_connect(self, conn: Client) -> None:
        self._conn = conn

    # ── Initialization ──────────────────────────────────────────────

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        # Store client capabilities for tool routing
        self._client_caps = {}
        if client_capabilities:
            self._client_caps["terminal"] = bool(
                getattr(client_capabilities, "terminal", False)
            )
            fs = getattr(client_capabilities, "fs", None)
            if fs:
                self._client_caps["fs_read"] = bool(
                    getattr(fs, "read_text_file", False)
                )
                self._client_caps["fs_write"] = bool(
                    getattr(fs, "write_text_file", False)
                )

        _acp_debug.info(
            "INITIALIZE: client=%s protocol=%d caps=%s",
            getattr(client_info, "name", "unknown"),
            protocol_version,
            self._client_caps,
        )
        logger.info(
            "ACP initialize: client=%s, protocol=%d, caps=%s",
            getattr(client_info, "name", "unknown"),
            protocol_version,
            self._client_caps,
        )
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(embedded_context=True),
            ),
            agent_info=Implementation(
                name="ask-agent", title="Ask Agent", version="1.0.0"
            ),
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> Any:
        return None

    # ── Session Management ──────────────────────────────────────────

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        self._sessions[session_id] = []
        model_infos, default_model = self._get_available_models()
        self._session_models[session_id] = default_model
        self._session_modes[session_id] = "build"
        self._set_cwd(cwd)

        logger.info(
            "ACP new_session: %s (cwd=%s, model=%s)", session_id, cwd, default_model
        )
        return NewSessionResponse(
            session_id=session_id,
            models=SessionModelState(
                available_models=model_infos,
                current_model_id=default_model,
            ),
            modes=SessionModeState(
                available_modes=[BUILD_MODE, PLAN_MODE],
                current_mode_id="build",
            ),
        )

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        logger.info("ACP load_session: %s", session_id)
        self._sessions.setdefault(session_id, [])
        self._session_models.setdefault(session_id, self._get_available_models()[1])
        self._session_modes.setdefault(session_id, "build")
        self._set_cwd(cwd)
        return LoadSessionResponse()

    async def list_sessions(
        self, cursor: str | None = None, cwd: str | None = None, **kwargs: Any
    ) -> ListSessionsResponse:
        return ListSessionsResponse(
            sessions=[
                SessionInfo(session_id=sid, cwd=cwd or "") for sid in self._sessions
            ]
        )

    async def set_session_mode(
        self, mode_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModeResponse | None:
        if mode_id not in ("plan", "build"):
            logger.warning("ACP set_session_mode: unknown mode %s", mode_id)
            return None
        old_mode = self._session_modes.get(session_id, "build")
        self._session_modes[session_id] = mode_id
        if old_mode != mode_id:
            self._sessions[session_id] = []
        logger.info("ACP set_session_mode: session=%s mode=%s", session_id, mode_id)
        return SetSessionModeResponse()

    async def set_session_model(
        self, model_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModelResponse | None:
        from ask import PROVIDER_CONFIG

        if not PROVIDER_CONFIG.get_api_config(model_id):
            logger.warning("ACP set_session_model: unknown model %s", model_id)
            return None
        self._session_models[session_id] = model_id
        self._apply_model(model_id)
        logger.info("ACP set_session_model: session=%s model=%s", session_id, model_id)
        return SetSessionModelResponse()

    async def set_config_option(
        self, config_id: str, session_id: str, value: str, **kwargs: Any
    ) -> Any:
        return None

    async def fork_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        new_id = f"sess_{uuid.uuid4().hex[:12]}"
        self._sessions[new_id] = list(self._sessions.get(session_id, []))
        return ForkSessionResponse(session_id=new_id)

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        self._sessions.setdefault(session_id, [])
        return ResumeSessionResponse()

    # ── Tool Execution ──────────────────────────────────────────────

    async def _stream_response(
        self, messages: list, tools: list, use_tools: bool = True
    ):
        """Async generator: yields (kind, payload) tuples as tokens arrive."""
        import ask as _ask
        from ask import merge_arguments
        import requests as _requests

        headers = {
            "Authorization": f"Bearer {_ask.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        data = {
            "model": _ask.DEEPSEEK_MODEL,
            "messages": messages,
            "stream": True,
            "thinking": {"type": _ask.get_thinking_mode()},
        }
        if use_tools:
            data["tools"] = tools
            data["tool_choice"] = "auto"

        collected = ""
        tool_calls_collected = []
        reasoning = ""
        in_think = False

        with _requests.post(
            f"{_ask.DEEPSEEK_API_URL}/v1/chat/completions",
            headers=headers,
            json=data,
            stream=True,
            timeout=(10, 30),
        ) as response:
            _ask._streaming_response = response
            if response.status_code != 200:
                yield ("text", f"Error: {response.status_code}")
                yield ("done", ("", "", []))
                return

            response.encoding = 'utf-8'
            for chunk in response.iter_lines(decode_unicode=True):
                if not chunk:
                    continue
                if _ask._interrupted:
                    break
                if not chunk.startswith("data:"):
                    continue
                if chunk == "data: [DONE]":
                    break
                try:
                    obj = json.loads(chunk[6:])
                    if not obj["choices"]:
                        continue
                    # if obj["choices"][0]["finish_reason"] is not None:
                    #     continue
                    delta = obj["choices"][0].get("delta")
                    if not delta:
                        continue

                    if delta.get("reasoning_content"):
                        reasoning += delta["reasoning_content"]
                        yield ("thought", delta["reasoning_content"])
                    elif delta.get("content"):
                        content = delta["content"]
                        # if "<think>" in content:
                        #     in_think = True
                        #     # content = content.replace("<think>", "")
                        # if "</think>" in content:
                        #     in_think = False
                        #     # content = content.replace("</think>", "")
                        #     collected += content
                        #     yield ("thought", content)
                        #     continue
                        # if in_think:
                        #     collected += content
                        #     yield ("thought", content)
                        #     continue
                        collected += content
                        yield ("text", content)
                    elif delta.get("tool_calls"):
                        for tc in delta["tool_calls"]:
                            tool_calls_collected.append(tc)
                except json.JSONDecodeError:
                    continue

        _ask._streaming_response = None
        tool_calls = merge_arguments(tool_calls_collected)
        yield ("done", (collected, reasoning, tool_calls))

    def _resolve_path(self, raw_path: str) -> str:
        """Resolve relative path against WORKDIR."""
        import ask as _ask

        if raw_path and not Path(raw_path).is_absolute():
            return str(_ask.WORKDIR / raw_path)
        return raw_path

    async def _execute_tool(self, name: str, args: dict, session_id: str) -> str:
        """Execute a tool, preferring client fs when available."""
        from ask import execute_tool

        # Try client fs for read_file (only if client supports it)
        if name == "read_file" and self._client_caps.get("fs_read"):
            try:
                path = self._resolve_path(args.get("path", ""))
                _acp_debug.info("TOOL: > fs_read %s", path)
                resp = await self._conn.read_text_file(
                    path=path,
                    session_id=session_id,
                    line=args.get("offset"),
                    limit=args.get("limit"),
                )
                if resp.content:
                    _acp_debug.debug("TOOL: fs_read success, len=%d", len(resp.content))
                    return resp.content
                _acp_debug.info("TOOL: fs_read returned empty content, falling back")
            except Exception as e:
                _acp_debug.info("TOOL: client fs_read failed: %s", e)

        # Try client fs for write_file (only if client supports it)
        if name == "write_file" and self._client_caps.get("fs_write"):
            try:
                path = self._resolve_path(args.get("path", ""))
                content = args.get("content", "")
                _acp_debug.info("TOOL: > fs_write %s", path)
                await self._conn.write_text_file(
                    content=content,
                    path=path,
                    session_id=session_id,
                )
                return f"Wrote {len(content)} bytes to {path}"
            except Exception as e:
                _acp_debug.info("TOOL: fs_write failed: %s", e)

        # Special handling for bash: use Popen for cancellable execution
        if name == "bash":
            return await self._execute_bash(args.get("command", ""), args.get("timeout"))

        # Default: local execution (fs fallback)
        _acp_debug.info("TOOL: > %s args=%s", name, str(args)[:300])
        return await asyncio.to_thread(execute_tool, name, args)

    async def _execute_bash(self, command: str, timeout: int | None = None) -> str:
        """Execute bash command with cancellation support using Popen."""
        import ask as _ask

        if any(d in command for d in ["rm -rf /", "shutdown"]):
            return "Error: Dangerous command blocked"

        _acp_debug.info("BASH: $ %s", command)

        async with self._process_lock:
            # Windows 上使用 PowerShell，其他系统使用默认 shell
            if platform.system() == "Windows":
                self._current_process = subprocess.Popen(
                    ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
                    cwd=str(_ask.WORKDIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                )
            else:
                self._current_process = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=str(_ask.WORKDIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

        try:
            stdout, stderr = await asyncio.to_thread(
                self._current_process.communicate,
                timeout=timeout or 10,
            )
            output = (stdout + stderr).strip()
            _acp_debug.info("BASH: exit=%d output_len=%d", self._current_process.returncode, len(output))
            return output
        except subprocess.TimeoutExpired:
            async with self._process_lock:
                if self._current_process:
                    self._current_process.kill()
                    self._current_process = None
            return f"Error: Command timed out after {timeout or 10}s"
        except asyncio.CancelledError:
            async with self._process_lock:
                if self._current_process:
                    _acp_debug.info("BASH: cancelled, killing process")
                    self._current_process.kill()
                    self._current_process = None
            raise
        finally:
            async with self._process_lock:
                self._current_process = None

    # ── Prompt Turn ─────────────────────────────────────────────────

    async def prompt(
        self,
        prompt: list[Any],
        session_id: str,
        **kwargs: Any,
    ) -> PromptResponse:
        from ask import SYSTEM_PROMPT_AGENT, TOOLS, AGENT
        import ask as _ask

        sess_msgs = self._sessions.setdefault(session_id, [])

        if not sess_msgs:
            sess_msgs.append({"role": "system", "content": SYSTEM_PROMPT_AGENT})

        parts: list[str] = []
        _acp_debug.info("PROMPT: session=%s blocks=%d", session_id, len(prompt))
        for i, block in enumerate(prompt):
            block_type = getattr(block, "type", None)
            _acp_debug.debug(
                "PROMPT block[%d]: type=%s",
                i,
                block_type,
            )
            if block_type == "text":
                preview = block.text[:200]
                _acp_debug.debug("PROMPT block[%d] text: %s", i, preview)
                parts.append(block.text)
            elif block_type == "resource":
                resource = block.resource
                uri = getattr(resource, "uri", "unknown")
                if hasattr(resource, "text"):
                    preview = resource.text[:300]
                    _acp_debug.debug(
                        "PROMPT block[%d] resource(text): uri=%s len=%d preview=%s",
                        i,
                        uri,
                        len(resource.text),
                        preview,
                    )
                    parts.append(f"[Attachment: {uri}]\n```\n{resource.text}\n```")
                elif hasattr(resource, "blob"):
                    mime = getattr(resource, "mime_type", "unknown")
                    _acp_debug.debug(
                        "PROMPT block[%d] resource(blob): uri=%s mime=%s",
                        i,
                        uri,
                        mime,
                    )
                    parts.append(f"[Attachment: {uri}] (binary, {mime})")
                else:
                    _acp_debug.debug(
                        "PROMPT block[%d] resource(unknown): uri=%s resource_attrs=%s",
                        i,
                        uri,
                        [a for a in dir(resource) if not a.startswith("_")],
                    )
            elif block_type == "resource_link":
                uri = getattr(block, "uri", "")
                name = getattr(block, "name", uri)
                parts.append(f"[File reference: {name} ({uri})]")
            elif block_type == "image":
                parts.append("[Image attachment included]")
            elif block_type == "audio":
                parts.append("[Audio attachment included]")
            elif hasattr(block, "text"):
                parts.append(block.text)
            else:
                _acp_debug.info("PROMPT block[%d] unknown type, skipped", i)
        user_text = "\n".join(parts)
        _acp_debug.info("PROMPT: User Prompt: %s", user_text)
        sess_msgs.append({"role": "user", "content": user_text})

        use_tools = self._session_modes.get(session_id, "build") == "build"

        # Track reasoning cleanup
        reasoning_start_index = len(sess_msgs)
        sub_turn = 1

        while True:
            _ask._interrupted = False

            if session_id in self._cancelled:
                self._cancelled.discard(session_id)
                return PromptResponse(stop_reason="cancelled")

            content = ""
            reasoning_content = ""
            tool_calls: list = []

            async for event in self._stream_response(sess_msgs, TOOLS, use_tools):
                kind, payload = event

                if session_id in self._cancelled:
                    self._cancelled.discard(session_id)
                    return PromptResponse(stop_reason="cancelled")

                if kind == "done":
                    content, reasoning_content, tool_calls = payload  # type: ignore
                elif kind == "text":
                    await self._conn.session_update(
                        session_id,
                        update_agent_message(text_block(str(payload))),
                    )
                elif kind == "thought":
                    await self._conn.session_update(
                        session_id,
                        update_agent_thought(text_block(str(payload))),
                    )

            if session_id in self._cancelled:
                self._cancelled.discard(session_id)
                return PromptResponse(stop_reason="cancelled")

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": content,
            }
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
                # 思考模式下，有工具调用时须回传 reasoning_content
                assistant_msg["reasoning_content"] = reasoning_content
            if sub_turn > 1 and not tool_calls: # 工具调用结束时，此时tool_calls=[]，需要额外判定追加 reasoning_content
                assistant_msg["reasoning_content"] = reasoning_content      
            sess_msgs.append(assistant_msg)

            if not tool_calls or _ask._interrupted:
                _ask._interrupted = False
                return PromptResponse(stop_reason="end_turn")

            for tc in tool_calls:
                if session_id in self._cancelled:
                    self._cancelled.discard(session_id)
                    return PromptResponse(stop_reason="cancelled")

                tc_id = tc.get("id", f"tc_{uuid.uuid4().hex[:8]}")
                func = tc.get("function", {})
                name = func.get("name", "")
                raw_args = func.get("arguments", "{}")

                try:
                    args = (
                        json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    )
                except json.JSONDecodeError:
                    args = {}

                await self._conn.session_update(
                    session_id,
                    start_tool_call(
                        tool_call_id=tc_id,
                        title=_tool_title(name, args),
                        kind=cast(TOOL_KIND, _get_tool_kind(name)),
                        status="in_progress",
                        raw_input=args,
                    ),
                )

                try:
                    output = await self._execute_tool(name, args, session_id)
                except Exception as e:
                    output = f"Error: {e}"

                # Don't send grep/glob/webfetch results to client
                if name in ("grep", "glob", "webfetch"):
                    await self._conn.session_update(
                        session_id,
                        update_tool_call(
                            tool_call_id=tc_id,
                            status="completed",
                            content=[],
                            raw_output="",
                        ),
                    )
                else:
                    display = output[:2000] + "..." if len(output) > 2000 else output
                    await self._conn.session_update(
                        session_id,
                        update_tool_call(
                            tool_call_id=tc_id,
                            status="completed",
                            content=[tool_content(text_block(display))],
                            raw_output=output[:5000],
                        ),
                    )

                _acp_debug.info("TOOL: Result -> %s", output[:400])    

                sess_msgs.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": output,
                    }
                )

            sub_turn += 1
    
            
    # ── Cancellation ────────────────────────────────────────────────

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        import ask as _ask

        logger.info("ACP cancel: session=%s", session_id)
        self._cancelled.add(session_id)
        _ask._interrupted = True
        _ask._close_streaming_response()

        # Kill any running subprocess
        async with self._process_lock:
            if self._current_process:
                _acp_debug.info("CANCEL: killing subprocess pid=%d", self._current_process.pid)
                try:
                    self._current_process.kill()
                except Exception as e:
                    _acp_debug.info("CANCEL: failed to kill subprocess: %s", e)
                self._current_process = None

    # ── Extensions ──────────────────────────────────────────────────

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        pass


# ── Entry Point ─────────────────────────────────────────────────────


async def run_acp_agent(log_level: str = "ERROR") -> None:
    from acp import run_agent
    from ask import init_providers, init_command_manager, AGENT
    import ask as _ask

    # 设置ACP调试日志级别
    _acp_debug.setLevel(getattr(logging, log_level.upper(), logging.ERROR))

    init_providers()
    init_command_manager()

    # ACP always works in AGENT mode
    _ask.current_mode = AGENT

    logger.info("Starting ask-agent in ACP mode (protocol v%d)", PROTOCOL_VERSION)
    await run_agent(AskAgentACP(), use_unstable_protocol=True)

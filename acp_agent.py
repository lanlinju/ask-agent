"""ACP (Agent Client Protocol) agent implementation for ask-agent.

Wraps ask-agent's core agent loop as an ACP-compatible agent,
allowing it to run inside Zed, JetBrains, and other ACP clients.

Usage: ag --acp
"""

import asyncio
import json
import logging
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

logger = logging.getLogger(__name__)

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
        return f"Read {args.get('path', '')}"
    if name == "write_file":
        return f"Write {args.get('path', '')}"
    if name == "edit_file":
        return f"Edit {args.get('path', '')}"
    if name == "glob":
        return f"Glob {args.get('pattern', '')}"
    if name == "grep":
        return f'Grep "{args.get("pattern", "")}"'
    if name == "webfetch":
        return f"Fetch {args.get('url', '')[:50]}"
    if name == "Task":
        return f"Task: {args.get('description', '')}"
    if name == "Skill":
        return f"Load skill: {args.get('skill', '')}"
    if name == "MCP":
        return f"Connect MCP: {args.get('server', '')}"
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

    async def _execute_tool(self, name: str, args: dict, session_id: str) -> str:
        """Execute a tool, preferring client fs when available."""
        from ask import execute_tool

        # Try client fs for read_file (only if client supports it)
        if name == "read_file" and self._client_caps.get("fs_read"):
            try:
                resp = await self._conn.read_text_file(
                    path=args.get("path", ""),
                    session_id=session_id,
                    line=args.get("offset"),
                    limit=args.get("limit"),
                )
                if resp.text is not None:
                    return resp.text
            except Exception:
                pass

        # Try client fs for write_file (only if client supports it)
        if name == "write_file" and self._client_caps.get("fs_write"):
            try:
                path = args.get("path", "")
                content = args.get("content", "")
                await self._conn.write_text_file(
                    content=content,
                    path=path,
                    session_id=session_id,
                )
                return f"Wrote {len(content)} bytes to {path}"
            except Exception:
                pass

        # Default: local execution (bash always local, fs fallback)
        return await asyncio.to_thread(execute_tool, name, args)

    # ── Prompt Turn ─────────────────────────────────────────────────

    async def prompt(
        self,
        prompt: list[Any],
        session_id: str,
        **kwargs: Any,
    ) -> PromptResponse:
        from ask import SYSTEM_PROMPT_AGENT, TOOLS, get_streaming_response, AGENT
        import ask as _ask

        sess_msgs = self._sessions.setdefault(session_id, [])

        # Initialize system prompt on first turn
        if not sess_msgs:
            sess_msgs.append({"role": "system", "content": SYSTEM_PROMPT_AGENT})

        # Extract user text from prompt content blocks
        user_text = "\n".join(block.text for block in prompt if hasattr(block, "text"))
        sess_msgs.append({"role": "user", "content": user_text})

        # Save and apply session-specific config
        orig_url, orig_key, orig_model = (
            _ask.DEEPSEEK_API_URL,
            _ask.DEEPSEEK_API_KEY,
            _ask.DEEPSEEK_MODEL,
        )
        orig_mode = _ask.current_mode

        session_model = self._session_models.get(session_id)
        if session_model:
            self._apply_model(session_model)

        use_tools = self._session_modes.get(session_id, "build") == "build"

        try:
            while True:
                _ask._interrupted = False

                if session_id in self._cancelled:
                    self._cancelled.discard(session_id)
                    return PromptResponse(stop_reason="cancelled")

                # Force AGENT mode for system prompt selection
                _ask.current_mode = AGENT
                try:
                    content, reasoning_content, tool_calls = await asyncio.to_thread(
                        get_streaming_response, sess_msgs, TOOLS, True, use_tools
                    )
                finally:
                    _ask.current_mode = orig_mode

                if session_id in self._cancelled:
                    self._cancelled.discard(session_id)
                    return PromptResponse(stop_reason="cancelled")

                # Stream updates to client
                if content:
                    await self._conn.session_update(
                        session_id, update_agent_message(text_block(content))
                    )
                if reasoning_content:
                    await self._conn.session_update(
                        session_id, update_agent_thought(text_block(reasoning_content))
                    )

                # Record assistant message
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": content,
                }
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                    if reasoning_content:
                        assistant_msg["reasoning_content"] = reasoning_content
                sess_msgs.append(assistant_msg)

                if not tool_calls:
                    return PromptResponse(stop_reason="end_turn")

                # Execute tool calls
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
                            json.loads(raw_args)
                            if isinstance(raw_args, str)
                            else raw_args
                        )
                    except json.JSONDecodeError:
                        args = {}

                    # Notify: tool start
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

                    # Execute
                    try:
                        output = await self._execute_tool(name, args, session_id)
                    except Exception as e:
                        output = f"Error: {e}"

                    # Notify: tool completed
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

                    sess_msgs.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": output,
                        }
                    )
        finally:
            _ask.DEEPSEEK_API_URL = orig_url
            _ask.DEEPSEEK_API_KEY = orig_key
            _ask.DEEPSEEK_MODEL = orig_model

    # ── Cancellation ────────────────────────────────────────────────

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        import ask as _ask

        logger.info("ACP cancel: session=%s", session_id)
        self._cancelled.add(session_id)
        _ask._interrupted = True
        _ask._close_streaming_response()

    # ── Extensions ──────────────────────────────────────────────────

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        pass


# ── Entry Point ─────────────────────────────────────────────────────


async def run_acp_agent() -> None:
    from acp import run_agent
    from ask import init_providers, init_command_manager

    init_providers()
    init_command_manager()

    logger.info("Starting ask-agent in ACP mode (protocol v%d)", PROTOCOL_VERSION)
    await run_agent(AskAgentACP(), use_unstable_protocol=True)

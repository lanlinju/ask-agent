"""ACP (Agent Client Protocol) agent implementation for ask-agent.

Wraps ask-agent's core agent loop as an ACP-compatible agent,
allowing it to run inside Zed, JetBrains, and other ACP clients.

Usage: ag --acp
"""

import asyncio
import json
import logging
import uuid
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
    update_tool_call,
)
from acp.interfaces import Client
from acp.schema import (
    AgentCapabilities,
    ClientCapabilities,
    ForkSessionResponse,
    Implementation,
    ListSessionsResponse,
    McpCapabilities,
    ModelInfo,
    PromptCapabilities,
    ResumeSessionResponse,
    SessionInfo,
    SessionModelState,
    TextContentBlock,
)

logger = logging.getLogger(__name__)


def _get_tool_kind(name: str) -> str:
    """Map tool name to ACP ToolKind."""
    kind_map = {
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
    if name.startswith("mcp_"):
        return "execute"
    return kind_map.get(name, "other")


def _tool_title(name: str, args: dict) -> str:
    """Generate a human-readable title for a tool call."""
    if name == "bash":
        cmd = args.get("command", "")
        return f"$ {cmd[:60]}"
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
    if name == "TodoWrite":
        return "Update todos"
    if name == "Task":
        return f"Task: {args.get('description', '')}"
    if name == "Skill":
        return f"Load skill: {args.get('skill', '')}"
    if name == "MCP":
        return f"Connect MCP: {args.get('server', '')}"
    return name


class AskAgentACP(Agent):
    """ACP agent wrapping ask-agent's core capabilities."""

    _conn: Client
    _sessions: dict[str, list[dict]]
    _session_models: dict[str, str]
    _cancelled: set[str]
    _next_session_num: int

    def __init__(self) -> None:
        self._sessions = {}
        self._session_models = {}
        self._cancelled = set()
        self._next_session_num = 0

    def _get_available_models(self) -> tuple[list[ModelInfo], str]:
        """Get available models and current default model ID."""
        from ask import list_models, PROVIDER_CONFIG

        models = list_models()
        model_infos = []
        for mid in models:
            info = PROVIDER_CONFIG.get_model_info(mid)
            name = info.name if info else mid
            provider = info.provider_id if info else ""
            model_infos.append(
                ModelInfo(
                    model_id=mid,
                    name=f"{name} ({provider})" if provider else name,
                )
            )
        default = PROVIDER_CONFIG.default_model or (models[0] if models else "")
        return model_infos, default

    def _apply_model(self, model_id: str) -> None:
        """Set global LLM config to use the given model."""
        import ask as _ask

        api_config = _ask.PROVIDER_CONFIG.get_api_config(model_id)
        if api_config:
            _ask.DEEPSEEK_API_URL = api_config["base_url"].rstrip("/v1")
            _ask.DEEPSEEK_API_KEY = api_config["api_key"]
            _ask.DEEPSEEK_MODEL = api_config["model"]

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
        logger.info(
            "ACP initialize: client=%s, protocol=%d",
            getattr(client_info, "name", "unknown"),
            protocol_version,
        )
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(embedded_context=True),
            ),
            agent_info=Implementation(
                name="ask-agent",
                title="Ask Agent",
                version="1.0.0",
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
        logger.info(
            "ACP new_session: %s (cwd=%s, model=%s)", session_id, cwd, default_model
        )
        return NewSessionResponse(
            session_id=session_id,
            models=SessionModelState(
                available_models=model_infos,
                current_model_id=default_model,
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
        if session_id not in self._sessions:
            self._sessions[session_id] = []
        model_infos, default_model = self._get_available_models()
        if session_id not in self._session_models:
            self._session_models[session_id] = default_model
        return LoadSessionResponse()

    async def list_sessions(
        self,
        cursor: str | None = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        sessions = []
        for sid in self._sessions:
            sessions.append(SessionInfo(session_id=sid, cwd=cwd or ""))
        return ListSessionsResponse(sessions=sessions)

    async def set_session_mode(
        self, mode_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModeResponse | None:
        return SetSessionModeResponse()

    async def set_session_model(
        self, model_id: str, session_id: str, **kwargs: Any
    ) -> SetSessionModelResponse | None:
        from ask import PROVIDER_CONFIG

        # Validate model exists
        api_config = PROVIDER_CONFIG.get_api_config(model_id)
        if not api_config:
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
    ) -> Any:
        new_id = f"sess_{uuid.uuid4().hex[:12]}"
        if session_id in self._sessions:
            self._sessions[new_id] = list(self._sessions[session_id])
        else:
            self._sessions[new_id] = []
        from acp.schema import ForkSessionResponse

        return ForkSessionResponse(session_id=new_id)

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        from acp.schema import ResumeSessionResponse

        if session_id not in self._sessions:
            self._sessions[session_id] = []
        return ResumeSessionResponse()

    # ── Prompt Turn ─────────────────────────────────────────────────

    async def prompt(
        self,
        prompt: list[Any],
        session_id: str,
        **kwargs: Any,
    ) -> PromptResponse:
        from ask import (
            SYSTEM_PROMPT_AGENT,
            TOOLS,
            DEEPSEEK_API_KEY,
            DEEPSEEK_API_URL,
            DEEPSEEK_MODEL,
            execute_tool,
            get_streaming_response,
            merge_arguments,
            current_mode,
            AGENT,
            logger as ask_logger,
        )
        import ask as _ask

        # Ensure session exists
        if session_id not in self._sessions:
            self._sessions[session_id] = []

        sess_msgs = self._sessions[session_id]

        # Initialize system prompt on first turn
        if not sess_msgs:
            sess_msgs.append({"role": "system", "content": SYSTEM_PROMPT_AGENT})

        # Extract text from prompt content blocks
        user_text = "\n".join(block.text for block in prompt if hasattr(block, "text"))
        sess_msgs.append({"role": "user", "content": user_text})

        # Save original global model config
        orig_url = _ask.DEEPSEEK_API_URL
        orig_key = _ask.DEEPSEEK_API_KEY
        orig_model = _ask.DEEPSEEK_MODEL
        orig_mode = _ask.current_mode

        # Apply session-specific model
        session_model = self._session_models.get(session_id)
        if session_model:
            self._apply_model(session_model)

        turn = 0
        try:
            while True:
                turn += 1

                # Reset interrupt flag before each LLM call
                _ask._interrupted = False

                # Check cancellation
                if session_id in self._cancelled:
                    self._cancelled.discard(session_id)
                    return PromptResponse(stop_reason="cancelled")

                # Force tools on
                _ask.current_mode = AGENT

                try:
                    content, reasoning_content, tool_calls = await asyncio.to_thread(
                        get_streaming_response, sess_msgs, TOOLS, True
                    )
                finally:
                    _ask.current_mode = orig_mode

                # Check cancellation after LLM call
                if session_id in self._cancelled:
                    self._cancelled.discard(session_id)
                    return PromptResponse(stop_reason="cancelled")

                # Stream agent text to client
                if content:
                    await self._conn.session_update(
                        session_id, update_agent_message(text_block(content))
                    )

                # Stream reasoning/thought if present
                if reasoning_content:
                    from acp import update_agent_thought

                    await self._conn.session_update(
                        session_id, update_agent_thought(text_block(reasoning_content))
                    )

                # Build assistant message for history
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": content,
                }
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                    if reasoning_content:
                        assistant_msg["reasoning_content"] = reasoning_content
                sess_msgs.append(assistant_msg)

                # No tool calls → turn complete
                if not tool_calls:
                    return PromptResponse(stop_reason="end_turn")

                # Execute each tool call
                for tc in tool_calls:
                    # Check cancellation between tools
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

                    title = _tool_title(name, args)
                    kind = _get_tool_kind(name)

                    # Notify: tool start
                    await self._conn.session_update(
                        session_id,
                        start_tool_call(
                            tool_call_id=tc_id,
                            title=title,
                            kind=cast(
                                Literal[
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
                                ],
                                kind,
                            ),
                            status="in_progress",
                            raw_input=args,
                        ),
                    )

                    # Execute tool (in thread to avoid blocking)
                    try:
                        output = await asyncio.to_thread(execute_tool, name, args)
                    except Exception as e:
                        output = f"Error: {e}"

                    # Truncate output for display
                    display_output = (
                        output[:2000] + "..." if len(output) > 2000 else output
                    )

                    # Notify: tool completed
                    await self._conn.session_update(
                        session_id,
                        update_tool_call(
                            tool_call_id=tc_id,
                            status="completed",
                            content=[tool_content(text_block(display_output))],
                            raw_output=output[:5000],
                        ),
                    )

                    # Add tool result to message history
                    sess_msgs.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc_id,
                            "content": output,
                        }
                    )
        finally:
            # Restore original global model config
            _ask.DEEPSEEK_API_URL = orig_url
            _ask.DEEPSEEK_API_KEY = orig_key
            _ask.DEEPSEEK_MODEL = orig_model

    # ── Cancellation ────────────────────────────────────────────────

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        import ask as _ask

        logger.info("ACP cancel: session=%s", session_id)
        self._cancelled.add(session_id)
        _ask._interrupted = True

    # ── Extensions (unused) ─────────────────────────────────────────

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        pass


async def run_acp_agent() -> None:
    """Entry point: run ask-agent as an ACP agent over stdio."""
    from acp import run_agent

    # Initialize ask-agent providers before starting ACP
    from ask import init_providers, init_command_manager

    init_providers()
    init_command_manager()

    logger.info("Starting ask-agent in ACP mode (protocol v%d)", PROTOCOL_VERSION)
    await run_agent(AskAgentACP(), use_unstable_protocol=True)

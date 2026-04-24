"""
Hook system for lifecycle event handling.

Hooks are extension points around the main agent loop.
They let users add behavior without rewriting the loop itself.

Exit-code contract for command hooks:
  - 0 -> continue
  - 1 -> block (PreToolUse only)
  - 2 -> inject a message into the conversation

Configuration is loaded from .hooks.json in the workspace root or ~/.ask-agent/.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class HookEvent(str, Enum):
    """Lifecycle events that hooks can attach to."""

    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    STOP = "Stop"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PERMISSION_REQUEST = "PermissionRequest"
    PERMISSION_DENIED = "PermissionDenied"
    TASK_CREATED = "TaskCreated"
    TASK_COMPLETED = "TaskCompleted"
    CONFIG_CHANGE = "ConfigChange"
    CWD_CHANGED = "CwdChanged"
    FILE_CHANGED = "FileChanged"
    NOTIFICATION = "Notification"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    TEAMMATE_IDLE = "TeammateIdle"


HOOK_EVENTS = list(HookEvent)
HOOK_TIMEOUT = 30  # seconds


@dataclass
class HookInput:
    """Input context passed to a hook."""

    event: HookEvent
    tool_name: str = ""
    tool_input: Dict[str, Any] = field(default_factory=dict)
    tool_output: str = ""
    tool_use_id: str = ""
    session_id: str = ""
    cwd: str = ""
    error: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HookOutput:
    """Result returned from a hook execution."""

    message: str = ""
    block: bool = False
    updated_input: Dict[str, Any] = field(default_factory=dict)
    permission_override: str = ""


@dataclass
class HookDefinition:
    """A single hook definition from configuration."""

    command: str = ""
    matcher: str = ""  # Regex pattern for tool names
    timeout: int = HOOK_TIMEOUT


HookConfig = Dict[str, List[Dict[str, Any]]]


class HookManager:
    """
    Load and execute hooks from .hooks.json configuration.

    The hook manager does three simple jobs:
    - load hook definitions from config file
    - run matching commands for an event
    - aggregate block / message results for the caller
    """

    def __init__(
        self,
        config_path: Optional[Path] = None,
        workdir: Optional[Path] = None,
    ):
        self.hooks: Dict[str, List[HookDefinition]] = {
            e.value: [] for e in HookEvent
        }
        self._workdir = workdir or Path.cwd()

        # Search for config: workdir -> ~/.ask-agent
        if config_path:
            self._load_config(config_path)
        else:
            paths = [
                self._workdir / ".hooks.json",
                Path.home() / ".ask-agent" / ".hooks.json",
            ]
            for p in paths:
                if p.exists():
                    self._load_config(p)
                    break

    def _load_config(self, config_path: Path) -> None:
        """Load hook definitions from a JSON config file."""
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            hooks_config = config.get("hooks", {})
            for event in HOOK_EVENTS:
                event_name = event.value
                for defn in hooks_config.get(event_name, []):
                    hook_def = HookDefinition(
                        command=defn.get("command", ""),
                        matcher=defn.get("matcher", ""),
                        timeout=defn.get("timeout", HOOK_TIMEOUT),
                    )
                    if hook_def.command:
                        self.hooks[event_name].append(hook_def)
            logger.info("Hooks loaded from %s", config_path)
        except Exception as e:
            logger.warning("Hook config error: %s", e)

    def register(self, event: HookEvent, definition: HookDefinition) -> None:
        """Register a hook for an event."""
        if event.value not in self.hooks:
            self.hooks[event.value] = []
        self.hooks[event.value].append(definition)

    def register_from_config(self, config: HookConfig) -> None:
        """Register hooks from configuration dict."""
        for event_name, definitions in config.items():
            try:
                event = HookEvent(event_name)
            except ValueError:
                continue
            for defn in definitions:
                hook_def = HookDefinition(
                    command=defn.get("command", ""),
                    matcher=defn.get("matcher", ""),
                    timeout=defn.get("timeout", HOOK_TIMEOUT),
                )
                if hook_def.command:
                    self.register(event, hook_def)

    def run_hooks(self, event: HookEvent, context: Optional[HookInput] = None) -> HookOutput:
        """
        Execute all hooks for an event.

        Args:
            event: The lifecycle event to fire hooks for.
            context: Contextual information about the event.

        Returns:
            Aggregated HookOutput with block status and messages.
        """
        result = HookOutput()
        hooks = self.hooks.get(event.value, [])

        for hook_def in hooks:
            # Check matcher (regex pattern for tool names)
            if hook_def.matcher and context:
                tool_name = context.tool_name
                if not re.match(hook_def.matcher, tool_name):
                    continue

            command = hook_def.command
            if not command:
                continue

            # Build environment with hook context
            env = dict(os.environ)
            if context:
                env["HOOK_EVENT"] = event.value
                env["HOOK_TOOL_NAME"] = context.tool_name
                env["HOOK_TOOL_INPUT"] = json.dumps(
                    context.tool_input, ensure_ascii=False
                )[:10000]
                if context.tool_output:
                    env["HOOK_TOOL_OUTPUT"] = str(context.tool_output)[:10000]

            try:
                r = subprocess.run(
                    command,
                    shell=True,
                    cwd=self._workdir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=hook_def.timeout,
                )

                if r.returncode == 0:
                    # Continue
                    if r.stdout.strip():
                        logger.info("[hook:%s] %s", event.value, r.stdout.strip()[:100])

                    # Try parsing structured JSON output
                    try:
                        hook_output = json.loads(r.stdout)
                        if isinstance(hook_output, dict):
                            if "updatedInput" in hook_output and context:
                                result.updated_input = hook_output["updatedInput"]
                            if "message" in hook_output:
                                result.message += hook_output["message"]
                            if "permissionDecision" in hook_output:
                                result.permission_override = hook_output[
                                    "permissionDecision"
                                ]
                    except (json.JSONDecodeError, TypeError):
                        pass  # stdout was not JSON -- normal for simple hooks

                elif r.returncode == 1:
                    # Block execution
                    result.block = True
                    reason = r.stderr.strip() or "Blocked by hook"
                    logger.warning("[hook:%s] BLOCKED: %s", event.value, reason[:200])

                elif r.returncode == 2:
                    # Inject message
                    msg = r.stderr.strip()
                    if msg:
                        if result.message:
                            result.message += "\n"
                        result.message += msg
                        logger.info("[hook:%s] INJECT: %s", event.value, msg[:200])

            except subprocess.TimeoutExpired:
                logger.warning("[hook:%s] Timeout (%ds)", event.value, hook_def.timeout)
            except Exception as e:
                logger.warning("[hook:%s] Error: %s", event.value, e)

        return result

    def clear(self) -> None:
        """Clear all registered hooks."""
        self.hooks = {e.value: [] for e in HookEvent}

"""
Permission system for tool execution.

Every tool call passes through a permission pipeline before execution.

Pipeline order:
  1. bash security validation (bash tool only)
  2. deny rules (bypass-immune, always checked first)
  3. mode-based decisions
  4. allow rules
  5. ask user (default for unmatched)

Key insight: "Safety is a pipeline, not a boolean."

Modes (from strict to permissive):
  - plan: deny all write operations, allow reads only
  - default: ask user for unmatched tools
  - auto: auto-allow reads, ask for writes
  - acceptEdits: auto-allow file edits, ask for bash writes
  - dontAsk: auto-allow all operations (still enforce deny rules)
  - bypassPermissions: skip all permission checks entirely

Configuration is loaded from .permissions.json in the workspace root or ~/.ask-agent/.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─── Types ───


class PermissionMode(str, Enum):
    """Permission modes controlling how tool calls are authorized.

    Ordered from most restrictive to most permissive:
      plan < default < auto < acceptEdits < dontAsk < bypassPermissions
    """

    PLAN = "plan"
    DEFAULT = "default"
    AUTO = "auto"
    ACCEPT_EDITS = "acceptEdits"
    DONT_ASK = "dontAsk"
    BYPASS_PERMISSIONS = "bypassPermissions"


class PermissionBehavior(str, Enum):
    """Possible outcomes of a permission check."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    PASSTHROUGH = "passthrough"


@dataclass
class PermissionRule:
    """A single permission rule.

    Attributes:
        tool: Tool name to match, or '*' for all tools.
        path: Glob pattern for file path matching, or '*' for any path.
        content: Glob pattern for command content matching (bash tool).
        behavior: allow, deny, or ask.
    """

    tool: str = "*"
    path: str = ""
    content: str = ""
    behavior: str = "ask"


@dataclass
class PermissionResult:
    """Result of a permission check.

    Attributes:
        behavior: The decided behavior (allow/deny/ask).
        reason: Human-readable explanation of the decision.
    """

    behavior: PermissionBehavior = PermissionBehavior.ASK
    reason: str = ""


# ─── Constants ───

MODES = tuple(m.value for m in PermissionMode)

# Tools that only read state
READ_ONLY_TOOLS = {"read_file", "glob", "grep", "webfetch", "Skill", "MCP", "TodoWrite"}

# Tools that modify state
WRITE_TOOLS = {"write_file", "edit_file", "bash"}

# Tools that modify files but not shell state (subset of WRITE_TOOLS)
EDIT_TOOLS = {"write_file", "edit_file"}

# Bash command categories
BASH_SEARCH_COMMANDS = {"find", "grep", "rg", "ag", "ack", "locate", "which", "whereis"}
BASH_READ_COMMANDS = {
    "cat", "head", "tail", "less", "more", "wc", "stat",
    "file", "strings", "jq", "awk", "cut", "sort", "uniq", "tr",
}
BASH_LIST_COMMANDS = {"ls", "tree", "du"}
BASH_SAFE_COMMANDS = {
    "echo", "printf", "pwd", "whoami", "hostname", "uname",
    "date", "uptime", "id", "env", "printenv", "type", "command",
    "test", "true", "false", "dirname", "basename", "realpath",
    "readlink", "md5sum", "sha256sum", "xxd", "od", "hexdump",
    "git",  # git read operations (status, log, diff, branch, etc.)
}

DEFAULT_RULES: List[PermissionRule] = [
    # Always deny dangerous patterns
    PermissionRule(tool="bash", content="rm -rf /", behavior="deny"),
    PermissionRule(tool="bash", content="sudo *", behavior="deny"),
    # Allow reading anything
    PermissionRule(tool="read_file", path="*", behavior="allow"),
    PermissionRule(tool="glob", behavior="allow"),
    PermissionRule(tool="grep", behavior="allow"),
]


# ─── Bash Security Validation ───


class BashSecurityValidator:
    """
    Validate bash commands for obviously dangerous patterns.

    The validator catches high-risk patterns early, before the permission
    pipeline even considers rules. Severe patterns (sudo, rm -rf) get
    immediate deny; milder flags escalate to ask.
    """

    VALIDATORS: List[tuple] = [
        ("sudo", r"\bsudo\b"),
        ("rm_rf", r"\brm\s+(-[a-zA-Z]*)?r"),
        ("shell_metachar", r"[;&|`$]"),
        ("cmd_substitution", r"\$\("),
        ("ifs_injection", r"\bIFS\s*="),
    ]

    SEVERE_PATTERNS = {"sudo", "rm_rf"}

    def validate(self, command: str) -> List[tuple]:
        """
        Check a bash command against all validators.

        Returns:
            List of (validator_name, matched_pattern) tuples for failures.
            Empty list means the command passed all validators.
        """
        failures = []
        for name, pattern in self.VALIDATORS:
            if re.search(pattern, command):
                failures.append((name, pattern))
        return failures

    def is_safe(self, command: str) -> bool:
        """Returns True only if no validators triggered."""
        return len(self.validate(command)) == 0

    def has_severe(self, command: str) -> bool:
        """Returns True if any severe pattern was triggered."""
        failures = self.validate(command)
        return any(f[0] in self.SEVERE_PATTERNS for f in failures)

    def describe_failures(self, command: str) -> str:
        """Human-readable summary of validation failures."""
        failures = self.validate(command)
        if not failures:
            return "No issues detected"
        parts = [f"{name} (pattern: {pattern})" for name, pattern in failures]
        return "Security flags: " + ", ".join(parts)


# ─── Read-Only Detection ───


def _split_command(command: str) -> List[str]:
    """Split a compound shell command into individual parts."""
    return [
        p.strip()
        for p in re.split(r"\s*(?:&&|\|\||;|\|)\s*", command)
        if p.strip()
    ]


def is_read_only_bash(command: str) -> bool:
    """Check if a bash command is read-only (search/read/list/safe only).

    Args:
        command: The shell command to check.

    Returns:
        True if the command appears to be read-only.
    """
    parts = _split_command(command)
    all_safe = True
    for part in parts:
        base = part.split()[0] if part.split() else ""
        if base in BASH_SEARCH_COMMANDS | BASH_READ_COMMANDS | BASH_LIST_COMMANDS | BASH_SAFE_COMMANDS:
            return True
        all_safe = False
    return all_safe


# ─── Workspace Trust ───


def is_workspace_trusted(workspace: Path | None = None) -> bool:
    """Check if a workspace has been explicitly marked as trusted.

    Args:
        workspace: Path to the workspace directory.

    Returns:
        True if the workspace has a trust marker file.
    """
    ws = workspace or Path.cwd()
    trust_marker = ws / ".ask-agent" / ".trusted"
    return trust_marker.exists()


# ─── Permission Manager ───


class PermissionManager:
    """
    Manages permission decisions for tool calls.

    Pipeline: bash_validator -> deny_rules -> mode_check -> allow_rules -> ask_user

    The decision path is deliberately short so readers can implement it
    themselves before adding more advanced policy layers.
    """

    def __init__(
        self,
        mode: str = "acceptEdits",
        rules: List[PermissionRule] | None = None,
        workdir: Path | None = None,
    ):
        if mode not in MODES:
            raise ValueError(f"Unknown mode: {mode}. Choose from {MODES}")
        self.mode = mode
        self.rules: List[PermissionRule] = rules or list(DEFAULT_RULES)
        self._workdir = workdir or Path.cwd()
        self._bash_validator = BashSecurityValidator()
        self.consecutive_denials = 0
        self.max_consecutive_denials = 3

    # ── Configuration ──

    @classmethod
    def from_config(cls, workdir: Path | None = None) -> "PermissionManager":
        """Load permission configuration from JSON files.

        Searches in order: workdir/.permissions.json -> ~/.ask-agent/.permissions.json

        Args:
            workdir: The workspace directory.

        Returns:
            A PermissionManager initialized from config, or defaults.
        """
        ws = workdir or Path.cwd()
        search_paths = [
            ws / ".permissions.json",
            Path.home() / ".ask-agent" / ".permissions.json",
        ]

        for config_path in search_paths:
            if config_path.exists():
                try:
                    data = json.loads(config_path.read_text(encoding="utf-8"))
                    mode = data.get("mode", "default")
                    raw_rules = data.get("rules", [])
                    rules = [
                        PermissionRule(
                            tool=r.get("tool", "*"),
                            path=r.get("path", ""),
                            content=r.get("content", ""),
                            behavior=r.get("behavior", "ask"),
                        )
                        for r in raw_rules
                    ]
                    logger.info("Permissions loaded from %s", config_path)
                    return cls(mode=mode, rules=rules, workdir=ws)
                except Exception as e:
                    logger.warning("Permission config error at %s: %s", config_path, e)

        return cls(workdir=ws)

    def save_config(self) -> None:
        """Save current permission rules to the workspace config file."""
        config_path = self._workdir / ".permissions.json"
        data = {
            "mode": self.mode,
            "rules": [
                {
                    "tool": r.tool,
                    "path": r.path,
                    "content": r.content,
                    "behavior": r.behavior,
                }
                for r in self.rules
            ],
        }
        config_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("Permissions saved to %s", config_path)

    # ── Pipeline ──

    def check(self, tool_name: str, tool_input: dict) -> PermissionResult:
        """
        Run the full permission pipeline for a tool call.

        Args:
            tool_name: Name of the tool being called.
            tool_input: Input arguments for the tool.

        Returns:
            PermissionResult with the decided behavior and reason.
        """
        # Step 0: Bash security validation
        #   - Severe patterns (sudo, rm -rf) → always DENY, no bypass
        #   - Non-severe patterns (&&, |, $(), etc.) → log warning and fall through
        #     to mode check; mode decides allow/ask, not security validator
        bash_security_notes: str = ""
        if tool_name == "bash":
            command = tool_input.get("command", "")
            failures = self._bash_validator.validate(command)
            if failures:
                if self._bash_validator.has_severe(command):
                    desc = self._bash_validator.describe_failures(command)
                    return PermissionResult(
                        behavior=PermissionBehavior.DENY,
                        reason=f"Bash security: {desc}",
                    )
                # Non-severe: just note it, let mode check decide
                desc = self._bash_validator.describe_failures(command)
                logger.warning("Bash security flagged: %s", desc)
                bash_security_notes = f" [security note: {desc}]"

        # Step 1: Deny rules (bypass-immune, checked first always)
        for rule in self.rules:
            if rule.behavior != "deny":
                continue
            if self._matches(rule, tool_name, tool_input):
                return PermissionResult(
                    behavior=PermissionBehavior.DENY,
                    reason=f"Blocked by deny rule: tool={rule.tool}"
                    + (f", content={rule.content}" if rule.content else "")
                    + (f", path={rule.path}" if rule.path else ""),
                )

        # Step 2: Mode-based decisions
        mode_result = self._check_mode(tool_name, tool_input)
        if mode_result is not None:
            if bash_security_notes:
                mode_result.reason += bash_security_notes
            return mode_result

        # Step 3: Allow rules
        for rule in self.rules:
            if rule.behavior != "allow":
                continue
            if self._matches(rule, tool_name, tool_input):
                self.consecutive_denials = 0
                return PermissionResult(
                    behavior=PermissionBehavior.ALLOW,
                    reason=f"Matched allow rule: tool={rule.tool}"
                    + (f", path={rule.path}" if rule.path else "")
                    + (f", content={rule.content}" if rule.content else ""),
                )

        # Step 4: Ask user (default for unmatched)
        return PermissionResult(
            behavior=PermissionBehavior.ASK,
            reason=f"No rule matched for {tool_name}",
        )

    def ask_user(self, tool_name: str, tool_input: dict) -> bool:
        """Interactive approval prompt.

        Options:
            y      - 允许本次
            n      - 拒绝本次
            always - 永久允许该工具（添加 allow 规则）
            never  - 永久拒绝该工具（添加 deny 规则）
            auto   - 切换到 auto 模式（读操作自动放行，写操作仍询问）
            dontAsk- 切换到 dontAsk 模式（全部自动放行）
            v      - 查看工具调用的完整参数

        Args:
            tool_name: Name of the tool being called.
            tool_input: Input arguments for the tool.

        Returns:
            True if the user approved the action.
        """
        preview = json.dumps(tool_input, ensure_ascii=False)[:200]
        print(f"\n  [Permission] {tool_name}: {preview}")

        while True:
            try:
                answer = input(
                    "  Allow? (y/n/always/never/auto/dontAsk/v): "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False

            # 查看详情
            if answer in ("v", "view", "details"):
                full_input = json.dumps(tool_input, ensure_ascii=False, indent=2)
                print(f"  --- Full args ---\n{full_input}\n  ---")
                continue

            # 永久拒绝：添加 deny 规则
            if answer == "never":
                self.rules.append(
                    PermissionRule(tool=tool_name, path="*", behavior="deny")
                )
                self.consecutive_denials += 1
                print(f"  ✋ 已添加拒绝规则: {tool_name} (用 /perm -l 查看)")
                return False

            # 永久允许：添加 allow 规则
            if answer == "always":
                self.rules.append(
                    PermissionRule(tool=tool_name, path="*", behavior="allow")
                )
                self.consecutive_denials = 0
                print(f"  ✅ 已添加允许规则: {tool_name} (用 /perm -l 查看)")
                return True

            # 切换模式
            if answer == "auto":
                self.set_mode(PermissionMode.AUTO.value)
                print(f"  🔄 已切换到 auto 模式（读操作自动放行，写操作仍询问）")
                self.consecutive_denials = 0
                return True

            if answer in ("dontask", "dontAsk"):
                self.set_mode(PermissionMode.DONT_ASK.value)
                print(f"  🔄 已切换到 dontAsk 模式（全部自动放行）")
                self.consecutive_denials = 0
                return True

            # 允许本次
            if answer in ("y", "yes"):
                self.consecutive_denials = 0
                return True

            # 拒绝本次
            if answer in ("n", "no"):
                self.consecutive_denials += 1
                if self.consecutive_denials >= self.max_consecutive_denials:
                    print(
                        f"  [{self.consecutive_denials} consecutive denials -- "
                        "consider /perm plan or /perm auto]"
                    )
                return False

            # 未知输入
            print("  ⚠️ 无效输入，可选: y / n / always / never / auto / dontAsk / v")

    # ── Mode Logic ──

    def _check_mode(
        self, tool_name: str, tool_input: dict
    ) -> PermissionResult | None:
        """Check mode-based permission decisions.

        Returns:
            PermissionResult if the mode decides, None to fall through.
        """
        # ── bypassPermissions: skip all checks (deny rules already applied) ──
        if self.mode == PermissionMode.BYPASS_PERMISSIONS.value:
            return PermissionResult(
                behavior=PermissionBehavior.ALLOW,
                reason="Bypass mode: all operations allowed",
            )

        # ── plan: deny all writes, allow reads only ──
        if self.mode == PermissionMode.PLAN.value:
            if tool_name in WRITE_TOOLS:
                return PermissionResult(
                    behavior=PermissionBehavior.DENY,
                    reason="Plan mode: write operations are blocked",
                )
            if tool_name in READ_ONLY_TOOLS:
                return PermissionResult(
                    behavior=PermissionBehavior.ALLOW,
                    reason="Plan mode: read-only allowed",
                )
            if tool_name == "bash" and is_read_only_bash(
                tool_input.get("command", "")
            ):
                return PermissionResult(
                    behavior=PermissionBehavior.ALLOW,
                    reason="Plan mode: read-only bash allowed",
                )
            return PermissionResult(
                behavior=PermissionBehavior.DENY,
                reason="Plan mode: only read operations allowed",
            )

        # ── auto: auto-allow reads, fall through for writes ──
        if self.mode == PermissionMode.AUTO.value:
            if tool_name in READ_ONLY_TOOLS:
                return PermissionResult(
                    behavior=PermissionBehavior.ALLOW,
                    reason="Auto mode: read-only tool auto-approved",
                )
            if tool_name == "bash" and is_read_only_bash(
                tool_input.get("command", "")
            ):
                return PermissionResult(
                    behavior=PermissionBehavior.ALLOW,
                    reason="Auto mode: read-only bash auto-approved",
                )
            # Write tools fall through to allow rules, then ask
            return None

        # ── acceptEdits: auto-allow file edits, ask for bash ──
        if self.mode == PermissionMode.ACCEPT_EDITS.value:
            if tool_name in READ_ONLY_TOOLS:
                return PermissionResult(
                    behavior=PermissionBehavior.ALLOW,
                    reason="AcceptEdits mode: read-only tool auto-approved",
                )
            if tool_name in EDIT_TOOLS:
                return PermissionResult(
                    behavior=PermissionBehavior.ALLOW,
                    reason="AcceptEdits mode: file edits auto-approved",
                )
            if tool_name == "bash" and is_read_only_bash(
                tool_input.get("command", "")
            ):
                return PermissionResult(
                    behavior=PermissionBehavior.ALLOW,
                    reason="AcceptEdits mode: read-only bash auto-approved",
                )
            # bash writes fall through to allow rules, then ask
            return None

        # ── dontAsk: auto-allow everything (deny rules still enforced) ──
        if self.mode == PermissionMode.DONT_ASK.value:
            return PermissionResult(
                behavior=PermissionBehavior.ALLOW,
                reason="DontAsk mode: all operations auto-approved",
            )

        # ── default: fall through to allow rules ──
        return None

    # ── Rule Matching ──

    def _matches(
        self, rule: PermissionRule, tool_name: str, tool_input: dict
    ) -> bool:
        """Check if a rule matches the tool call.

        Args:
            rule: The permission rule to check.
            tool_name: Name of the tool being called.
            tool_input: Input arguments for the tool.

        Returns:
            True if the rule matches.
        """
        # Tool name match
        if rule.tool and rule.tool != "*":
            if rule.tool != tool_name:
                return False

        # Path pattern match
        if rule.path and rule.path != "*":
            path = tool_input.get("path", "")
            if not fnmatch(path, rule.path):
                return False

        # Content pattern match (for bash commands)
        if rule.content:
            command = tool_input.get("command", "")
            if not fnmatch(command, rule.content):
                return False

        return True

    # ── Utilities ──

    def add_rule(
        self,
        tool: str = "*",
        path: str = "",
        content: str = "",
        behavior: str = "allow",
    ) -> None:
        """Add a permission rule.

        Args:
            tool: Tool name or '*' for all.
            path: Path glob pattern.
            content: Content glob pattern (for bash).
            behavior: allow, deny, or ask.
        """
        self.rules.append(
            PermissionRule(tool=tool, path=path, content=content, behavior=behavior)
        )

    def list_rules(self) -> List[Dict[str, str]]:
        """Return all rules as a list of dicts."""
        return [
            {
                "tool": r.tool,
                "path": r.path,
                "content": r.content,
                "behavior": r.behavior,
            }
            for r in self.rules
        ]

    def set_mode(self, mode: str) -> bool:
        """Switch permission mode at runtime.

        Args:
            mode: One of 'plan', 'default', 'auto', 'acceptEdits',
                  'dontAsk', 'bypassPermissions'.

        Returns:
            True if the mode was successfully set.
        """
        if mode not in MODES:
            return False
        self.mode = mode
        logger.info("Permission mode set to: %s", mode)
        return True

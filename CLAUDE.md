# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**ask-agent** (command: `ag`) is a Python CLI tool for interacting with AI providers (OpenAI, DeepSeek, etc.) via streaming chat. It supports multiple interaction modes: Q&A, translation, agent (with tool use), and role-play. Integrates MCP (Model Context Protocol) for external tools and ACP (Agent Client Protocol) for IDE integration (Zed, JetBrains, VS Code).

## Build & Development Commands

```bash
# Run the application
make run           # Run ask.py in agent mode (ERROR log level)
make info          # Run with INFO log level
make debug         # Run with DEBUG log level

# Linting
make lint          # Type check with pyright

# Testing (run from project root)
pytest tests/ -v                                          # All tests
pytest tests/test_merge_arguments.py -v                   # Single test file
pytest tests/test_merge_arguments.py::test_merge_arguments_empty -v  # Single test
pytest tests/ -v -k "merge"                               # Tests matching pattern

# MCP server for testing
make mcpserver     # Run HTTP MCP server

# Utilities
make pipreqs       # Regenerate requirements.txt
make clean         # Clear cache directory
```

## Architecture

### Core Modules

| Module | Responsibility |
|--------|---------------|
| `ask.py` | Main entry point. Contains CLI parsing, mode switching, system prompt building, agent loop, tool execution, and Telegram bot integration. |
| `acp_agent.py` | ACP (Agent Client Protocol) implementation wrapping the agent loop for IDE integration. |
| `provider.py` | Multi-provider config parsing. `ProviderConfig` loads `providers.json`, resolves API keys (supports `env:VAR_NAME`), manages model switching. |
| `mcp.py` | MCP client implementation. `StdioClient` (subprocess) and `HttpClient` (HTTP) for connecting to MCP servers. `MCPManager` coordinates multiple servers. |
| `MCPConfig.py` | MCP server configuration dataclasses. Parses `mcp.json`, normalizes server types (`stdio`/`http`/`streamablehttp`). |
| `session.py` | Session persistence. Saves/loads conversation history to `~/.ask-agent/cache/{mode}/` as JSON files. |
| `memory.py` | Cross-session memory system. Stores memories in `.memory/` (project) or `~/.ask-agent/memory/` (global). Memories are YAML-frontmatter Markdown files indexed by `MEMORY.md`. |
| `role.py` | Role-play mode manager. Discovers roles from `roles/` directories, loads role prompts from `.md` files. |
| `agent.py` | Agent manager. Discovers agents from `agents/` directories, loads agent prompts from `.md` files. |
| `command.py` | Custom command manager. Supports both Markdown files (`command/*.md`) and JSON config (`command.json`). |
| `config.py` | `ConfigPathManager` - resolves config files with priority: project dir → `~/.ask-agent/`. |

### Utility Modules (`util/`)

| Module | Responsibility |
|--------|---------------|
| `util/util.py` | ANSI color constants (One Dark Pro theme), `format_range_info`, `read_file`/`write_file` helpers. |
| `util/text_diff.py` | Text diff formatting for file edit operations. |
| `util/hooks.py` | Hook system for extending agent behavior (`HookManager`, `HookEvent`). |
| `util/background.py` | Background task manager for concurrent operations. |

### Key Design Patterns

**SystemPromptBuilder** (`ask.py:863`): Pipeline-based prompt assembly with sections: core → tools_metadata → memory → guidance → DYNAMIC_BOUNDARY → dynamic. Each section has its own `_build_*()` method.

**Subagent Types** (`ask.py:86`): Registry of subagent types (`explore`, `code`, `plan`) with defined tool access and prompts. The agent loop can spawn subagents for focused tasks.

**Config Resolution**: Most configs use `ConfigPathManager` which checks project directory first, then `~/.ask-agent/`. Exceptions: `config.json` and `roles.json` always use `~/.ask-agent/`.

**Mode System**: Four modes (`ASK=0`, `TRANSLATE=1`, `AGENT=2`, `ROLE=3`) with different system prompts and behaviors. Mode switching happens via `/ask`, `/e`, `/agent`, `/role` commands.

### Entry Points

- `./ag` or `python ask.py` - Main CLI
- `python ask.py --agent` - Start in agent mode
- `python ask.py --acp` - Start as ACP agent for IDE integration
- `python ask.py --translate "word"` - Translation mode
- `python ./server/http_server.py` - HTTP MCP test server

### Configuration Files

All stored in `~/.ask-agent/` (global) or project root (local):

| File | Purpose | Priority |
|------|---------|----------|
| `providers.json` | AI provider configs (API keys, models, endpoints) | Global → Project |
| `mcp.json` | MCP server configurations | Project → Global |
| `agents.json` | Agent metadata (names, descriptions) | Project → Global |
| `command.json` | Custom command definitions | Project → Global |
| `config.json` | Global state (current mode, role) | Always `~/.ask-agent/` |
| `roles.json` | Role metadata | Always `~/.ask-agent/` |

Session cache: `~/.ask-agent/cache/{ask,agent,translate,role}/`

### External Dependencies

- `agent-client-protocol` - ACP SDK for IDE integration
- `prompt-toolkit` - Interactive CLI input with history
- `python-telegram-bot` - Telegram bot integration
- `python-dotenv` - `.env` file loading
- `requests` - HTTP client for API calls

## Code Conventions

- **Type hints**: Use Python 3.10+ syntax (`str | None`, not `Optional[str]`)
- **Dataclasses**: Use `@dataclass` with `from_dict` classmethods for config parsing
- **Logging**: Module-level `logger = logging.getLogger(__name__)`
- **File I/O**: Always specify `encoding='utf-8'`, use `Path` objects
- **JSON**: Use `ensure_ascii=False` for Unicode, `indent=2` for readability
- **Error handling**: Custom exception hierarchies with `raise ... from e`
- **Test docstrings**: Chinese (existing convention)

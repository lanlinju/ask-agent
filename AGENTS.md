# AGENTS.md

## Project Overview

**ask-agent** is a Python CLI tool for interacting with AI providers (OpenAI, DeepSeek, etc.) via streaming chat. Supports MCP (Model Context Protocol) tool integration, multiple agents/roles, Telegram bot mode, and session management.

## Project Structure

```
ask.py          - Main entry point (CLI, Telegram bot)
agent.py        - Agent management (discover/load agent configs)
provider.py     - Provider config parsing (ProviderOptions, ModelInfo)
mcp.py          - MCP client (StdioClient, HttpClient, tool discovery)
session.py      - Session persistence (save/load conversations)
role.py         - Role/prompt management
command.py      - Command execution manager
config.py       - Config path resolution (current dir vs ~/.ask-agent)
MCPConfig.py    - MCP server configuration dataclasses
util.py         - Utilities (terminal colors, helpers)
server/         - HTTP MCP server implementation
agents/         - Agent prompt files (*.md)
roles/          - Role prompt files
skills/         - Skill prompt files
command/        - Command definitions
tests/          - pytest test suite
```

## Build, Lint, Test Commands

```bash
# Run the application
make run           # Run ask.py in agent mode (ERROR log level)
make info          # Run with INFO log level
make debug         # Run with DEBUG log level

# Linting
make lint          # Type check with pyright

# Testing (no make target; run pytest directly)
pytest tests/ -v                      # Run all tests
pytest tests/test_merge_arguments.py -v                          # Run single test file
pytest tests/test_merge_arguments.py::test_merge_arguments_empty -v  # Run single test
pytest tests/ -v -k "merge"                                      # Run tests matching pattern

# MCP server
make mcpserver     # Run HTTP MCP server for testing

# Utilities
make pipreqs       # Regenerate requirements.txt
make clean         # Clear cache directory
```

## Testing Conventions

- Tests live in `tests/`, use `pytest` (no extra config file; run from project root).
- Test files: `test_<module>.py`, test functions: `test_<description>`.
- Tests import functions directly: `from ask import merge_arguments`.
- Docstrings on tests are in Chinese (existing convention).
- No fixtures or conftest.py currently; tests are self-contained.

## Code Style Guidelines

### Imports
Group imports: stdlib → third-party → local modules. Prefer specific imports over `import *`.
```python
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any
from mcp import MCPManager
from provider import ProviderConfig
```

### Type Annotations
Use type hints for all function parameters and return values. Use modern Python 3.10+ union syntax: `str | None` (not `Optional[str]`). Import from `typing`: `List`, `Dict`, `Optional`, `Any`, `Tuple`, `Union`, `Generator`. Annotate module-level globals: `messages: List[Dict[str, str | List]] = []`

### Naming Conventions
- **Classes**: PascalCase (`SessionManager`, `StdioClient`, `MCPError`)
- **Functions/variables**: snake_case (`get_streaming_response`, `current_messages`)
- **Constants**: UPPER_SNAKE_CASE (`DEEPSEEK_API_KEY`, `SYSTEM_PROMPT_ASK`)
- **Private methods**: underscore prefix (`_send_request`, `_normalize_type`)
- **Module-level logger**: `logger = logging.getLogger(__name__)`

### Error Handling
Create custom exception hierarchies with base exceptions. Use `raise ... from e` to preserve exception chains. Return boolean/None for failures, log errors appropriately.
```python
class MCPError(Exception): pass
class ConnectionError(MCPError): pass

try:
    # ...
except Exception as e:
    raise ConnectionError(f"Connection failed: {e}") from e
```

### Docstrings
Use Google-style or simple docstrings. Document parameters (`Args:`), returns (`Returns:`), and raises (`Raises:`).
```python
def save_session(messages: List[Dict]) -> Optional[Tuple[str, Path]]:
    """Save session to file.

    Args:
        messages: List of messages to save

    Returns:
        (session_id, file_path), None if messages only contain system prompt
    """
```

### Dataclasses
Use `@dataclass` for configuration and data structures. Provide `from_dict` classmethods for parsing from dictionaries.
```python
@dataclass
class ProviderOptions:
    base_url: str
    api_key: str

    @classmethod
    def from_dict(cls, options: Dict[str, Any]) -> "ProviderOptions":
        return cls(...)
```

### Context Managers
Implement `__enter__` and `__exit__` for resource management.

### File I/O
Always specify `encoding='utf-8'` when opening files. Use `Path` objects from pathlib.
```python
from pathlib import Path
path = Path("config.json")
with open(path, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
```

### Logging
Create module-level logger: `logger = logging.getLogger(__name__)`. Use appropriate levels and include relevant context.

### HTTP Requests
Use `requests` library. Set timeouts. Handle `requests.RequestException`.
```python
with requests.post(url, json=data, headers=headers, timeout=30) as response:
    response.raise_for_status()
```

### JSON
Use `ensure_ascii=False` for Unicode handling. Use `indent=2` for readable JSON. Handle `json.JSONDecodeError`.

### Subprocess
Use `subprocess.Popen` for long-running processes. Set `stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE`.

### Configuration
Use JSON files for configuration (`providers.json`, `mcp.json`). Provide `create_sample_config()` functions. Validate on load.

### Environment Variables
Use `python-dotenv` for loading `.env` files. Call `load_dotenv(override=True)` at module level. Use `os.getenv()` with defaults.

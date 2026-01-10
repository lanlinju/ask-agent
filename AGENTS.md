# Agent Guidelines for ask-agent

This file guides AI agents working in this codebase.

## Build, Lint, Test Commands

```bash
# Run the application
make run           # Run ask.py in agent mode
make info           # Run with INFO log level
make debug          # Run with DEBUG log level

# Linting
make lint           # Type check with pyright

# Testing
cd tests && make test              # Run all tests
pytest tests/test_merge_arguments.py -v  # Run single test file
pytest tests/test_merge_arguments.py::test_merge_arguments_empty -v  # Run single test

# MCP server
make mcpserver     # Run HTTP MCP server for testing
```

## Code Style Guidelines

### Imports
- Group imports: stdlib → third-party → local modules
- Prefer specific imports over `import *`
- Example:
  ```python
  import json
  import logging
  from pathlib import Path
  from typing import List, Dict, Optional
  from mcp import MCPManager
  from provider import ProviderConfig
  ```

### Type Annotations
- Use type hints for function parameters and return values
- Import from `typing`: `List`, `Dict`, `Optional`, `Any`, `Tuple`, `Union`, `Generator`
- Modern Python 3.10+ union syntax: `str | None` (not `Optional[str]`)
- For module-level globals, use annotations: `messages: List[Dict[str, str | List]] = []`

### Naming Conventions
- **Classes**: PascalCase (`SessionManager`, `StdioClient`)
- **Functions/variables**: snake_case (`get_streaming_response`, `current_messages`)
- **Constants**: UPPER_SNAKE_CASE (`DEEPSEEK_API_KEY`, `SYSTEM_PROMPT_ASK`)
- **Private methods**: underscore prefix (`_send_request`, `_normalize_type`)
- **Module-level private**: underscore prefix (e.g., `_initialized` in classes)

### Error Handling
- Create custom exception hierarchies with base exceptions
- Use `raise ... from e` to preserve exception chains
- Return boolean/None for failures, log errors appropriately
- Example:
  ```python
  class MCPError(Exception):
      pass

  class ConnectionError(MCPError):
      pass

  try:
      # ...
  except Exception as e:
      raise ConnectionError(f"Connection failed: {e}") from e
  ```

### Docstrings
- Use Google-style or simple docstrings for classes and methods
- Document parameters (`Args:`), returns (`Returns:`), and raises (`Raises:`)
- Example:
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
- Use `@dataclass` for configuration and data structures
- Provide `from_dict` classmethods for parsing from dictionaries
- Example:
  ```python
  @dataclass
  class ServerConfig:
      name: str
      type: str
      enabled: bool = True

      @classmethod
      def from_dict(cls, config: Dict[str, Any]) -> "ServerConfig":
          return cls(
              name=config.get("name"),
              type=config.get("type"),
              enabled=config.get("enabled", True)
          )
  ```

### Context Managers
- Implement `__enter__` and `__exit__` for resource management
- Example:
  ```python
  class StdioClient:
      def __enter__(self):
          self.connect()
          return self

      def __exit__(self, exc_type, exc_val, exc_tb):
          self.close()
  ```

### File I/O
- Always specify `encoding='utf-8'` when opening files
- Use `Path` objects from pathlib for file paths
- Example:
  ```python
  from pathlib import Path
  path = Path("config.json")
  with open(path, 'w', encoding='utf-8') as f:
      json.dump(data, f, indent=2, ensure_ascii=False)
  ```

### Logging
- Create module-level logger: `logger = logging.getLogger(__name__)`
- Use appropriate levels: `logger.debug()`, `logger.info()`, `logger.warning()`, `logger.error()`
- Include relevant context in log messages

### Global State
- Module-level globals use `global` keyword in functions
- Common globals: `messages`, `current_mode`, `memory`, `model_prompt`

### Configuration
- Use JSON files for configuration (`providers.json`, `mcp.json`)
- Provide `create_sample_config()` functions for scaffolding
- Validate configuration on load, return warnings/errors

### HTTP Requests
- Use `requests` library for HTTP calls
- Set timeouts appropriately
- Handle `requests.RequestException`
- Example:
  ```python
  with requests.post(url, json=data, headers=headers, timeout=30) as response:
      response.raise_for_status()
  ```

### Subprocess
- Use `subprocess.Popen` for long-running processes
- Set `stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE`
- Provide `cwd` parameter for working directory control

### JSON
- Use `ensure_ascii=False` for proper Unicode handling
- Use `indent=2` for readable JSON files
- Handle `json.JSONDecodeError` when parsing

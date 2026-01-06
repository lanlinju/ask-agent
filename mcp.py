import json
import logging
import requests
from typing import Dict, List, Any, Generator, Optional, Union
from urllib.parse import urljoin
import uuid
import subprocess

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MCPError(Exception):
    """MCP protocol error"""
    pass


class ConnectionError(MCPError):
    """Connection error"""
    pass


class ToolCallError(MCPError):
    """Tool call error"""
    pass


def generate_request_id() -> str:
    """Generate unique request ID"""
    return str(uuid.uuid4())


class StdioClient:
    """Stdio-based MCP client"""

    def __init__(self, command: List[str]):
        """
        Initialize stdio client

        Args:
            command: Command list to start the server, e.g., ["python3", "server.py"]
        """
        self.command = command
        self.process: Optional[subprocess.Popen] = None
        self._initialized = False

    def connect(self) -> Dict[str, Any]:
        """
        Start server process and initialize connection

        Returns:
            Initialization information returned by the server

        Raises:
            ConnectionError: Raised when connection fails
        """
        try:
            # Start subprocess
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=0
            )

            # Send initialization request
            init_params = {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "Python MCP Client",
                    "version": "1.0.0"
                }
            }

            response = self._send_request("initialize", init_params)
            self._initialized = True
            logger.info(f"Connection successful: {response.get('serverInfo')}")
            return response

        except Exception as e:
            self.close()
            raise ConnectionError(f"Connection failed: {e}") from e

    def _send_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Send JSON-RPC request

        Args:
            method: RPC method name
            params: Method parameters

        Returns:
            result field of server response

        Raises:
            MCPError: Raised when request fails
        """
        if not self.process or not self.process.stdin or not self.process.stdout:
            raise MCPError("Process not started or already closed")

        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": generate_request_id()
        }

        try:
            # Send request
            request_line = json.dumps(request) + "\n"
            logger.debug(f"Sending request: {request_line.strip()}")
            self.process.stdin.write(request_line)
            self.process.stdin.flush()

            # Read response
            response_line = self.process.stdout.readline()
            if not response_line.strip():
                stderr_output = self.process.stderr.read() if self.process.stderr else ""
                raise MCPError(f"Received empty response. Error output: {stderr_output}")

            logger.debug(f"Received response: {response_line.strip()}")
            response = json.loads(response_line.strip())

            # Handle error
            if "error" in response:
                error = response["error"]
                raise MCPError(
                    f"Server error [{error.get('code')}]: {error.get('message')}")

            return response.get("result", {})

        except json.JSONDecodeError as e:
            raise MCPError(f"JSON parsing failed: {e}") from e
        except IOError as e:
            raise MCPError(f"IO error: {e}") from e

    def list_tools(self) -> List[Dict[str, Any]]:
        """
        List all available tools

        Returns:
            List of tools
        """
        if not self._initialized:
            raise MCPError("Client not initialized, please call connect() first")

        result = self._send_request("tools/list")
        tools = result.get("tools", [])
        logger.info(f"Available tools: {[t['name'] for t in tools]}")
        return tools

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call specified tool

        Args:
            name: Tool name
            arguments: Tool parameters

        Returns:
            Tool execution result

        Raises:
            ToolCallError: Raised when tool call fails
        """
        if not self._initialized:
            raise MCPError("Client not initialized, please call connect() first")

        try:
            params = {
                "name": name,
                "arguments": arguments
            }
            result = self._send_request("tools/call", params)
            logger.info(f"Tool '{name}' call successful")
            return result
        except MCPError as e:
            raise ToolCallError(f"Tool '{name}' call failed: {e}") from e

    def close(self):
        """Close connection and cleanup resources"""
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=5)

    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()

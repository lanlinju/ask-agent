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
                    "name": "Ask Agent MCP Client",
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

class StreambleHttpClient:
    """HTTP-based MCP client (supports streaming responses)"""

    def __init__(self, server_url: str, timeout: int = 30):
        """
        Initialize HTTP client

        Args:
            server_url: Server URL
            timeout: Request timeout in seconds
        """
        self.server_url = server_url.rstrip('/')
        self.timeout = timeout
        self.session = requests.Session()
        self.session_id: Optional[str] = None
        self._initialized = False

    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()

    def connect(self) -> Dict[str, Any]:
        """
        Connect to server and initialize

        Returns:
            Initialization information returned by the server

        Raises:
            ConnectionError: Raised when connection fails
        """
        try:
            init_params = {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "Ask Agent Streamable HTTP MCP Client",
                    "version": "1.0.0"
                }
            }

            # Get session_id during initialization
            results = list(self._send_request("initialize", init_params))
            if not results:
                raise ConnectionError("Initialization failed: No response received")

            result = results[0]
            self._initialized = True
            logger.info(f"Connection successful. Session ID: {self.session_id}, Server: {result.get('serverInfo')}")
            return result

        except Exception as e:
            raise ConnectionError(f"Connection failed: {e}") from e

    def _prepare_headers(self) -> Dict[str, str]:
        """Prepare request headers"""
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json"
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        return headers

    def _send_request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        stream: bool = False
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Send HTTP request (supports streaming responses)

        Args:
            method: RPC method name
            params: Method parameters
            stream: Whether to enable streaming response

        Yields:
            Server response

        Raises:
            MCPError: Raised when request fails
        """
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": generate_request_id()
        }

        logger.debug(f"Sending request: {request}")
        headers = self._prepare_headers()
        is_initialization = (method == "initialize")

        try:
            with self.session.post(
                self.server_url,
                json=request,
                headers=headers,
                stream=stream,
                timeout=self.timeout
            ) as response:
                response.raise_for_status()

                # Save session_id during initialization
                if is_initialization and "mcp-session-id" in response.headers:
                    self.session_id = response.headers["mcp-session-id"]

                content_type = response.headers.get("content-type", "").lower()

                # Handle JSON response
                if "application/json" in content_type:
                    data = response.json()
                    logger.debug(f"Received JSON response: {data}")

                    if "error" in data:
                        error = data["error"]
                        raise MCPError(f"Server error [{error.get('code')}]: {error.get('message')}")

                    if "result" in data:
                        yield data["result"]

                # Handle SSE streaming response
                elif "text/event-stream" in content_type:
                    for line in response.iter_lines():
                        if line and line.startswith(b"data: "):
                            try:
                                event_data = json.loads(line[6:].decode("utf-8"))
                                logger.debug(f"Processing SSE stream response: {event_data}")

                                if "error" in event_data:
                                    error = event_data["error"]
                                    raise MCPError(f"Stream error [{error.get('code')}]: {error.get('message')}")

                                # Return result or notification
                                if "result" in event_data:
                                    yield event_data["result"]
                                elif "method" in event_data:
                                    yield event_data  # Notification event

                            except json.JSONDecodeError:
                                logger.warning(f"Failed to parse SSE data: {line}")
                                continue
                else:
                    logger.warning(f"Unknown content-type: {content_type}")

        except requests.RequestException as e:
            raise MCPError(f"HTTP request failed: {e}") from e

    def list_tools(self) -> List[Dict[str, Any]]:
        """
        List all available tools

        Returns:
            List of tools
        """
        if not self._initialized:
            raise MCPError("Client not initialized, please call connect() first")

        results = list(self._send_request("tools/list"))
        if not results:
            return []

        tools = results[0].get("tools", [])
        logger.info(f"Available tools: {[t['name'] for t in tools]}")
        return tools

    def call_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        stream: bool = False
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Call specified tool

        Args:
            name: Tool name
            arguments: Tool parameters
            stream: Whether to enable streaming response

        Yields:
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

            if stream:
                params["stream"] = True

            for result in self._send_request("tools/call", params, stream):
                yield result

            logger.info(f"Tool '{name}' call successful")

        except MCPError as e:
            raise ToolCallError(f"Tool '{name}' call failed: {e}") from e

    def close(self):
        """Close connection and cleanup resources"""
        self.session.close()
        self._initialized = False
        self.session_id = None
        logger.info("Connection closed")

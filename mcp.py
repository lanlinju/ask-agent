import json
import logging
import requests
from typing import Dict, List, Any, Generator, Optional, Union, Tuple
from urllib.parse import urljoin
import uuid
import subprocess
from pathlib import Path
from MCPConfig import MCPConfig, ServerConfig

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

    def __init__(
        self,
        command: List[str],
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[Union[str, Path]] = None
    ):
        """
        Initialize stdio client

        Args:
            command: Command list to start the server, e.g., ["python3", "server.py"]
            env: Additional environment variables dict (merged with current environment)
            cwd: Working directory path
        """
        self.command = command
        self.env = env
        self.cwd = Path(cwd) if cwd else None
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
            # Prepare environment variables
            import os
            process_env = None
            if self.env:
                process_env = os.environ.copy()
                process_env.update(self.env)
                logger.debug(f"Using custom environment variables: {self.env}")

            # Validate working directory
            if self.cwd:
                if not self.cwd.exists():
                    raise ConnectionError(f"Working directory does not exist: {self.cwd}")
                if not self.cwd.is_dir():
                    raise ConnectionError(f"Working directory is not a directory: {self.cwd}")
                logger.debug(f"Using working directory: {self.cwd}")

            # Start subprocess
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=0,
                env=process_env,
                cwd=str(self.cwd) if self.cwd else None
            )

            logger.info(f"Process started: PID={self.process.pid}, command={' '.join(self.command)}")
            if self.cwd:
                logger.info(f"Working directory: {self.cwd}")

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
                raise MCPError(
                    f"Received empty response. Error output: {stderr_output}")

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
            raise MCPError(
                "Client not initialized, please call connect() first")

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
            raise MCPError(
                "Client not initialized, please call connect() first")

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

    def __init__(self, server_url: str, timeout: int = 30, headers: Optional[Dict[str, str]] = None):
        """
        Initialize HTTP client

        Args:
            server_url: Server URL
            timeout: Request timeout in seconds
            headers: Additional headers to include in requests
        """
        self.server_url = server_url.rstrip('/')
        self.timeout = timeout
        self.headers = headers or {}
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
                raise ConnectionError(
                    "Initialization failed: No response received")

            result = results[0]
            self._initialized = True
            logger.info(
                f"Connection successful. Session ID: {self.session_id}, Server: {result.get('serverInfo')}")
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
        headers.update(self.headers)
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
                        raise MCPError(
                            f"Server error [{error.get('code')}]: {error.get('message')}")

                    if "result" in data:
                        yield data["result"]

                # Handle SSE streaming response
                elif "text/event-stream" in content_type:
                    for line in response.iter_lines():
                        if line and line.startswith(b"data: "):
                            try:
                                event_data = json.loads(
                                    line[6:].decode("utf-8"))
                                logger.debug(
                                    f"Processing SSE stream response: {event_data}")

                                if "error" in event_data:
                                    error = event_data["error"]
                                    raise MCPError(
                                        f"Stream error [{error.get('code')}]: {error.get('message')}")

                                # Return result or notification
                                if "result" in event_data:
                                    yield event_data["result"]
                                elif "method" in event_data:
                                    yield event_data  # Notification event

                            except json.JSONDecodeError:
                                logger.warning(
                                    f"Failed to parse SSE data: {line}")
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
            raise MCPError(
                "Client not initialized, please call connect() first")

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
            raise MCPError(
                "Client not initialized, please call connect() first")

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


# ========== MCP 管理器 ==========
class MCPManager:
    """MCP 服务器管理器"""

    def __init__(self):
        self.config = MCPConfig(Path.cwd() / "mcp.json")
        # name -> (client, tools)
        self.active_clients: Dict[str, Tuple[Any, List[Dict]]] = {}
        self.loaded = False

    def load_config(self) -> bool:
        """加载 MCP 配置"""
        if self.config.load():
            self.loaded = True
            logger.info(f"成功加载 {len(self.config)} 个 MCP 服务器配置")
            return True
        return False

    def list_servers(self) -> List[str]:
        """列出所有可用的 MCP 服务器"""
        if not self.loaded:
            self.load_config()
        return self.config.list_servers()

    def get_server_info(self, name: str) -> Optional[ServerConfig]:
        """获取服务器配置信息"""
        return self.config.get_server(name)

    def is_server_connected(self, name: str) -> bool:
        """判断指定的 MCP 服务器是否已连接"""
        return name in self.active_clients

    def connect_server(self, name: str) -> bool:
        """连接到指定的 MCP 服务器"""
        # 如果已经连接，直接返回
        if self.is_server_connected(name):
            logger.info(f"服务器 '{name}' 已连接")
            return True

        server = self.config.get_server(name)
        if not server:
            logger.error(f"未找到服务器 '{name}'")
            print("❌ 未找到 MCP 服务器配置")
            print("   请在当前目录创建 mcp.json 配置文件")
            return False

        if not server.enabled:
            logger.warning(f"服务器 '{name}' 已禁用")
            return False

        try:
            # 根据类型创建客户端
            if server.is_stdio():
                cmd = server.get_full_command()
                if not cmd:
                    logger.error(f"服务器 '{name}' 命令无效")
                    return False

                client = StdioClient(
                    cmd,
                    env=server.env,
                    cwd=server.cwd
                )
                client.connect()
                logger.info(f"✓ 已连接 stdio 服务器: {name}")

            else:  # HTTP
                if not server.url:
                    logger.error(f"服务器 '{name}' 缺少 URL")
                    return False
                client = StreambleHttpClient(
                    server.url,
                    timeout=server.timeout or 30,
                    headers=server.headers)
                client.connect()
                logger.info(f"✓ 已连接 HTTP 服务器: {name}")

            # 获取工具列表
            tools = client.list_tools()

            # 转换为 OpenAI 格式
            openai_tools = []
            for tool in tools:
                openai_tool = self._convert_mcp_tool(tool, name)
                openai_tools.append(openai_tool)

            # 保存客户端和工具
            self.active_clients[name] = (client, openai_tools)
            logger.info(
                f"加载了 {len(openai_tools)} 个工具: {[t['function']['name'] for t in openai_tools]}")

            return True

        except Exception as e:
            logger.error(f"连接服务器 '{name}' 失败: {e}")
            return False

    def disconnect_server(self, name: str) -> bool:
        """断开指定的 MCP 服务器"""
        if name not in self.active_clients:
            logger.warning(f"服务器 '{name}' 未连接")
            return False

        try:
            client, _ = self.active_clients[name]
            client.close()
            del self.active_clients[name]
            logger.info(f"✓ 已断开服务器: {name}")
            return True
        except Exception as e:
            logger.error(f"断开服务器 '{name}' 失败: {e}")
            return False

    def get_active_tools(self) -> List[Dict]:
        """获取所有已连接服务器的工具列表"""
        all_tools = []
        for name, (client, tools) in self.active_clients.items():
            all_tools.extend(tools)
        return all_tools

    def call_mcp_tool(self, server_name: str, tool_name: str, arguments: Dict) -> str:
        """调用 MCP 工具"""
        if server_name not in self.active_clients:
            return f"Error: 服务器 '{server_name}' 未连接"

        try:
            client, _ = self.active_clients[server_name]

            # 根据客户端类型调用工具
            if isinstance(client, StdioClient):
                result = client.call_tool(tool_name, arguments)
            else:  # HTTPClient
                results = list(client.call_tool(tool_name, arguments))
                result = results[0] if results else {}

            # 提取结果内容
            if isinstance(result, dict):
                content = result.get("content", [])
                if content and isinstance(content, list):
                    # 合并所有内容块
                    text_parts = []
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text_parts.append(item.get("text", ""))

                    logger.debug(f"MCP {server_name}:{tool_name} Tool Result: {content}")        
                    return "\n".join(text_parts) if text_parts else str(result)
                return str(result)

            return str(result)

        except Exception as e:
            logger.error(f"调用工具失败: {e}")
            return f"Error: {e}"

    def _convert_mcp_tool(self, mcp_tool: Dict, server_name: str) -> Dict:
        """将 MCP 工具格式转换为 OpenAI function calling 格式"""
        # 工具名称加上服务器前缀，避免冲突
        prefixed_name = f"mcp_{server_name}_{mcp_tool['name']}"

        input_schema = mcp_tool.get("inputSchema", {})

        return {
            "type": "function",
            "function": {
                "name": prefixed_name,
                "description": f"[MCP:{server_name}] {mcp_tool.get('description', '')}",
                "parameters": {
                    "type": "object",
                    "properties": input_schema.get("properties", {}),
                    "required": input_schema.get("required", []),
                },
            }
        }

    def interactive_select_server(self) -> List[str]:
        """
        交互式选择要连接的 MCP 服务器

        Returns:
            成功连接的服务器名称列表，失败或取消返回空列表
        """
        if not self.loaded:
            self.load_config()

        enabled_servers = self.config.list_enabled_servers()

        if not enabled_servers:
            print("❌ 没有可用的 MCP 服务器")
            print("   请检查 mcp.json 配置文件")
            return []

        print(f"\n可用的 MCP 服务器 ({len(enabled_servers)} 个):")
        print("-" * 60)

        server_list = []
        for i, server_name in enumerate(enabled_servers, 1):
            server = self.config.get_server(server_name)
            if server:
                is_connected = self.is_server_connected(server_name)
                description = server.description if server.description else "无描述"
                if len(description) > 40:
                    description = description[:37] + "..."
                active_mark = " - \033[32m(已连接)\033[0m" if is_connected else ""
                print(f"  {i}. {server_name:<20} {description}{active_mark}")
                server_list.append(server_name)

        print("-" * 60)

        while True:
            try:
                user_input = input("\n请输入要连接的服务器编号 (支持多个，用空格分隔，按 Enter 退出): ").strip()

                if not user_input:
                    print("取消操作")
                    return []

                indices = []
                invalid_numbers = []

                for num_str in user_input.split():
                    try:
                        index = int(num_str) - 1
                        if 0 <= index < len(server_list):
                            indices.append(index)
                        else:
                            invalid_numbers.append(num_str)
                    except ValueError:
                        invalid_numbers.append(num_str)

                if invalid_numbers:
                    print(f"❌ 无效的编号: {', '.join(invalid_numbers)}")
                    print(f"   请输入 1-{len(server_list)} 之间的数字，用空格分隔")
                    continue

                if not indices:
                    print("❌ 未选择任何服务器")
                    continue

                selected_servers = [server_list[i] for i in indices]
                connected_servers = []

                for server_name in selected_servers:
                    if self.connect_server(server_name):
                        connected_servers.append(server_name)
                    else:
                        print(f"⚠ 连接服务器 '{server_name}' 失败")

                if connected_servers:
                    print(f"\n✅ 成功连接 {len(connected_servers)} 个服务器")
                    return connected_servers
                else:
                    print("\n❌ 所有服务器连接失败")
                    return []

            except ValueError:
                print("❌ 请输入有效的数字")
            except KeyboardInterrupt:
                print("\n\n取消操作")
                return []

    def list_mcp_servers(self):
        """列出所有可用的 MCP 服务器"""
        servers = self.list_servers()

        if not servers:
            print("❌ 未找到 MCP 服务器配置")
            print("   请在当前目录创建 mcp.json 配置文件")
            return

        print(f"\n📋 可用的 MCP 服务器 ({len(servers)} 个):\n")

        for name in servers:
            server = self.get_server_info(name)
            if not server:
                continue

            # 检查是否已连接
            is_connected = name in self.active_clients
            status = "✓ 已连接" if is_connected else "○ 未连接"
            print(f"  [{status}] {name}")
            print(f"    类型: {server.type}")
            if server.description:
                print(f"    描述: {server.description}")
            if server.is_stdio():
                cmd = server.get_full_command()
                if cmd:
                    print(f"    命令: {' '.join(cmd)}")
            else:
                print(f"    URL: {server.url}")
            if not server.enabled:
                print(f"    状态: 已禁用")
            if is_connected:
                _, tools = self.active_clients[name]
                if tools:
                    tool_names = [tool['function']['name'].replace(f"mcp_{name}_", "") for tool in tools]
                    print(f"    工具: {', '.join(tool_names)}")
            print()

    def cleanup(self):
        """清理所有连接"""
        for name in list(self.active_clients.keys()):
            self.disconnect_server(name)

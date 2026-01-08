"""
MCP 配置文件解析器
支持解析和验证 mcp.json 配置文件
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """配置错误基类"""
    pass


class ConfigValidationError(ConfigError):
    """配置验证错误"""
    pass


class ConfigLoadError(ConfigError):
    """配置加载错误"""
    pass


@dataclass
class ServerConfig:
    """服务器配置数据类"""

    name: str
    type: str  # "stdio" 或 "http"

    # Stdio 配置
    command: Optional[str] = None
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    cwd: Optional[str] = None

    # HTTP 配置
    url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None

    # 通用配置
    timeout: Optional[int] = None
    enabled: bool = True
    description: Optional[str] = None

    @classmethod
    def _normalize_type(cls, server_type: str) -> str:
        """
        标准化服务器类型

        Args:
            server_type: 原始类型

        Returns:
            标准化的类型 ("stdio" 或 "http")
        """
        type_aliases = {
            "stdio": ["stdio", "local"],
            "http": ["http", "streamablehttp", "remote"]
        }

        server_type_lower = server_type.lower()
        for std_type, aliases in type_aliases.items():
            if server_type_lower in aliases:
                return std_type

        return server_type

    @classmethod
    def from_dict(cls, name: str, config: Dict[str, Any]) -> "ServerConfig":
        """
        从字典创建配置对象

        Args:
            name: 服务器名称
            config: 配置字典

        Returns:
            ServerConfig 实例

        Raises:
            ConfigValidationError: 配置验证失败
        """
        server_type = config.get("type", "stdio")

        # 标准化类型
        normalized_type = cls._normalize_type(server_type)

        if normalized_type not in ("stdio", "http"):
            raise ConfigValidationError(
                f"服务器 '{name}' 的类型 '{server_type}' 无效，"
                f"必须是 'stdio'/'local' 或 'http'/'streamableHttp'/'remote'"
            )

        # 根据类型验证特定字段
        if normalized_type == "stdio":
            if "command" not in config:
                raise ConfigValidationError(
                    f"Stdio 服务器 '{name}' 缺少必需的 'command' 字段"
                )

            return cls(
                name=name,
                type=normalized_type,
                command=config["command"],
                args=config.get("args", []),
                env=config.get("env"),
                cwd=config.get("cwd"),
                timeout=config.get("timeout"),
                enabled=config.get("enabled", True),
                description=config.get("description")
            )

        else:  # http
            if "url" not in config:
                raise ConfigValidationError(
                    f"HTTP 服务器 '{name}' 缺少必需的 'url' 字段"
                )

            return cls(
                name=name,
                type=normalized_type,
                url=config["url"],
                headers=config.get("headers"),
                timeout=config.get("timeout"),
                enabled=config.get("enabled", True),
                description=config.get("description")
            )

    def is_stdio(self) -> bool:
        """判断是否为 stdio 类型服务器"""
        return self.type == "stdio"

    def is_http(self) -> bool:
        """判断是否为 HTTP 类型服务器"""
        return self.type == "http"

    def get_full_command(self) -> Optional[List[str]]:
        """
        获取完整的命令行（仅 stdio 类型）

        Returns:
            完整命令列表，如果不是 stdio 类型则返回 None
        """
        if not self.is_stdio() or not self.command:
            return None

        cmd = [self.command]
        if self.args:
            cmd.extend(self.args)
        return cmd

    def __repr__(self) -> str:
        """字符串表示"""
        if self.is_stdio():
            return f"ServerConfig(name='{self.name}', type='stdio', command='{self.command}')"
        else:
            return f"ServerConfig(name='{self.name}', type='streamableHttp', url='{self.url}')"


class MCPConfig:
    """MCP 配置文件解析器"""

    def __init__(self, path: Union[str, Path] = "mcp.json"):
        """
        初始化配置解析器

        Args:
            path: 配置文件路径
        """
        self.path = Path(path)
        self.servers: Dict[str, ServerConfig] = {}
        self.raw_config: Dict[str, Any] = {}
        self._loaded = False

    def load(self) -> bool:
        """
        加载并验证配置文件

        Returns:
            加载是否成功
        """
        try:
            # 检查文件是否存在
            if not self.path.exists():
                self.servers = {}
                logger.warning(f"MCP 配置文件不存在: {self.path}")
                return True

            # 读取 JSON 文件
            with open(self.path, 'r', encoding='utf-8') as f:
                self.raw_config = json.load(f)

            # 验证配置结构
            if not isinstance(self.raw_config, dict):
                raise ConfigValidationError("配置文件必须是 JSON 对象")

            if "servers" not in self.raw_config:
                logger.warning("配置文件中没有 'servers' 字段")
                self.raw_config["servers"] = {}

            if not isinstance(self.raw_config["servers"], dict):
                raise ConfigValidationError("'servers' 字段必须是对象")

            # 解析每个服务器配置
            self.servers.clear()
            errors = []

            for name, config in self.raw_config["servers"].items():
                try:
                    server = ServerConfig.from_dict(name, config)
                    self.servers[name] = server
                    logger.debug(f"成功加载服务器配置: {name}")
                except ConfigValidationError as e:
                    errors.append(str(e))
                    logger.error(f"服务器配置验证失败: {e}")

            # 如果有错误，抛出异常
            if errors:
                logger.error(
                    f"配置验证失败，发现 {len(errors)} 个错误:\n" + "\n".join(f"  - {err}" for err in errors))

            self._loaded = True
            logger.info(f"成功加载 {len(self.servers)} 个服务器配置")
            return True

        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}")
            return False
        except ConfigError as e:
            logger.error(f"配置错误: {e}")
            return False
        except Exception as e:
            logger.error(f"加载配置文件时发生未知错误: {e}")
            return False

    def reload(self) -> bool:
        """
        重新加载配置文件

        Returns:
            重新加载是否成功
        """
        self._loaded = False
        return self.load()

    def get_server(self, name: str) -> Optional[ServerConfig]:
        """
        获取指定名称的服务器配置

        Args:
            name: 服务器名称

        Returns:
            服务器配置对象，如果不存在则返回 None
        """
        if not self._loaded:
            logger.warning("配置文件尚未加载，请先调用 load()")
            return None

        return self.servers.get(name)

    def list_servers(self) -> List[str]:
        """
        列出所有服务器名称

        Returns:
            服务器名称列表
        """
        return list(self.servers.keys())

    def list_enabled_servers(self) -> List[str]:
        """
        列出所有启用的服务器名称

        Returns:
            启用的服务器名称列表
        """
        return [
            name for name, config in self.servers.items()
            if config.enabled
        ]

    def get_servers_by_type(self, server_type: str) -> List[ServerConfig]:
        """
        获取指定类型的所有服务器

        Args:
            server_type: 服务器类型 ("stdio" 或 "http")

        Returns:
            服务器配置列表
        """
        normalized_type = ServerConfig._normalize_type(server_type)
        return [
            config for config in self.servers.values()
            if config.type == normalized_type
        ]

    def validate(self) -> List[str]:
        """
        验证配置的完整性

        Returns:
            警告和建议列表
        """
        warnings = []

        if not self.servers:
            warnings.append("没有配置任何服务器")

        enabled_servers = self.list_enabled_servers()
        if not enabled_servers:
            warnings.append("没有启用的服务器")

        # 检查重复的 URL 或命令
        urls = {}
        commands = {}

        for name, config in self.servers.items():
            if config.is_http() and config.url:
                if config.url in urls:
                    warnings.append(
                        f"服务器 '{name}' 和 '{urls[config.url]}' "
                        f"使用相同的 URL: {config.url}"
                    )
                else:
                    urls[config.url] = name

            if config.is_stdio() and config.command:
                cmd = config.get_full_command()
                cmd_str = " ".join(cmd) if cmd else ""
                if cmd_str in commands:
                    warnings.append(
                        f"服务器 '{name}' 和 '{commands[cmd_str]}' "
                        f"使用相同的命令: {cmd_str}"
                    )
                else:
                    commands[cmd_str] = name

        return warnings

    def to_dict(self) -> Dict[str, Any]:
        """
        将配置转换为字典

        Returns:
            配置字典
        """
        return {
            "servers": {
                name: self._server_to_dict(config)
                for name, config in self.servers.items()
            }
        }

    def _server_to_dict(self, server: ServerConfig) -> Dict[str, Any]:
        """将服务器配置转换为字典"""
        result = {"type": server.type}

        if server.is_stdio():
            result["command"] = server.command
            if server.args:
                result["args"] = server.args
            if server.env:
                result["env"] = server.env
            if server.cwd:
                result["cwd"] = server.cwd
        else:
            result["url"] = server.url
            if server.headers:
                result["headers"] = server.headers

        if server.timeout is not None:
            result["timeout"] = server.timeout

        if not server.enabled:
            result["enabled"] = False

        if server.description:
            result["description"] = server.description

        return result

    def save(self, path: Optional[Union[str, Path]] = None) -> bool:
        """
        保存配置到文件

        Args:
            path: 保存路径，默认为当前配置文件路径

        Returns:
            保存是否成功
        """
        save_path = Path(path) if path else self.path

        try:
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
            logger.info(f"配置已保存到: {save_path}")
            return True
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            return False

    def __len__(self) -> int:
        """返回服务器数量"""
        return len(self.servers)

    def __contains__(self, name: str) -> bool:
        """检查服务器是否存在"""
        return name in self.servers

    def __iter__(self):
        """迭代所有服务器配置"""
        return iter(self.servers.values())


def create_sample_config(path: Union[str, Path] = "mcp.json") -> bool:
    """
    创建示例配置文件

    Args:
        path: 配置文件路径

    Returns:
        创建是否成功
    """
    sample_config = {
        "servers": {
            "filesystem": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                "description": "文件系统访问服务器",
                "enabled": True
            },
            "weather": {
                "type": "http",
                "url": "http://localhost:8000/mcp",
                "description": "天气信息服务器",
                "timeout": 30,
                "enabled": True
            },
            "database": {
                "type": "local",
                "command": "python",
                "args": ["db_server.py"],
                "env": {
                    "DB_HOST": "localhost",
                    "DB_PORT": "5432"
                },
                "description": "数据库访问服务器",
                "enabled": False
            }
        }
    }

    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(sample_config, f, indent=2, ensure_ascii=False)
        print(f"✓ 示例配置文件已创建: {path}")
        return True
    except Exception as e:
        print(f"✗ 创建示例配置文件失败: {e}")
        return False


def example_usage():
    """使用示例"""
    import sys

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s - %(message)s'
    )

    print("\n" + "=" * 60)
    print("MCP 配置文件解析示例")
    print("=" * 60 + "\n")

    config_path = "test_mcp.json"

    # 如果配置文件不存在，创建示例配置
    if not Path(config_path).exists():
        print(f"配置文件不存在，创建示例配置...\n")
        if not create_sample_config(config_path):
            sys.exit(1)

    # 1. 加载配置
    print(f">>> 加载配置文件: {config_path}")
    config = MCPConfig(config_path)

    if not config.load():
        print("✗ 配置加载失败")
        sys.exit(1)

    print(f"✓ 成功加载 {len(config)} 个服务器配置\n")

    # 2. 列出所有服务器
    print(">>> 所有服务器:")
    for server in config:
        status = "✓ 已启用" if server.enabled else "✗ 已禁用"
        print(f"\n  [{status}] {server.name}")
        print(f"  类型: {server.type}")

        if server.description:
            print(f"  描述: {server.description}")

        if server.is_stdio():
            cmd = server.get_full_command()
            print(f"  命令: {' '.join(cmd) if cmd else 'N/A'}")
            if server.env:
                print(f"  环境变量: {server.env}")
        else:
            print(f"  URL: {server.url}")
            if server.headers:
                print(f"  请求头: {server.headers}")

        if server.timeout:
            print(f"  超时: {server.timeout}s")

    print("\n" + "-" * 60)

    # 3. 按类型筛选
    print("\n>>> Stdio 服务器:")
    stdio_servers = config.get_servers_by_type("stdio")
    for server in stdio_servers:
        print(f"  - {server.name}: {server.command}")

    print("\n>>> HTTP 服务器:")
    http_servers = config.get_servers_by_type("http")
    for server in http_servers:
        print(f"  - {server.name}: {server.url}")

    # 4. 验证配置
    print("\n" + "-" * 60)
    print("\n>>> 配置验证:")
    warnings = config.validate()
    if warnings:
        print("⚠ 发现以下警告:")
        for warning in warnings:
            print(f"  - {warning}")
    else:
        print("✓ 配置验证通过，无警告")

    # 5. 获取特定服务器
    print("\n" + "-" * 60)
    print("\n>>> 获取特定服务器:")
    server_name = "filesystem"
    server = config.get_server(server_name)
    if server:
        print(f"✓ 找到服务器 '{server_name}':")
        print(f"  {server}")
    else:
        print(f"✗ 未找到服务器 '{server_name}'")

    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    example_usage()

"""
Provider 配置解析器
支持多个 AI Provider 的配置管理
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


class ProviderConfigError(Exception):
    """Provider 配置错误"""

    pass


@dataclass
class ModelModalities:
    """模型支持的模态类型"""

    input: List[str] = None  # 支持的输入类型: ["text", "image", "audio", "video"]
    output: List[str] = None  # 支持的输出类型: ["text", "image"]

    def __post_init__(self):
        if self.input is None:
            self.input = ["text"]
        if self.output is None:
            self.output = ["text"]

    def supports_image_input(self) -> bool:
        """是否支持图片输入"""
        return "image" in self.input

    def supports_image_output(self) -> bool:
        """是否支持图片输出"""
        return "image" in self.output

    def supports_audio_input(self) -> bool:
        """是否支持音频输入"""
        return "audio" in self.input

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "ModelModalities":
        """从字典创建 ModelModalities"""
        if not data:
            return cls()
        return cls(
            input=data.get("input", ["text"]),
            output=data.get("output", ["text"]),
        )


@dataclass
class ModelInfo:
    """模型信息"""

    id: str  # 模型 ID（如 "gpt-4o"）
    name: str  # 模型显示名称
    provider_id: str  # 所属 provider ID
    modalities: ModelModalities = None  # 模型支持的模态类型
    thinking: Optional[str] = None  # 模型级 thinking 配置（"enabled" or "disabled"）

    def __post_init__(self):
        if self.modalities is None:
            self.modalities = ModelModalities()

    def __repr__(self) -> str:
        return f"ModelInfo(id='{self.id}', name='{self.name}', provider='{self.provider_id}')"


@dataclass
class ProviderOptions:
    """Provider 选项"""

    base_url: str
    api_key: str
    thinking: str = "enabled"  # "enabled" or "disabled"
    timeout: Optional[int] = None
    max_retries: Optional[int] = None
    extra_headers: Optional[Dict[str, str]] = None

    @classmethod
    def from_dict(cls, options: Dict[str, Any]) -> "ProviderOptions":
        """从字典创建 ProviderOptions"""
        return cls(
            base_url=options.get("baseURL", ""),
            api_key=options.get("apiKey", ""),
            thinking=options.get("thinking", "enabled"),
            timeout=options.get("timeout"),
            max_retries=options.get("maxRetries"),
            extra_headers=options.get("headers"),
        )

    def resolve_api_key(self) -> str:
        """
        解析 API Key，支持环境变量引用

        格式：
        - 直接值: "sk-xxxxx"
        - 环境变量: "env:OPENAI_API_KEY"
        """
        if self.api_key.startswith("env:"):
            env_var = self.api_key[4:]
            api_key = os.getenv(env_var)
            if not api_key:
                logger.warning(f"环境变量 {env_var} 未设置")
                return ""
            return api_key
        return self.api_key


@dataclass
class Provider:
    """Provider 配置"""

    id: str  # provider ID（如 "openai"）
    name: str  # provider 显示名称
    options: ProviderOptions
    models: Dict[str, ModelInfo]
    enabled: bool = True

    @classmethod
    def from_dict(cls, provider_id: str, config: Dict[str, Any]) -> "Provider":
        """从字典创建 Provider"""
        # 解析选项
        options = ProviderOptions.from_dict(config.get("options", {}))

        # 解析模型列表
        models = {}
        for model_id, model_config in config.get("models", {}).items():
            # 解析 modalities 配置
            modalities = ModelModalities.from_dict(model_config.get("modalities"))
            models[model_id] = ModelInfo(
                id=model_id,
                name=model_config.get("name", model_id),
                provider_id=provider_id,
                modalities=modalities,
                thinking=model_config.get("thinking"),  # 模型级 thinking 配置
            )

        return cls(
            id=provider_id,
            name=config.get("name", provider_id),
            options=options,
            models=models,
            enabled=config.get("enabled", True),
        )

    def get_model(self, model_id: str) -> Optional[ModelInfo]:
        """获取指定模型"""
        return self.models.get(model_id)

    def list_models(self) -> List[ModelInfo]:
        """列出所有模型"""
        return list(self.models.values())

    def get_full_model_id(self, model_id: str) -> str:
        """获取完整的模型 ID（provider/model）"""
        return f"{self.id}/{model_id}"

    def __repr__(self) -> str:
        return (
            f"Provider(id='{self.id}', name='{self.name}', models={len(self.models)})"
        )


class ProviderConfig:
    """Provider 配置管理器"""

    def __init__(self, config_path: Union[str, Path] = "providers.json", data: Optional[Dict[str, Any]] = None):
        """
        初始化配置管理器

        Args:
            config_path: 配置文件路径（data 为 None 时使用）
            data: 直接从字典加载配置，跳过文件读取
        """
        self.config_path = Path(config_path)
        self._data = data
        self.providers: Dict[str, Provider] = {}
        self.default_model: Optional[str] = None
        self.thinking: str = "enabled"  # 全局 thinking 默认值: "enabled" or "disabled"
        self.reasoning_effort: str = "high"  # 推理努力程度: "high" or "max"
        self.raw_config: Dict[str, Any] = {}
        self._loaded = False

    def load(self) -> bool:
        """
        加载配置文件

        Returns:
            加载是否成功
        """
        # 优先从字典加载
        if self._data is not None:
            return self._parse_dict(self._data)

        try:
            # 检查文件是否存在
            if not self.config_path.exists():
                logger.warning(f"配置文件不存在: {self.config_path}")
                return False

            # 读取 JSON 文件
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.raw_config = json.load(f)

            return self._parse_dict(self.raw_config)

        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}")
            return False
        except ProviderConfigError as e:
            logger.error(f"配置错误: {e}")
            return False
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            return False

    def _parse_dict(self, data: Dict[str, Any]) -> bool:
        """
        从字典解析配置

        Args:
            data: 配置字典（provider namespace，含 model/providers 等字段）

        Returns:
            解析是否成功
        """
        try:
            self.raw_config = data

            # 验证配置结构
            if not isinstance(data, dict):
                raise ProviderConfigError("配置文件必须是 JSON 对象")

            # 解析默认模型（可选字段）
            self.default_model = data.get("model")

            # 解析全局 thinking 配置（可选字段，默认 enabled）
            self.thinking = data.get("thinking", "enabled")

            # 解析全局 reasoning_effort 配置（可选字段，默认 high）
            # 有效值: "high", "max"
            self.reasoning_effort = data.get("reasoning_effort", "high")

            # 解析 providers
            providers_config = data.get("providers", {})
            if not isinstance(providers_config, dict):
                raise ProviderConfigError("'providers' 字段必须是对象")

            self.providers.clear()
            errors = []

            for provider_id, provider_config in providers_config.items():
                try:
                    provider = Provider.from_dict(provider_id, provider_config)
                    self.providers[provider_id] = provider
                    logger.debug(f"成功加载 Provider: {provider_id}")
                except Exception as e:
                    error_msg = f"Provider '{provider_id}' 解析失败: {e}"
                    errors.append(error_msg)
                    logger.error(error_msg)

            if errors:
                raise ProviderConfigError(
                    f"配置解析失败，发现 {len(errors)} 个错误:\n"
                    + "\n".join(f"  - {err}" for err in errors)
                )

            self._loaded = True
            logger.info(f"成功加载 {len(self.providers)} 个 Provider 配置")

            # 验证默认模型
            if self.default_model:
                if not self.get_model_info(self.default_model):
                    logger.warning(f"默认模型 '{self.default_model}' 不存在")

            return True

        except ProviderConfigError as e:
            logger.error(f"配置错误: {e}")
            return False
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            return False

    def get_provider(self, provider_id: str) -> Optional[Provider]:
        """获取指定 Provider"""
        return self.providers.get(provider_id)

    def list_providers(self) -> List[Provider]:
        """列出所有 Provider"""
        return list(self.providers.values())

    def list_enabled_providers(self) -> List[Provider]:
        """列出所有启用的 Provider"""
        return [p for p in self.providers.values() if p.enabled]

    def parse_model_id(self, model_id: str) -> tuple[Optional[str], str]:
        """
        解析模型 ID

        Args:
            model_id: 模型 ID，格式可以是：
                     - "provider/model" (如 "openai/gpt-4o")
                     - "model" (如 "gpt-4o")

        Returns:
            (provider_id, model_id)
        """
        if "/" in model_id:
            parts = model_id.split("/", 1)
            return (parts[0], parts[1])
        return (None, model_id)

    def get_model_info(self, model_id: str) -> Optional[ModelInfo]:
        """
        获取模型信息

        Args:
            model_id: 模型 ID，支持 "provider/model" 或 "model" 格式

        Returns:
            ModelInfo 或 None
        """
        provider_id, model_name = self.parse_model_id(model_id)

        if provider_id:
            # 指定了 provider
            provider = self.get_provider(provider_id)
            if provider:
                return provider.get_model(model_name)
        else:
            # 未指定 provider，搜索所有 provider
            for provider in self.providers.values():
                model = provider.get_model(model_name)
                if model:
                    return model

        return None

    def get_provider_for_model(self, model_id: str) -> Optional[Provider]:
        """
        获取模型所属的 Provider

        Args:
            model_id: 模型 ID

        Returns:
            Provider 或 None
        """
        model_info = self.get_model_info(model_id)
        if model_info:
            return self.get_provider(model_info.provider_id)
        return None

    def list_all_models(self) -> List[ModelInfo]:
        """列出所有模型"""
        models = []
        for provider in self.providers.values():
            models.extend(provider.list_models())
        return models

    def get_api_config(self, model_id: str) -> Optional[Dict[str, Any]]:
        """
        获取指定模型的 API 配置

        Args:
            model_id: 模型 ID

        Returns:
            包含 base_url, api_key, model 等信息的字典
        """
        provider = self.get_provider_for_model(model_id)
        if not provider:
            return None

        model_info = self.get_model_info(model_id)
        if not model_info:
            return None

        # thinking 优先级：模型级 > provider 级 > 全局级
        thinking = model_info.thinking or provider.options.thinking or self.thinking

        return {
            "base_url": provider.options.base_url,
            "api_key": provider.options.resolve_api_key(),
            "model": model_info.id,
            "provider": provider.id,
            "thinking": thinking,
            "reasoning_effort": self.reasoning_effort,  # 全局 reasoning_effort 设置
            "timeout": provider.options.timeout,
            "max_retries": provider.options.max_retries,
            "headers": provider.options.extra_headers,
        }

    def validate(self) -> List[str]:
        """
        验证配置

        Returns:
            警告列表
        """
        warnings = []

        if not self.providers:
            warnings.append("没有配置任何 Provider")

        enabled_providers = self.list_enabled_providers()
        if not enabled_providers:
            warnings.append("没有启用的 Provider")

        # 检查 API Key
        for provider in self.providers.values():
            api_key = provider.options.resolve_api_key()
            if not api_key:
                warnings.append(f"Provider '{provider.id}' 的 API Key 未设置")

            if not provider.options.base_url:
                warnings.append(f"Provider '{provider.id}' 的 base_url 未设置")

            if not provider.models:
                warnings.append(f"Provider '{provider.id}' 没有配置任何模型")

        # 检查默认模型（可选字段）
        if self.default_model:
            if not self.get_model_info(self.default_model):
                logger.warning(f"默认模型 '{self.default_model}' 不存在")

        return warnings

    def to_dict(self) -> Dict[str, Any]:
        """将配置转换为字典"""
        providers_dict = {}
        for provider_id, provider in self.providers.items():
            models_dict = {}
            for model_id, model in provider.models.items():
                model_data: Dict[str, Any] = {"name": model.name}
                # 保存 modalities 配置（仅当不是默认值时）
                if model.modalities:
                    if model.modalities.input != ["text"] or model.modalities.output != ["text"]:
                        model_data["modalities"] = {
                            "input": model.modalities.input,
                            "output": model.modalities.output,
                        }
                models_dict[model_id] = model_data

            providers_dict[provider_id] = {
                "name": provider.name,
                "enabled": provider.enabled,
                "options": {
                    "baseURL": provider.options.base_url,
                    "apiKey": provider.options.api_key,
                    "thinking": provider.options.thinking,
                    "timeout": provider.options.timeout,
                    "maxRetries": provider.options.max_retries,
                },
                "models": models_dict,
            }

        return {"model": self.default_model, "thinking": self.thinking, "reasoning_effort": self.reasoning_effort, "providers": providers_dict}

    def save(self, path: Optional[Union[str, Path]] = None) -> bool:
        """
        保存配置到文件

        Args:
            path: 保存路径，默认为当前配置文件路径

        Returns:
            保存是否成功
        """
        save_path = Path(path) if path else self.config_path

        try:
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
            logger.info(f"配置已保存到: {save_path}")
            return True
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            return False

    def get_model_by_index(self, index: int) -> Optional[str]:
        """根据索引获取模型ID（从1开始）

        Args:
            index: 模型索引（从1开始）

        Returns:
            模型ID（provider/model格式），如果索引无效返回None
        """
        models = []
        for provider in self.list_enabled_providers():
            for model in provider.list_models():
                models.append(provider.get_full_model_id(model.id))

        if 1 <= index <= len(models):
            return models[index - 1]
        return None

    def get_current_model_index(self) -> int:
        """获取当前模型的索引

        Returns:
            当前模型的索引（从1开始），如果未找到返回0
        """
        if not self.default_model:
            return 0

        models = []
        for provider in self.list_enabled_providers():
            for model in provider.list_models():
                models.append(provider.get_full_model_id(model.id))

        try:
            return models.index(self.default_model) + 1
        except ValueError:
            return 0


def create_sample_config(path: Union[str, Path] = "providers.json") -> bool:
    """
    创建示例配置文件

    Args:
        path: 配置文件路径

    Returns:
        创建是否成功
    """
    sample_config = {
        "model": "openai/gpt-4o",
        "providers": {
            "openai": {
                "name": "OpenAI",
                "enabled": True,
                "options": {
                    "baseURL": "https://api.openai.com/v1",
                    "apiKey": "env:OPENAI_API_KEY",
                    "timeout": 60,
                },
                "models": {
                    "gpt-4o": {"name": "GPT-4o"},
                    "gpt-4-turbo": {"name": "GPT-4 Turbo"},
                    "gpt-3.5-turbo": {"name": "GPT-3.5 Turbo"},
                },
            },
            "cerebras": {
                "name": "Cerebras AI",
                "enabled": True,
                "options": {
                    "baseURL": "https://api.cerebras.ai/v1",
                    "apiKey": "env:CEREBRAS_API_KEY",
                },
                "models": {
                    "qwen-3-235b-a22b": {"name": "Qwen 3 235B A22B"},
                    "llama-3.3-70b": {"name": "Llama 3.3 70B"},
                },
            },
            "deepseek": {
                "name": "DeepSeek",
                "enabled": True,
                "options": {
                    "baseURL": "https://api.deepseek.com/v1",
                    "apiKey": "env:DEEPSEEK_API_KEY",
                },
                "models": {
                    "deepseek-chat": {"name": "DeepSeek Chat"},
                    "deepseek-reasoner": {"name": "DeepSeek Reasoner"},
                },
            },
            "anthropic": {
                "name": "Anthropic",
                "enabled": False,
                "options": {
                    "baseURL": "https://api.anthropic.com/v1",
                    "apiKey": "env:ANTHROPIC_API_KEY",
                },
                "models": {
                    "claude-sonnet-4": {"name": "Claude Sonnet 4"},
                    "claude-opus-4": {"name": "Claude Opus 4"},
                },
            },
        },
    }

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sample_config, f, indent=2, ensure_ascii=False)
        print(f"✓ 示例配置文件已创建: {path}")
        return True
    except Exception as e:
        print(f"✗ 创建示例配置失败: {e}")
        return False


def example_usage():
    """使用示例"""
    import sys

    # 配置日志
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

    print("\n" + "=" * 60)
    print("Provider 配置解析器示例")
    print("=" * 60 + "\n")

    config_path = "providers.json"

    # 如果配置文件不存在，创建示例配置
    if not Path(config_path).exists():
        print(f"配置文件不存在，创建示例配置...\n")
        if not create_sample_config(config_path):
            sys.exit(1)

    # 1. 加载配置
    print(f">>> 加载配置文件: {config_path}")
    config = ProviderConfig(config_path)

    if not config.load():
        print("✗ 配置加载失败")
        sys.exit(1)

    print(f"✓ 成功加载 {len(config.providers)} 个 Provider\n")

    # 2. 显示默认模型
    if config.default_model:
        print(f">>> 默认模型: {config.default_model}")
        model_info = config.get_model_info(config.default_model)
        if model_info:
            print(f"    名称: {model_info.name}")
            print(f"    Provider: {model_info.provider_id}\n")

    # 3. 列出所有 Provider
    print(">>> 所有 Provider:")
    for provider in config.list_providers():
        status = "✓ 已启用" if provider.enabled else "✗ 已禁用"
        print(f"\n  [{status}] {provider.id}")
        print(f"    名称: {provider.name}")
        print(f"    Base URL: {provider.options.base_url}")
        print(f"    模型数量: {len(provider.models)}")

        # 显示模型列表
        if provider.models:
            print(f"    模型:")
            for model in provider.list_models():
                print(f"      - {model.id}: {model.name}")

    print("\n" + "-" * 60)

    # 4. 列出所有模型
    print("\n>>> 所有可用模型:")
    all_models = config.list_all_models()
    for model in all_models:
        full_id = f"{model.provider_id}/{model.id}"
        print(f"  - {full_id}: {model.name}")

    # 5. 获取模型的 API 配置
    print("\n" + "-" * 60)
    print("\n>>> 获取模型 API 配置:")

    test_models = [
        "openai/gpt-4o",
        "gpt-4o",  # 不指定 provider
        "deepseek-chat",
    ]

    for model_id in test_models:
        print(f"\n  模型: {model_id}")
        api_config = config.get_api_config(model_id)
        if api_config:
            print(f"    Base URL: {api_config['base_url']}")
            print(f"    Model: {api_config['model']}")
            print(f"    Provider: {api_config['provider']}")
            api_key = api_config["api_key"]
            if api_key:
                print(
                    f"    API Key: {api_key[:10]}..."
                    if len(api_key) > 10
                    else "    API Key: (未设置)"
                )
            else:
                print(f"    API Key: (未设置)")
        else:
            print(f"    ✗ 未找到")

    # 6. 验证配置
    print("\n" + "-" * 60)
    print("\n>>> 配置验证:")
    warnings = config.validate()
    if warnings:
        print("⚠ 发现以下警告:")
        for warning in warnings:
            print(f"  - {warning}")
    else:
        print("✓ 配置验证通过，无警告")

    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    example_usage()

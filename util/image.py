"""
图片处理工具模块
支持图片下载、Base64 编码、格式验证等功能
"""

import base64
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 支持的图片格式
SUPPORTED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

# MIME 类型映射
MIME_TYPE_MAP = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.bmp': 'image/bmp',
}


def get_image_mime_type(file_path: str) -> str:
    """根据文件扩展名获取图片 MIME 类型

    Args:
        file_path: 文件路径

    Returns:
        MIME 类型字符串，默认返回 image/jpeg
    """
    ext = os.path.splitext(file_path)[1].lower()
    return MIME_TYPE_MAP.get(ext, 'image/jpeg')


def is_supported_image(file_path: str) -> bool:
    """检查文件是否为支持的图片格式

    Args:
        file_path: 文件路径

    Returns:
        是否为支持的图片格式
    """
    ext = os.path.splitext(file_path)[1].lower()
    return ext in SUPPORTED_IMAGE_EXTENSIONS


def image_to_base64(file_path: str) -> Optional[str]:
    """将图片文件转换为 Base64 编码

    Args:
        file_path: 图片文件路径

    Returns:
        Base64 编码字符串，失败返回 None
    """
    try:
        path = Path(file_path)
        if not path.exists():
            logger.error(f"图片文件不存在: {file_path}")
            return None

        if not path.is_file():
            logger.error(f"路径不是文件: {file_path}")
            return None

        if not is_supported_image(file_path):
            logger.error(f"不支持的图片格式: {file_path}")
            return None

        with open(path, 'rb') as f:
            image_data = f.read()

        base64_data = base64.b64encode(image_data).decode('utf-8')
        logger.debug(f"图片已转换为 Base64: {file_path} ({len(base64_data)} chars)")

        return base64_data
    except Exception as e:
        logger.error(f"图片转 Base64 失败: {e}")
        return None


def image_bytes_to_base64(image_bytes: bytes) -> str:
    """将图片字节数据转换为 Base64 编码

    Args:
        image_bytes: 图片字节数据

    Returns:
        Base64 编码字符串
    """
    return base64.b64encode(image_bytes).decode('utf-8')


def create_data_url(file_path: str) -> Optional[str]:
    """创建 Data URL（用于 API 调用）

    Args:
        file_path: 图片文件路径

    Returns:
        Data URL 字符串，格式: data:{mime_type};base64,{base64_data}
        失败返回 None
    """
    base64_data = image_to_base64(file_path)
    if not base64_data:
        return None

    mime_type = get_image_mime_type(file_path)
    return f"data:{mime_type};base64,{base64_data}"


def create_data_url_from_bytes(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    """从字节数据创建 Data URL

    Args:
        image_bytes: 图片字节数据
        mime_type: MIME 类型

    Returns:
        Data URL 字符串
    """
    base64_data = image_bytes_to_base64(image_bytes)
    return f"data:{mime_type};base64,{base64_data}"


def get_image_info(file_path: str) -> Optional[dict]:
    """获取图片基本信息

    Args:
        file_path: 图片文件路径

    Returns:
        包含图片信息的字典，失败返回 None
    """
    try:
        path = Path(file_path)
        if not path.exists():
            return None

        stat = path.stat()
        return {
            "path": str(path),
            "name": path.name,
            "extension": path.suffix.lower(),
            "mime_type": get_image_mime_type(file_path),
            "size_bytes": stat.st_size,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "is_supported": is_supported_image(file_path),
        }
    except Exception as e:
        logger.error(f"获取图片信息失败: {e}")
        return None


def validate_image_size(file_path: str, max_size_mb: float = 50.0) -> bool:
    """验证图片文件大小

    Args:
        file_path: 图片文件路径
        max_size_mb: 最大文件大小（MB）

    Returns:
        文件大小是否在限制内
    """
    try:
        path = Path(file_path)
        if not path.exists():
            return False

        size_mb = path.stat().st_size / (1024 * 1024)
        return size_mb <= max_size_mb
    except Exception:
        return False

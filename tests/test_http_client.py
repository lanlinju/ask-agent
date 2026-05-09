import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.mcp import StreambleHttpClient
import logging


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_http_client():
    """测试 HTTP 客户端"""
    print("\n" + "=" * 50)
    print("测试 HTTP 客户端")
    print("=" * 50 + "\n")
    
    try:
        with StreambleHttpClient("http://localhost:8000/mcp") as client:
            # 列出工具
            print(">>> 列出可用工具")
            tools = client.list_tools()
            for tool in tools:
                print(f"  - {tool['name']}: {tool.get('description', 'N/A')}")
            print()

            print(">>> 调用: greet('HTTP')")
            for result in client.call_tool("greet", {"name": "HTTP"}):
                content = result.get("content", [])
                if content:
                    print(f"结果: {content[0].get('text', '')[:100]}...")
            print()
           
            print()

    except Exception as e:
        logger.error(f"测试失败: {e}")

if __name__ == "__main__":
    test_http_client()        
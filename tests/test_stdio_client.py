import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp import StdioClient
import logging


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_stdio_client():
    """测试 stdio 客户端"""
    print("\n" + "=" * 50)
    print("测试 Stdio 客户端")
    print("=" * 50 + "\n")
    
    try:
        server_path = os.path.join(os.path.dirname(__file__), "stdio_server.py")
        with StdioClient(["python3", server_path]) as client:
            # 列出工具
            print(">>> 列出可用工具")
            tools = client.list_tools()
            for tool in tools:
                print(f"  - {tool['name']}: {tool.get('description', 'N/A')}")
            print()

            # 调用工具
            print(">>> 调用工具: add(2, 3)")
            result = client.call_tool("add", {"a": 2, "b": 3})
            print(f"结果: {result}\n")

            print(">>> 调用工具: greet('Python')")
            result = client.call_tool("greet", {"name": "Python"})
            print(f"结果: {result}\n")

    except Exception as e:
        logger.error(f"测试失败: {e}")

if __name__ == "__main__":
    test_stdio_client()
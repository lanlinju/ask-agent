def test_merge_arguments_empty():
    """测试空输入"""
    from ask import merge_arguments
    result = merge_arguments([])
    assert result == []


def test_merge_arguments_single_tool():
    """测试单个完整工具调用"""
    from ask import merge_arguments
    
    tool_calls = [
        {'index': 0, 'id': 'call_123', 'type': 'function', 'function': {'name': 'bash', 'arguments': '{"cmd":"ls"}'}}
    ]
    
    result = merge_arguments(tool_calls)
    assert len(result) == 1
    assert result[0]['id'] == 'call_123'
    assert result[0]['function']['name'] == 'bash'
    assert result[0]['function']['arguments'] == '{"cmd":"ls"}'


def test_merge_arguments_multiple_fragments():
    """测试单个工具调用的多个 arguments 片段"""
    from ask import merge_arguments
    
    tool_calls = [
        {'index': 0, 'id': 'call_123', 'type': 'function', 'function': {'name': 'bash', 'arguments': ''}},
        {'index': 0, 'function': {'arguments': '{'}},
        {'index': 0, 'function': {'arguments': '"'}},
        {'index': 0, 'function': {'arguments': 'command'}},
        {'index': 0, 'function': {'arguments': '"'}},
        {'index': 0, 'function': {'arguments': ':'}},
        {'index': 0, 'function': {'arguments': ' '}},
        {'index': 0, 'function': {'arguments': '"'}},
        {'index': 0, 'function': {'arguments': 'echo'}},
        {'index': 0, 'function': {'arguments': ' '}},
        {'index': 0, 'function': {'arguments': 'Hello'}},
        {'index': 0, 'function': {'arguments': '"'}},
        {'index': 0, 'function': {'arguments': '}'}},
    ]
    
    result = merge_arguments(tool_calls)
    assert len(result) == 1
    assert result[0]['id'] == 'call_123'
    assert result[0]['function']['name'] == 'bash'
    assert result[0]['function']['arguments'] == '{"command": "echo Hello"}'


def test_merge_arguments_multiple_tools():
    """测试多个不同的工具调用"""
    from ask import merge_arguments
    
    tool_calls = [
        {'index': 0, 'id': 'call_001', 'type': 'function', 'function': {'name': 'bash', 'arguments': '{"cmd":"ls"}'}},
        {'index': 1, 'id': 'call_002', 'type': 'function', 'function': {'name': 'bash', 'arguments': '{"cmd":"pwd"}'}},
    ]
    
    result = merge_arguments(tool_calls)
    assert len(result) == 2
    assert result[0]['id'] == 'call_001'
    assert result[0]['function']['arguments'] == '{"cmd":"ls"}'
    assert result[1]['id'] == 'call_002'
    assert result[1]['function']['arguments'] == '{"cmd":"pwd"}'


def test_merge_arguments_multiple_tools_with_fragments():
    """测试多个工具调用，每个都有多个 fragments"""
    from ask import merge_arguments
    
    tool_calls = [
        {'index': 0, 'id': 'call_001', 'type': 'function', 'function': {'name': 'bash', 'arguments': ''}},
        {'index': 0, 'function': {'arguments': '{'}},
        {'index': 0, 'function': {'arguments': '"cmd"'}},
        {'index': 0, 'function': {'arguments': ':'}},
        {'index': 0, 'function': {'arguments': '"ls"'}},
        {'index': 0, 'function': {'arguments': '}'}},
        {'index': 1, 'id': 'call_002', 'type': 'function', 'function': {'name': 'bash', 'arguments': ''}},
        {'index': 1, 'function': {'arguments': '{'}},
        {'index': 1, 'function': {'arguments': '"cmd"'}},
        {'index': 1, 'function': {'arguments': ':'}},
        {'index': 1, 'function': {'arguments': '"pwd"'}},
        {'index': 1, 'function': {'arguments': '}'}},
    ]
    
    result = merge_arguments(tool_calls)
    assert len(result) == 2
    assert result[0]['id'] == 'call_001'
    assert result[0]['function']['arguments'] == '{"cmd":"ls"}'
    assert result[1]['id'] == 'call_002'
    assert result[1]['function']['arguments'] == '{"cmd":"pwd"}'


def test_merge_arguments_order_preserved():
    """测试工具调用顺序保持不变"""
    from ask import merge_arguments
    
    tool_calls = [
        {'index': 2, 'id': 'call_003', 'type': 'function', 'function': {'name': 'bash', 'arguments': '{"cmd":"date"}'}},
        {'index': 0, 'id': 'call_001', 'type': 'function', 'function': {'name': 'bash', 'arguments': '{"cmd":"ls"}'}},
        {'index': 1, 'id': 'call_002', 'type': 'function', 'function': {'name': 'bash', 'arguments': '{"cmd":"pwd"}'}},
    ]
    
    result = merge_arguments(tool_calls)
    assert len(result) == 3
    assert result[0]['id'] == 'call_001'
    assert result[1]['id'] == 'call_002'
    assert result[2]['id'] == 'call_003'


def test_merge_arguments_gemini_thought_signature():
    """测试Gemini的thought_signature保留在extra_content中"""
    from ask import merge_arguments
    
    # Gemini返回的tool_call带有extra_content.google.thought_signature
    tool_calls = [
        {
            'index': 0,
            'id': 'call_gemini',
            'type': 'function',
            'function': {'name': 'bash', 'arguments': '{"command":"date"}'},
            'extra_content': {
                'google': {
                    'thought_signature': 'EjQKMgEMOdbHhEfnAtFwU4+jzZWkiXO9bAhTFWzwXTyJiQfOE/STssHZ/5d6nkfiQOjjMtvN'
                }
            }
        }
    ]
    
    result = merge_arguments(tool_calls)
    assert len(result) == 1
    assert result[0]['id'] == 'call_gemini'
    assert 'extra_content' in result[0]
    assert 'google' in result[0]['extra_content']
    assert 'thought_signature' in result[0]['extra_content']['google']
    assert result[0]['extra_content']['google']['thought_signature'] == 'EjQKMgEMOdbHhEfnAtFwU4+jzZWkiXO9bAhTFWzwXTyJiQfOE/STssHZ/5d6nkfiQOjjMtvN'


def test_merge_arguments_gemini_thought_signature_top_level():
    """测试Gemini的thought_signature在顶层时转换为extra_content格式"""
    from ask import merge_arguments
    
    # 兼容thought_signature在顶层的情况
    tool_calls = [
        {
            'index': 0,
            'id': 'call_gemini',
            'type': 'function',
            'function': {'name': 'bash', 'arguments': '{"command":"date"}'},
            'thought_signature': 'EjQKMgEMOdbHhEfnAtFwU4+jzZWkiXO9bAhTFWzwXTyJiQfOE/STssHZ/5d6nkfiQOjjMtvN'
        }
    ]
    
    result = merge_arguments(tool_calls)
    assert len(result) == 1
    assert result[0]['id'] == 'call_gemini'
    assert 'extra_content' in result[0]
    assert 'google' in result[0]['extra_content']
    assert 'thought_signature' in result[0]['extra_content']['google']
    assert result[0]['extra_content']['google']['thought_signature'] == 'EjQKMgEMOdbHhEfnAtFwU4+jzZWkiXO9bAhTFWzwXTyJiQfOE/STssHZ/5d6nkfiQOjjMtvN'

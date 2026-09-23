"""
忆光 Agent 用户隔离测试

验证不同用户的数据互不可见：
1. 用户A保存记忆 → 只属于用户A
2. 用户B保存记忆 → 只属于用户B
3. 用户A搜索 → 只看到A的记忆
4. 用户B搜索 → 只看到B的记忆
"""
import sys
import os
import json

# 设置工作目录
sys.path.insert(0, os.path.join(os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"), "src"))

from agents.agent import run_agent


def test_user_isolation():
    """测试用户数据隔离"""
    print("=" * 60)
    print("🧪 用户隔离测试")
    print("=" * 60)

    # 用户A保存一条记忆
    print("\n📝 用户A保存记忆...")
    result_a = run_agent("今天去爬了香山，秋天的红叶特别美", user_id="user_A")
    print(f"   用户A结果: {result_a}")

    # 用户B保存一条记忆
    print("\n📝 用户B保存记忆...")
    result_b = run_agent("今天在公司做了一个技术分享，反应不错", user_id="user_B")
    print(f"   用户B结果: {result_b}")

    # 用户A搜索记忆
    print("\n🔍 用户A搜索「香山」...")
    search_a = run_agent("帮我搜索一下关于香山的记忆", user_id="user_A")
    print(f"   用户A搜索结果: {search_a}")

    # 用户B搜索记忆（不应该看到用户A的香山记忆）
    print("\n🔍 用户B搜索「香山」...")
    search_b = run_agent("帮我搜索一下关于香山的记忆", user_id="user_B")
    print(f"   用户B搜索结果: {search_b}")

    # 用户A搜索记忆（不应该看到用户B的技术分享）
    print("\n🔍 用户A搜索「技术分享」...")
    search_a2 = run_agent("帮我搜索一下关于技术分享的记忆", user_id="user_A")
    print(f"   用户A搜索结果: {search_a2}")

    # 验证隔离
    print("\n" + "=" * 60)
    print("📊 隔离验证:")
    print(f"   用户A搜到香山: {'香山' in str(search_a)}")
    print(f"   用户B搜到香山: {'香山' in str(search_b)}")
    print(f"   用户A搜到技术分享: {'技术分享' in str(search_a2)}")
    print("=" * 60)


if __name__ == "__main__":
    test_user_isolation()

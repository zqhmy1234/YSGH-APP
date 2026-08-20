"""pytest 根配置（P3 顺手清理：消除 23 个测试文件的 sys.path 样板）

conftest.py 所在目录（backend/）会被 pytest 自动加入 sys.path，
因此 tests/ 下的测试可直接 `import app.*`，无需手写
`sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`。
存量测试的样板保留（无害），新测试不再需要。
"""

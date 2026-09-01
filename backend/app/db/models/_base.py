"""models 子包共享工具（R1#16 拆包 · 包内私有，勿直接 import 本模块）

拆包后各域模块仍需要主键生成 helper `_uuid`（原 models.py 模块级函数），
归集到本模块避免每个域模块重复定义。
"""
import uuid


def _uuid() -> str:
    return str(uuid.uuid4())

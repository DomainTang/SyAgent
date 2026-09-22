# -*- coding: utf-8 -*-
"""Shihua VOC MCP Server：石化疑似源识别（数据生成 + 溯源分析）MCP 服务。

入口见 ``shihua_mcp.server``：
    python -m shihua_mcp.server            # stdio
    python -m shihua_mcp.server --self-test
    python -m shihua_mcp.server --http

（此处故意不 import server：``python -m shihua_mcp.server`` 需要的是模块本身，
包初始化时提前导入会触发 runpy 的 "found in sys.modules" 警告。）
"""

VERSION = "0.2.0"

__all__ = ["VERSION"]

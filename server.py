#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仓库根目录启动入口（供 MCP 平台/客户端直接拉起，无需先安装本包）。

为什么需要它：本仓库是 ``src/`` 布局，克隆下来直接执行
``python -m shihua_mcp.server`` 会因为 ``shihua_mcp`` 不在 ``sys.path`` 而报
``ModuleNotFoundError``；平台托管部署通常只给一个启动命令，不会先 ``pip install``。
本文件先把 ``src/`` 挂进 ``sys.path`` 再调用服务主函数，因此下面三种写法都可用：

```bash
python server.py                    # 裸克隆直接跑（推荐给平台填启动命令）
python server.py --self-test        # 自检
python server.py --http --port 8000 # Streamable HTTP + SSE
shihua-mcp                          # pip install -e . 之后的控制台脚本
```
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from shihua_mcp.server import main  # noqa: E402  (必须在 sys.path 调整之后导入)

if __name__ == "__main__":
    sys.exit(main() or 0)

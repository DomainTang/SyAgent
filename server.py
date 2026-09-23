#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""仓库根目录启动入口（供 MCP 平台/客户端直接拉起，无需先安装本包）。

为什么需要它：本仓库是 ``src/`` 布局，克隆下来直接执行
``python -m shihua_mcp.server`` 会因为 ``shihua_mcp`` 不在 ``sys.path`` 而报
``ModuleNotFoundError``；平台托管部署通常只给一个启动命令，不会先 ``pip install``。
本文件先把 ``src/`` 挂进 ``sys.path`` 再调用服务主函数，因此下面三种写法都可用：

```bash
python server.py                    # 裸克隆直接跑（推荐给平台填启动命令）
python server.py --check-env        # 环境自检：解释器 / 依赖 / 数据与模型是否齐备
python server.py --self-test        # 自检
python server.py --http --port 8000 # Streamable HTTP + SSE
shihua-mcp                          # pip install -e . 之后的控制台脚本
```

依赖装在哪个解释器里就用哪个解释器启动（例如 Windows 上
``.\.venv\Scripts\python.exe server.py --http``）。用错解释器时不会抛裸 traceback，
而是直接告诉你该换成哪个命令。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# 环境检查只用标准库，因此依赖没装时也能运行
from shihua_mcp.env_check import (  # noqa: E402
    check_environment,
    format_dependency_hint,
    missing_modules,
)

if "--check-env" in sys.argv:
    sys.exit(check_environment())

_missing = missing_modules()
if _missing:
    print(format_dependency_hint(_missing), file=sys.stderr)
    sys.exit(2)

from shihua_mcp.server import main  # noqa: E402  (必须在 sys.path 与依赖检查之后)

if __name__ == "__main__":
    sys.exit(main() or 0)

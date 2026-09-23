# -*- coding: utf-8 -*-
"""环境自检：解释器、依赖、数据与模型文件（只用标准库，缺依赖时也能运行）。

用途：``python server.py --check-env``

之所以单独放一个模块：服务主模块要 import fastmcp/mcp，依赖没装时直接抛裸
traceback；而环境检查本身必须在“依赖没装”的情况下也能跑，用来告诉用户到底
该用哪个解释器。
"""
from __future__ import annotations

import importlib.util
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path

# 服务启动必需；缺了就起不来
CORE_MODULES = ("mcp", "fastmcp")
# 两个工具用到的库；缺了对应功能不可用
FEATURE_MODULES = ("torch", "numpy", "pandas", "scipy", "matplotlib", "folium", "openpyxl")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VENV_DIRS = (".venv", "venv", ".env")


def missing_modules(modules: tuple = CORE_MODULES + FEATURE_MODULES) -> list:
    """返回当前解释器里找不到的模块名（不真正导入，秒级返回）。"""
    missing = []
    for name in modules:
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ValueError):  # 依赖损坏或父包缺失
            missing.append(name)
    return missing


def project_venv(project_root: Path = PROJECT_ROOT):
    """工程自带的虚拟环境（存在则返回路径，否则 None）。"""
    for dirname in VENV_DIRS:
        candidate = project_root / dirname
        if (candidate / ("Scripts" if sys.platform == "win32" else "bin")).is_dir():
            return candidate
    return None


def running_in_venv(venv: Path) -> bool:
    try:
        return Path(sys.executable).resolve().is_relative_to(venv.resolve())
    except (OSError, ValueError):
        return False


def interpreter_hint() -> str:
    """给出「应该用哪个解释器」的可直接复制命令。"""
    venv = project_venv()
    lines = []
    if venv and not running_in_venv(venv):
        python_bin = (venv / "Scripts" / "python.exe") if sys.platform == "win32" \
            else (venv / "bin" / "python")
        lines.append(f"检测到本工程自带虚拟环境：{venv}")
        lines.append("  请改用：")
        lines.append(f'    "{python_bin}" server.py --http --port 8000')
    lines.append(f"当前解释器：{sys.executable}")
    lines.append("或给当前解释器安装依赖：")
    lines.append("  python -m pip install -r requirements.txt")
    return "\n".join(lines)


def format_dependency_hint(missing: list) -> str:
    return (
        "[环境错误] 当前解释器缺少依赖：" + ", ".join(missing) +
        "\n" + interpreter_hint() +
        "\n环境自检（不需要依赖即可运行）：python server.py --check-env"
    )


def _module_status(name: str) -> str:
    try:
        if importlib.util.find_spec(name) is None:
            return "缺失"
    except (ImportError, ValueError):
        return "缺失"
    try:
        return f"OK（{distribution_version(name)}）"
    except PackageNotFoundError:
        return "OK"


def _file_status(path: Path) -> str:
    if not path.exists():
        return f"未找到（{path}）"
    if path.is_file():
        return f"OK（{path.name}, {path.stat().st_size / 1024:.0f} KB）"
    files = sorted(path.glob("*.txt")) + sorted(path.glob("*.kml")) + sorted(path.glob("*.pth"))
    if not files:
        return f"空目录（{path}）"
    names = ", ".join(p.name for p in files[:6])
    return f"OK（{path}，{len(files)} 个文件：{names}）"


def check_environment() -> int:
    """打印环境体检报告，返回 0 表示依赖齐备，1 表示缺依赖。"""
    print("=" * 72)
    print("Shihua VOC MCP 环境自检")
    print("=" * 72)
    print(f"解释器      : {sys.executable}")
    print(f"Python 版本 : {sys.version.split()[0]} ({sys.platform})")
    print(f"工程根目录  : {PROJECT_ROOT}")

    print("\n[依赖]")
    for name in CORE_MODULES:
        print(f"  {name:<12} : {_module_status(name)}   （服务启动必需）")
    for name in FEATURE_MODULES:
        print(f"  {name:<12} : {_module_status(name)}")

    print("\n[数据与模型]")
    print(f"  data/   : {_file_status(PROJECT_ROOT / 'data')}")
    print(f"  models/ : {_file_status(PROJECT_ROOT / 'models')}")

    missing = missing_modules()
    print("\n[结论]")
    if not missing:
        print("  依赖齐备，可以启动：python server.py --http --port 8000")
        return 0
    core_missing = [m for m in missing if m in CORE_MODULES]
    print("  缺少依赖：" + ", ".join(missing))
    if core_missing:
        print("  服务无法启动，请换解释器或安装依赖：")
    else:
        print("  服务可以启动，但缺失的库会让对应功能不可用（如 torch → 无法做溯源分析）。")
    print(interpreter_hint())
    return 1


__all__ = [
    "CORE_MODULES", "FEATURE_MODULES", "PROJECT_ROOT",
    "missing_modules", "project_venv", "running_in_venv", "interpreter_hint",
    "format_dependency_hint", "check_environment",
]

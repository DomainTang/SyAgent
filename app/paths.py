# -*- coding: utf-8 -*-
"""统一路径配置：数据 / 模型 / 输出目录一律按此定位，便于跨机器与容器部署。

解析优先级（先命中先用）：
1. 环境变量 ``SHIHUA_DATA_DIR`` / ``SHIHUA_MODELS_DIR`` / ``SHIHUA_OUTPUT_DIR``；
2. 源码/克隆目录下的 ``data`` / ``models`` / ``output``（``python server.py`` 运行）；
3. 已安装包的所在目录（``pip install`` / ``uvx`` 后 data、models 随包分发）；
4. 当前工作目录及其上三级（平台通常在仓库目录里拉起子进程）。

全部返回绝对路径：MCP 服务由客户端或平台拉起时，工作目录未必是仓库根目录，
用相对路径会直接读不到模型文件（历史上踩过这个坑）。
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MODEL_URL_ENV = "SHIHUA_MODEL_URL"

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent


def _candidate_roots() -> list[Path]:
    """按“源码目录 → 安装目录 → 工作目录”的顺序给出候选根目录。"""
    roots = [PROJECT_ROOT, APP_DIR.parent]
    cwd = Path.cwd().resolve()
    roots.append(cwd)
    roots.extend(list(cwd.parents)[:3])
    return roots


def _resolve_dir(env_names: tuple[str, ...], dirname: str) -> Path:
    for env_name in env_names:
        raw = os.getenv(env_name)
        if raw:
            return Path(raw).expanduser().resolve()
    for root in _candidate_roots():
        candidate = root / dirname
        if candidate.is_dir():
            return candidate
    return PROJECT_ROOT / dirname


DATA_DIR = _resolve_dir(("SHIHUA_DATA_DIR",), "data")
MODELS_DIR = _resolve_dir(("SHIHUA_MODELS_DIR",), "models")
OUTPUT_DIR = _resolve_dir(("SHIHUA_OUTPUT_DIR",), "output")

# 主要运行文件
CORPUS_FILE = DATA_DIR / "语料库带经维度2020-2025.txt"
KML_FILE = DATA_DIR / "安庆监测点位及分区 202403.kml"
MODEL_FILE = MODELS_DIR / "voc_model.pth"
MODEL_RETRAINED_FILE = MODELS_DIR / "voc_model_retrained.pth"


def ensure_dirs() -> None:
    """确保数据 / 模型 / 输出目录存在（数据与模型通常由仓库自带或外部挂载）。"""
    for directory in (DATA_DIR, MODELS_DIR, OUTPUT_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def find_model_path() -> Path:
    """优先使用重训权重 ``voc_model_retrained.pth``，其次 ``voc_model.pth``。"""
    for candidate in (MODEL_RETRAINED_FILE, MODEL_FILE):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"未在 {MODELS_DIR} 下找到 voc_model_retrained.pth / voc_model.pth；"
        "请把模型权重放入 models/，用 SHIHUA_MODELS_DIR 指向模型目录，"
        f"或设置 {MODEL_URL_ENV} 让服务启动时自动下载"
    )


def download_model(url: str, target: Optional[Path] = None, timeout: float = 300.0) -> Path:
    """把权重下载到 ``MODELS_DIR``（先写临时文件再改名，避免半截文件被加载）。"""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    if target is None:
        name = Path(url.split("?")[0]).name
        target = MODELS_DIR / (name if name.endswith(".pth") else MODEL_RETRAINED_FILE.name)
    request = urllib.request.Request(url, headers={"User-Agent": "shihua-mcp/0.3"})
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".part",
                                    dir=str(target.parent))
    os.close(fd)  # 必须先关掉临时文件句柄，否则 Windows 上无法改名/删除
    tmp = Path(tmp_name)
    try:
        logger.info("正在下载模型权重：%s -> %s", url, target)
        with urllib.request.urlopen(request, timeout=timeout) as resp, open(tmp, "wb") as fp:
            shutil.copyfileobj(resp, fp)
        if tmp.stat().st_size == 0:
            raise OSError(f"下载到的文件为空: {url}")
        tmp.replace(target)
        logger.info("模型权重下载完成（%.1f MB）: %s", target.stat().st_size / 1024 / 1024, target)
        return target
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def ensure_model_file(model_url: Optional[str] = None) -> Path:
    """返回可用权重路径；本地缺失且配置了 ``SHIHUA_MODEL_URL`` 时自动下载。"""
    try:
        return find_model_path()
    except FileNotFoundError:
        url = model_url or os.getenv(MODEL_URL_ENV)
        if not url:
            raise
        return download_model(url)


def new_run_dir(output_root: str | os.PathLike | None = None) -> Path:
    """创建本次运行的输出目录 ``<output_root>/run_YYYYmmdd_HHMMSS``。

    同一秒内多次分析时自动追加序号，保证每次运行的产物不会互相覆盖。
    """
    root = Path(output_root or OUTPUT_DIR)
    base = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    for attempt in range(1, 1000):
        candidate = root / (base if attempt == 1 else f"{base}_{attempt}")
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError(f"同一秒内运行次数过多，无法创建输出目录: {root}")

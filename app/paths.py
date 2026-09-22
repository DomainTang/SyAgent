# -*- coding: utf-8 -*-
"""统一路径配置：数据 / 模型 / 输出目录一律按此定位，便于跨机器与容器部署。

解析优先级（先命中先用）：
1. 环境变量 ``SHIHUA_DATA_DIR`` / ``SHIHUA_MODELS_DIR`` / ``SHIHUA_OUTPUT_DIR``；
2. 仓库根目录下的 ``data`` / ``models`` / ``output``（克隆仓库后直接运行）；
3. 当前工作目录下的同名目录（以 wheel / uvx 方式安装后运行）。

全部返回绝对路径：MCP 服务由客户端或平台拉起时，工作目录未必是仓库根目录，
用相对路径会直接读不到模型文件（历史上踩过这个坑）。
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent


def _resolve_dir(env_names: tuple[str, ...], dirname: str) -> Path:
    for env_name in env_names:
        raw = os.getenv(env_name)
        if raw:
            return Path(raw).expanduser().resolve()
    repo_candidate = PROJECT_ROOT / dirname
    if repo_candidate.is_dir():
        return repo_candidate
    cwd_candidate = Path.cwd() / dirname
    if cwd_candidate.is_dir():
        return cwd_candidate
    return repo_candidate


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
        "请把模型权重放入 models/，或用 SHIHUA_MODELS_DIR 指向模型目录"
    )


def new_run_dir(output_root: str | os.PathLike | None = None) -> Path:
    """创建本次运行的输出目录 ``<output_root>/run_YYYYmmdd_HHMMSS``。"""
    root = Path(output_root or OUTPUT_DIR)
    run_dir = root / datetime.now().strftime("run_%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir

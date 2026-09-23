# -*- coding: utf-8 -*-
"""数据获取：内置测试文本抽样 + 仿真数据生成（MCP 工具 `get-monitoring-data` 的数据层）。

两种来源：
1. ``SAMPLE``：从 ``data/测试文本*.txt`` 随机抽取若干文件、每个文件抽取若干行，
   这些行本身就是模型可消费的样本行（``传感器030，位置为生产指挥中心，风速为2级，风向为东北风``）；
2. ``SIMULATED``：由 ``app/simulated_data.py`` 生成一整批厂区多源监测数据，
   恒定带 SIMULATED 标记，供联调、演示与故障演练使用。

``acquire_data()`` 是两者的统一入口，返回结构固定的"数据信封"，两个 MCP 工具共用：
`get-monitoring-data` 直接返回它，`analyze-source` 在不传数据时用它取一批数据。
"""
from __future__ import annotations

import glob
import logging
import os
import random
from pathlib import Path
from typing import Iterable, Optional

from . import monitoring_text, paths, simulated_data

logger = logging.getLogger(__name__)

SOURCE_SAMPLE = "sample"
SOURCE_SIMULATED = "simulated"
SOURCE_AUTO = "auto"
SOURCE_VALUES = (SOURCE_AUTO, SOURCE_SAMPLE, SOURCE_SIMULATED)


# ---------------------------------------------------------------- 测试文本抽样
def discover_sample_files(data_dir: Optional[str | os.PathLike] = None) -> list[str]:
    """返回样本目录下的候选 txt 文件（按可复现顺序排序）。"""
    directory = Path(data_dir or paths.DATA_DIR)
    return sorted(glob.glob(str(directory / "*.txt")))


def is_sample_line(line: str) -> bool:
    """样本行特征：同时含“传感器”与“位置为”（与模型输入格式一致）。"""
    return ("传感器" in line) and ("位置为" in line)


def has_sample_lines(file_path) -> bool:
    """文件里是否至少有一条有效样本行（不做抽样，便于复现随机序列）。"""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fp:
            return any(is_sample_line(line.strip()) for line in fp)
    except OSError as exc:
        logger.warning("读取样本文件失败（%s）: %s", file_path, exc)
        return False


def read_sample_lines(file_path, max_lines: Optional[int] = None,
                      rng: Optional[random.Random] = None) -> list:
    """读取文件中的有效样本行；保持文件内原始顺序。

    ``max_lines``：>0 抽取该行数；<=0 读取全部；None 随机抽取 5~20 行。
    """
    rng = rng or random.Random()
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fp:
            lines = [ln.strip() for ln in fp if ln.strip()]
    except OSError as exc:
        logger.warning("读取样本文件失败（%s）: %s", file_path, exc)
        return []

    valid = [ln for ln in lines if is_sample_line(ln)]
    if not valid:
        return []
    if max_lines is None:
        upper = min(20, len(valid))
        lower = min(5, upper)
        count = rng.randint(lower, upper)
    elif max_lines <= 0:
        count = len(valid)
    else:
        count = min(int(max_lines), len(valid))
    return [valid[i] for i in sorted(rng.sample(range(len(valid)), count))]


def pick_sample_lines(data_dir: Optional[str | os.PathLike] = None, files: int = 2,
                      max_lines: int = 12, seed: Optional[int] = None,
                      rng: Optional[random.Random] = None) -> dict:
    """随机挑选若干样本文件并抽取样本行。

    返回 ``{"lines": [...], "sources": [{"file": 文件名, "path": 绝对路径, "lines": n}]}``。
    """
    rng = rng or random.Random(seed)
    candidates = [p for p in discover_sample_files(data_dir) if has_sample_lines(p)]
    if not candidates:
        return {"lines": [], "sources": []}

    count = len(candidates) if not files or files <= 0 else min(int(files), len(candidates))
    chosen = rng.sample(candidates, count) if count < len(candidates) else list(candidates)

    lines: list = []
    sources: list = []
    for path in chosen:
        picked = read_sample_lines(path, max_lines=max_lines, rng=rng)
        if not picked:
            continue
        lines.extend(picked)
        sources.append({"file": os.path.basename(path), "path": path, "lines": len(picked)})
    return {"lines": lines, "sources": sources}


def build_sample_text(lines: Iterable[str], sources: Optional[list] = None) -> str:
    """把样本行渲染成带【数据来源】头的文本（可再次喂给 analyze-source）。"""
    head = [
        f"【数据来源】{monitoring_text.data_source_label(monitoring_text.DATA_SOURCE_SAMPLE)}",
        f"【数据说明】{monitoring_text.SAMPLE_NOTICE}",
    ]
    if sources:
        batch = "、".join(f"{s['file']}（{s['lines']} 行）" for s in sources)
        head.append(f"【数据批次】样本文件 {batch}")
    return "\n".join(head) + "\n\n" + "\n".join(lines)


# ---------------------------------------------------------------- 统一取数入口
def acquire_data(source: str = SOURCE_AUTO, files: int = 2, max_lines: int = 12,
                 scenario: Optional[str] = None, point_count: int = 6,
                 seed: Optional[int] = None) -> dict:
    """获取一批监测数据，返回统一信封（恒定带 data_source 标记）。"""
    mode = (source or SOURCE_AUTO).strip().lower()
    if mode not in SOURCE_VALUES:
        return {
            "status": "error",
            "error": f"未知数据来源 {source!r}，可选值：{' / '.join(SOURCE_VALUES)}",
        }

    if mode == SOURCE_AUTO:
        mode = (SOURCE_SAMPLE if any(has_sample_lines(p) for p in discover_sample_files())
                else SOURCE_SIMULATED)

    if mode == SOURCE_SAMPLE:
        picked = pick_sample_lines(files=files, max_lines=max_lines, seed=seed)
        if not picked["lines"]:
            return {
                "status": "error",
                "data_source": monitoring_text.DATA_SOURCE_SAMPLE,
                "data_source_label": monitoring_text.data_source_label(
                    monitoring_text.DATA_SOURCE_SAMPLE),
                "error": f"未在 {paths.DATA_DIR} 下找到含“传感器…，位置为…”的样本行",
            }
        logger.info("从 %d 个测试文本中抽取 %d 行样本",
                    len(picked["sources"]), len(picked["lines"]))
        return {
            "status": "ok",
            "source": SOURCE_SAMPLE,
            "data_source": monitoring_text.DATA_SOURCE_SAMPLE,
            "data_source_label": monitoring_text.data_source_label(
                monitoring_text.DATA_SOURCE_SAMPLE),
            "notice": monitoring_text.SAMPLE_NOTICE,
            "sample_lines": picked["lines"],
            "text": build_sample_text(picked["lines"], picked["sources"]),
            "monitoring": None,
            "sources": picked["sources"],
            "seed": seed,
        }

    payload = simulated_data.generate_monitoring_data(
        seed=seed, scenario=scenario, point_count=point_count)
    text = monitoring_text.format_monitoring_text(
        {k: payload[k] for k in ("气体浓度", "气象", "设备隐患台账", "厂区布置") if k in payload},
        monitoring_text.DATA_SOURCE_SIMULATED,
        simulation_id=payload.get("simulation_id"),
        generated_at=payload.get("generated_at"),
        scenario=payload.get("scenario"),
    )
    return {
        "status": "ok",
        "source": SOURCE_SIMULATED,
        "data_source": monitoring_text.DATA_SOURCE_SIMULATED,
        "data_source_label": monitoring_text.data_source_label(
            monitoring_text.DATA_SOURCE_SIMULATED),
        "notice": monitoring_text.SIMULATION_NOTICE,
        "sample_lines": simulated_data.build_sample_lines(payload),
        "text": text,
        "monitoring": payload,
        "sources": [],
        "simulation_id": payload.get("simulation_id"),
        "scenario": payload.get("scenario"),
        "scenario_label": payload.get("scenario_label"),
        "seed": seed,
    }


__all__ = [
    "SOURCE_SAMPLE", "SOURCE_SIMULATED", "SOURCE_AUTO", "SOURCE_VALUES",
    "discover_sample_files", "is_sample_line", "has_sample_lines", "read_sample_lines",
    "pick_sample_lines", "build_sample_text", "acquire_data",
]

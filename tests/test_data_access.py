# -*- coding: utf-8 -*-
"""数据获取自检：测试文本抽样、仿真数据生成、数据信封与来源标记。

运行方式（二选一）:
    python tests/test_data_access.py
    pytest tests/test_data_access.py
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import monitoring_text as mt
from app import sample_data as sdata
from app import simulated_data as sim


# ---------------------------------------------------------------- 测试文本抽样
def test_sample_files_and_lines_are_discovered():
    files = sdata.discover_sample_files()
    assert files, "data/ 下应有内置测试文本"
    usable = [p for p in files if sdata.has_sample_lines(p)]
    assert usable, "测试文本里应有“传感器…，位置为…”样本行"
    for line in sdata.read_sample_lines(usable[0], max_lines=3):
        assert sdata.is_sample_line(line)


def test_max_lines_semantics():
    first = [p for p in sdata.discover_sample_files() if sdata.has_sample_lines(p)][0]
    assert len(sdata.read_sample_lines(first, max_lines=2)) <= 2
    assert len(sdata.read_sample_lines(first, max_lines=0)) >= 1  # <=0 读取全部
    picked = sdata.read_sample_lines(first, max_lines=None)      # None 随机 5~20
    assert 1 <= len(picked) <= 20


def test_pick_sample_lines_respects_file_count_and_seed():
    picked = sdata.pick_sample_lines(files=1, max_lines=3, seed=7)
    assert picked["lines"] and len(picked["sources"]) == 1
    assert picked["sources"][0]["lines"] == len(picked["lines"])
    assert os.path.basename(picked["sources"][0]["path"]) == picked["sources"][0]["file"]

    again = sdata.pick_sample_lines(files=1, max_lines=3, seed=7)
    assert again["lines"] == picked["lines"], "同一随机种子应可复现"


def test_sample_text_carries_source_header():
    envelope = sdata.acquire_data(source="sample", seed=3, max_lines=3)
    assert envelope["status"] == "ok"
    assert envelope["data_source"] == mt.DATA_SOURCE_SAMPLE
    assert envelope["text"].startswith("【数据来源】")
    assert "SAMPLE" in envelope["text"]
    assert envelope["sources"] and envelope["sample_lines"]


def test_auto_prefers_bundled_sample_files():
    envelope = sdata.acquire_data(source="auto", seed=1, max_lines=2)
    assert envelope["status"] == "ok"
    assert envelope["data_source"] == mt.DATA_SOURCE_SAMPLE


def test_unknown_source_is_rejected():
    envelope = sdata.acquire_data(source="file")
    assert envelope["status"] == "error" and "未知数据来源" in envelope["error"]


# ---------------------------------------------------------------- 仿真数据
def test_simulated_data_passes_all_scenarios():
    units: dict = {}
    for spec in sim.SENSOR_TYPES:
        units.setdefault(spec["气体种类"], set()).add(spec["单位"])

    for scenario in sim.SCENARIOS:
        for seed in range(20):
            data = sim.generate_monitoring_data(seed=seed, scenario=scenario)
            assert data["scenario"] == scenario
            assert data["气体浓度"] and data["气象"] and data["厂区布置"]
            for reading in data["气体浓度"]:
                assert reading["单位"] in units[reading["气体种类"]], reading
                assert reading["浓度值"] >= 0


def test_simulated_timestamps_are_sane():
    now = datetime(2026, 9, 22, 14, 30, 0)
    for seed in range(30):
        data = sim.generate_monitoring_data(seed=seed, now=now)
        assert data["generated_at"] == now.strftime("%Y-%m-%d %H:%M:%S")
        for reading in data["气体浓度"]:
            hh, mm, ss = (int(x) for x in reading["时间戳"].split(":"))
            stamp = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
            assert now - timedelta(minutes=10) <= stamp <= now, (seed, reading["时间戳"])


def test_simulated_payload_is_always_marked():
    data = sim.generate_monitoring_data(seed=3)
    assert data["data_source"] == mt.DATA_SOURCE_SIMULATED
    assert data["simulation_id"].startswith("SIM-")
    assert "SIMULATED" in data["notice"]

    envelope = sdata.acquire_data(source="simulated", seed=3, scenario="leak", point_count=4)
    assert envelope["status"] == "ok"
    assert envelope["data_source"] == mt.DATA_SOURCE_SIMULATED
    assert envelope["simulation_id"] == envelope["monitoring"]["simulation_id"]
    assert "【数据来源】" in envelope["text"] and "SIMULATED" in envelope["text"]
    assert len(envelope["sample_lines"]) == len(envelope["monitoring"]["气体浓度"]) == 4


def test_sample_lines_keep_corpus_format():
    data = sim.generate_monitoring_data(seed=5, scenario="leak")
    lines = sim.build_sample_lines(data)
    assert lines and len(lines) == len(data["气体浓度"])
    for line in lines:
        assert "传感器" in line and "位置为" in line and "风速为" in line and "风向为" in line


# ---------------------------------------------------------------- 文本渲染
def test_text_render_always_declares_source():
    data = sim.generate_monitoring_data(seed=21, scenario="leak")
    text = mt.format_monitoring_text(
        {k: data[k] for k in ("气体浓度", "气象", "设备隐患台账", "厂区布置")},
        mt.DATA_SOURCE_SIMULATED, data["simulation_id"], data["generated_at"], data["scenario"])
    assert text.startswith("【数据来源】")
    assert "SIMULATED" in text and "严禁用于现场处置决策" in text
    assert "m/s" in text and "℃" in text

    sample_text = sdata.build_sample_text(["传感器141，位置为生产指挥中心"], [])
    assert sample_text.startswith("【数据来源】") and "SAMPLE" in sample_text
    assert mt.notice_for(mt.DATA_SOURCE_USER) == mt.USER_NOTICE


def test_run_output_dir_is_unique_per_analysis():
    import tempfile

    from app import paths

    with tempfile.TemporaryDirectory() as tmp:
        first = paths.new_run_dir(tmp)
        second = paths.new_run_dir(tmp)
        assert first != second, "同一秒内的两次分析不得共用输出目录"
        assert first.is_dir() and second.is_dir()


def _run_all():
    funcs = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in funcs:
        try:
            fn()
            print(f"[PASS] {name}")
        except Exception as exc:  # noqa: BLE001 - 自检脚本，失败即报告
            failed += 1
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(funcs) - failed}/{len(funcs)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())

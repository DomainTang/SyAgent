# -*- coding: utf-8 -*-
"""数据层自检：仿真数据结构 / 单位合法性 / 校验阻塞逻辑 / 数据来源标记不得丢失。

运行方式（二选一）:
    python tests/test_data_layer.py
    pytest tests/test_data_layer.py
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import realtime_data as rd
from app import simulated_data as sd


# ---------------------------------------------------------------- 仿真数据
def test_simulated_data_passes_validation_for_all_scenarios():
    for scenario in sd.SCENARIOS:
        for seed in range(30):
            data = sd.generate_monitoring_data(seed=seed, scenario=scenario)
            report = rd.validate_monitoring_data(data, rd.DATA_SOURCE_SIMULATED)
            assert report["proceed"], (scenario, seed, report["blocking"])
            assert report["status"] in ("ok", "ok_degraded")


def test_simulated_units_match_gas_type():
    for seed in range(50):
        for r in sd.generate_monitoring_data(seed=seed)["气体浓度"]:
            assert r["单位"] in rd.GAS_UNITS[r["气体种类"]], r


def test_simulated_values_are_non_negative_and_timestamps_sane():
    now = datetime(2026, 9, 22, 14, 30, 0)
    for seed in range(50):
        data = sd.generate_monitoring_data(seed=seed, now=now)
        assert data["generated_at"] == now.strftime("%Y-%m-%d %H:%M:%S")
        for r in data["气体浓度"]:
            assert r["浓度值"] >= 0
            hh, mm, ss = (int(x) for x in r["时间戳"].split(":"))
            ts = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
            assert now - timedelta(minutes=10) <= ts <= now, (seed, r["时间戳"])


def test_simulated_payload_is_always_marked():
    data = sd.generate_monitoring_data(seed=3)
    assert data["data_source"] == rd.DATA_SOURCE_SIMULATED
    assert data["simulation_id"].startswith("SIM-")
    assert "SIMULATED" in data["notice"]


def test_sample_lines_keep_corpus_format():
    data = sd.generate_monitoring_data(seed=5, scenario="leak")
    lines = sd.build_sample_lines(data)
    assert lines and len(lines) == len(data["气体浓度"])
    for line in lines:
        assert "传感器" in line and "位置为" in line and "风速为" in line and "风向为" in line


# ---------------------------------------------------------------- 校验逻辑
def test_validator_blocks_missing_core_blocks():
    report = rd.validate_monitoring_data({})
    assert not report["proceed"] and report["status"] == "insufficient_data"
    assert any("气体传感器实时读数" in b for b in report["blocking"])
    assert any("气象" in b for b in report["blocking"])


def test_validator_blocks_illegal_unit():
    data = sd.generate_monitoring_data(seed=11, scenario="leak")
    bad = dict(data)
    bad["气体浓度"] = [dict(r) for r in data["气体浓度"]]
    bad["气体浓度"][0]["单位"] = "ppm" if bad["气体浓度"][0]["气体种类"] == "可燃气体" else "%LEL"
    report = rd.validate_monitoring_data(bad)
    assert not report["proceed"]
    assert any("单位不匹配" in b for b in report["blocking"])


def test_validator_blocks_negative_and_missing_timestamp():
    data = sd.generate_monitoring_data(seed=12)
    bad = dict(data)
    bad["气体浓度"] = [dict(data["气体浓度"][0], 浓度值=-5, 时间戳="")]
    report = rd.validate_monitoring_data(bad)
    assert not report["proceed"]
    assert any("为负" in b for b in report["blocking"])
    assert any("时间戳" in b for b in report["blocking"])


def test_validator_rejects_simulated_data_claimed_as_real():
    data = sd.generate_monitoring_data(seed=13)
    report = rd.validate_monitoring_data(data, rd.DATA_SOURCE_REAL)
    assert not report["proceed"]
    assert any("数据来源标记不一致" in b for b in report["blocking"])


def test_degraded_when_ledger_and_layout_missing():
    data = sd.generate_monitoring_data(seed=14)
    partial = {k: data[k] for k in ("气体浓度", "气象")}
    report = rd.validate_monitoring_data(partial)
    assert report["proceed"] and report["status"] == "ok_degraded"
    assert report["confidence_penalty"] > 0


def test_missing_blocks_use_canonical_names():
    rep = rd.validate_monitoring_data({})
    assert rep["missing_blocks"] == ["气体浓度", "气象", "设备隐患台账", "厂区布置"]
    partial = sd.generate_monitoring_data(seed=31)
    rep = rd.validate_monitoring_data({k: partial[k] for k in ("气体浓度", "气象")})
    assert rep["missing_blocks"] == ["设备隐患台账", "厂区布置"]


# ---------------------------------------------------------------- 数据来源透传
def test_text_render_always_declares_source():
    data = sd.generate_monitoring_data(seed=21, scenario="leak")
    text = rd.format_monitoring_text(
        {k: data[k] for k in ("气体浓度", "气象", "设备隐患台账", "厂区布置")},
        rd.DATA_SOURCE_SIMULATED, data["simulation_id"], data["generated_at"], data["scenario"])
    assert text.startswith("【数据来源】")
    assert "SIMULATED" in text
    assert "严禁用于现场处置决策" in text
    assert "m/s" in text and "℃" in text and ("%LEL" in text or "ppm" in text)


def test_acquire_modes():
    sim = rd.acquire_monitoring_data(mode="simulate", seed=8)
    assert sim["status"] == "ok" and sim["data_source"] == rd.DATA_SOURCE_SIMULATED
    assert "SIMULATED" in sim["text"]

    off = rd.acquire_monitoring_data(mode="off")
    assert off["status"] == "no_data" and off["data_source"] == rd.DATA_SOURCE_UNAVAILABLE
    assert off["monitoring"] is None and not off["validation"]["proceed"]

    real_payload = sd.generate_monitoring_data(seed=9)
    real_payload["data_source"] = rd.DATA_SOURCE_REAL
    real_payload.pop("simulation_id")
    real = rd.acquire_monitoring_data(mode="real", provider=lambda: real_payload)
    assert real["status"] == "ok" and real["data_source"] == rd.DATA_SOURCE_REAL
    assert "SIMULATED" not in real["text"]

    def boom():
        raise RuntimeError("GDS 网关超时")

    failed = rd.acquire_monitoring_data(mode="real", provider=boom)
    assert failed["status"] == "no_data" and failed["data_source"] == rd.DATA_SOURCE_UNAVAILABLE
    assert "GDS 网关超时" in failed["error"]

    # real 模式下即使没配数据源，也不允许静默回落到仿真
    empty = rd.acquire_monitoring_data(mode="real")
    assert empty["data_source"] == rd.DATA_SOURCE_UNAVAILABLE


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

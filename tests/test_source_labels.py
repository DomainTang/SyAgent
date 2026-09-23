# -*- coding: utf-8 -*-
"""端到端自检：数据来源标记在 Excel / 图表 / 烟羽地图 / 结果文本里不得丢失。

运行方式（二选一）:
    python tests/test_source_labels.py
    pytest tests/test_source_labels.py
"""
import os
import sys
import tempfile

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import analysis, charts, monitoring_text as mt, paths  # noqa: E402
from app.plume_visualization import create_plume_visualization  # noqa: E402

SIMULATED_LABEL = mt.DATA_SOURCE_LABEL[mt.DATA_SOURCE_SIMULATED]
SAMPLE_LABEL = mt.DATA_SOURCE_LABEL[mt.DATA_SOURCE_SAMPLE]

KML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<kml><Document>
  <Placemark><name>监测点001</name><Point><coordinates>117.0300,30.5300,0</coordinates></Point></Placemark>
  <Placemark><name>装置区A</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
  117.0290,30.5290,0 117.0310,30.5290,0 117.0310,30.5310,0 117.0290,30.5310,0 117.0290,30.5290,0
  </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
</Document></kml>
"""


def _fake_records(data_source):
    return [{
        "index": 1,
        "input_text": "传感器141，位置为生产指挥中心，风速为2级，风向为东北风",
        "processed_text": "传感器141，位置为生产指挥中心",
        "predicted_source": "乙苯装置（117.02958715,30.52963611）",
        "predicted_source_name": "乙苯装置",
        "confidence": 0.87,
        "confidence_level": "高",
        "top3": [{"source": "乙苯装置", "confidence": 0.87}],
        "longitude": "117.02958715",
        "latitude": "30.52963611",
        "wind_speed": "2",
        "wind_direction": "东北风",
        "true_label": None,
        "data_source": data_source,
        "status": "success",
    }]


def test_excel_carries_data_source_column():
    rows = analysis.result_rows(_fake_records(mt.DATA_SOURCE_SIMULATED), SIMULATED_LABEL)
    assert rows[0]["数据来源"] == SIMULATED_LABEL
    assert rows[0]["疑似源分析"].endswith("(117.02958715,30.52963611)")
    assert "乙苯装置（117.02958715" not in rows[0]["疑似源分析"], "坐标不应重复出现两次"

    rows = analysis.result_rows(_fake_records(mt.DATA_SOURCE_SAMPLE), SAMPLE_LABEL)
    assert rows[0]["数据来源"] == SAMPLE_LABEL


def test_chart_watermark_added_only_for_simulated():
    fig, _ = plt.subplots()
    charts.add_simulation_watermark(fig)
    assert any("SIMULATED" in t.get_text() for t in fig.texts)
    plt.close(fig)

    with tempfile.TemporaryDirectory() as tmp:
        for simulated in (False, True):
            out = os.path.join(tmp, f"chart_{simulated}.png")
            png = charts.plot_source_distribution(_fake_records(None), out, simulated=simulated)
            assert png and png[:8] == b"\x89PNG\r\n\x1a\n"
            assert os.path.exists(out)


def test_plume_html_banner_only_when_data_source_given():
    with tempfile.TemporaryDirectory() as tmp:
        kml_path = os.path.join(tmp, "points.kml")
        with open(kml_path, "w", encoding="utf-8") as fp:
            fp.write(KML_TEMPLATE)

        excel_path = os.path.join(tmp, "预测结果.xlsx")
        pd.DataFrame(analysis.result_rows(_fake_records(None), SIMULATED_LABEL)).to_excel(
            excel_path, index=False)

        plain_html = os.path.join(tmp, "plain.html")
        create_plume_visualization(kml_path, excel_path, plain_html, 0)
        with open(plain_html, encoding="utf-8") as fp:
            assert "严禁用于现场处置决策" not in fp.read()

        banner_html = os.path.join(tmp, "banner.html")
        create_plume_visualization(kml_path, excel_path, banner_html, 0,
                                   data_source=SIMULATED_LABEL)
        with open(banner_html, encoding="utf-8") as fp:
            content = fp.read()
        assert "数据来源：" in content and "严禁用于现场处置决策" in content


def test_plume_index_page_carries_source_banner():
    with tempfile.TemporaryDirectory() as tmp:
        kml_path = os.path.join(tmp, "points.kml")
        with open(kml_path, "w", encoding="utf-8") as fp:
            fp.write(KML_TEMPLATE)
        excel_path = os.path.join(tmp, "预测结果.xlsx")
        pd.DataFrame(analysis.result_rows(_fake_records(None), SIMULATED_LABEL)).to_excel(
            excel_path, index=False)

        plume_dir = os.path.join(tmp, "plume")
        produced = analysis.generate_plume_maps(kml_path, excel_path, plume_dir, max_events=1,
                                                data_source_label_text=SIMULATED_LABEL)
        index_path = os.path.join(plume_dir, "index.html")
        assert index_path in produced and os.path.exists(index_path), produced
        with open(index_path, encoding="utf-8") as fp:
            assert "SIMULATED" in fp.read()


def test_simulated_analysis_marks_source_everywhere():
    with tempfile.TemporaryDirectory() as tmp:
        payload = analysis.run_analysis(source="simulated", seed=42, output_root=tmp)
        assert payload["status"] == "success"
        assert payload["data_source"] == mt.DATA_SOURCE_SIMULATED
        assert payload["data_source_label"] == SIMULATED_LABEL
        assert "SIMULATED" in payload["text_report"]
        assert "严禁用于现场处置决策" in payload["text_report"]

        excel_path = payload["artifacts"]["excel"]
        assert excel_path and os.path.exists(excel_path)
        assert set(pd.read_excel(excel_path)["数据来源"]) == {SIMULATED_LABEL}

        assert [c["name"] for c in payload["artifacts"]["charts"]] == ["预测源分布_Top12", "置信度分布"]
        for chart in payload["artifacts"]["charts"]:
            assert os.path.exists(chart["path"])


def test_sample_analysis_uses_bundled_test_text():
    with tempfile.TemporaryDirectory() as tmp:
        payload = analysis.run_analysis(source="sample", seed=11, max_lines=4, output_root=tmp)
        assert payload["status"] == "success"
        assert payload["data_source"] == mt.DATA_SOURCE_SAMPLE
        assert payload["data_source_label"] == SAMPLE_LABEL
        assert payload["input"]["sources"], "应记录抽到的样本文件"
        assert "SAMPLE" in payload["text_report"]
        assert "严禁用于现场处置决策" not in payload["text_report"]
        assert set(pd.read_excel(payload["artifacts"]["excel"])["数据来源"]) == {SAMPLE_LABEL}


def test_analysis_inputs_are_interchangeable():
    from app import sample_data

    envelope = sample_data.acquire_data(source="simulated", seed=7, scenario="leak", point_count=4)
    for data in (envelope["text"], envelope["monitoring"], envelope["sample_lines"],
                 "传感器141，位置为生产指挥中心，风速为2级，风向为东北风"):
        inputs = analysis.resolve_inputs(data)
        assert inputs["sample_lines"], data
        assert all(analysis.is_sample_line(line) for line in inputs["sample_lines"])

    # 仿真文本回放后仍应识别为 SIMULATED，并保留批次号
    replayed = analysis.run_analysis(envelope["text"], seed=7)
    assert replayed["data_source"] == mt.DATA_SOURCE_SIMULATED
    assert replayed["simulation_id"] == envelope["simulation_id"]

    # 未声明来源的样本行归为 USER
    assert analysis.resolve_inputs("传感器141，位置为生产指挥中心")["data_source"] == mt.DATA_SOURCE_USER


def test_data_dirs_are_absolute():
    assert paths.DATA_DIR.is_absolute() and paths.MODELS_DIR.is_absolute()
    assert paths.OUTPUT_DIR.is_absolute()


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

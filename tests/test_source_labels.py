# -*- coding: utf-8 -*-
"""数据来源标记端到端自检：Excel 列、图表水印、烟羽地图横幅、结果文本。

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

from app import analysis, charts, paths, realtime_data  # noqa: E402
from app.plume_visualization import create_plume_visualization  # noqa: E402

SIMULATED_LABEL = realtime_data.DATA_SOURCE_LABEL[realtime_data.DATA_SOURCE_SIMULATED]

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
    rows = analysis.result_rows(_fake_records(realtime_data.DATA_SOURCE_SIMULATED), SIMULATED_LABEL)
    assert rows[0]["数据来源"] == SIMULATED_LABEL
    assert rows[0]["疑似源分析"].endswith("(117.02958715,30.52963611)")
    assert "乙苯装置（117.02958715" not in rows[0]["疑似源分析"], "坐标不应重复出现两次"


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


def test_analysis_marks_simulated_everywhere():
    """端到端：仿真数据一路跑到 Excel / 图表 / 文本，来源标记不得丢失。"""
    with tempfile.TemporaryDirectory() as tmp:
        payload = analysis.run_analysis(seed=42, output_root=tmp)
        assert payload["status"] == "success"
        assert payload["data_source"] == realtime_data.DATA_SOURCE_SIMULATED
        assert payload["data_source_label"] == SIMULATED_LABEL
        assert "SIMULATED" in payload["text_report"]
        assert "严禁用于现场处置决策" in payload["text_report"]

        excel_path = payload["artifacts"]["excel"]
        assert excel_path and os.path.exists(excel_path)
        df = pd.read_excel(excel_path)
        assert set(df["数据来源"]) == {SIMULATED_LABEL}

        chart_names = [c["name"] for c in payload["artifacts"]["charts"]]
        assert chart_names == ["预测源分布_Top12", "置信度分布"]
        for chart in payload["artifacts"]["charts"]:
            assert os.path.exists(chart["path"])

        # 输入文本里的【数据来源】行必须被识别，而不是被当成用户数据
        envelope = realtime_data.acquire_monitoring_data(seed=42)
        replayed = analysis.run_analysis(envelope["text"], seed=42, output_root=tmp)
        assert replayed["data_source"] == realtime_data.DATA_SOURCE_SIMULATED
        assert replayed["simulation_id"] == envelope["simulation_id"]


def test_analysis_inputs_are_interchangeable():
    envelope = realtime_data.acquire_monitoring_data(seed=7, scenario="leak", point_count=4)
    for data in (envelope["text"], envelope["monitoring"],
                 "\n".join(envelope["text"].split("【气体浓度】")[1].splitlines()[:2]),
                 "传感器141，位置为生产指挥中心，风速为2级，风向为东北风"):
        inputs = analysis.resolve_inputs(data)
        assert inputs["sample_lines"], data
        assert all(analysis.is_sample_line(line) for line in inputs["sample_lines"])


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

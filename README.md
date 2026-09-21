# 石化疑似源识别系统 · 命令行版（Shihua VOC CLI）

从原 Qt 图形界面工程中提取出**只做推理/可视化**的必要代码，重构为纯命令行自动流程：
程序自动扫描 `data/` 下的文本样本 → 随机挑选一个或多个文件并抽取样本行 → 加载模型批量预测
→ 在终端输出完整结果 → 自动绘制统计图并生成烟羽扩散可视化 HTML。

原工程（含 Qt 界面）不改动；本目录为独立新工程。

## 目录结构

```
ShihuaCLI/
├── app/
│   ├── cli.py                    # ★ 命令行主程序（本次重构新增）
│   ├── model_design.py           # 模型定义/分词器/语料分析（自原工程）
│   ├── plume_visualization.py    # 烟羽扩散可视化（自原工程）
│   └── paths.py                  # 目录配置
├── data/                         # 放语料库、KML、测试文本（按 data/说明.txt）
├── models/                       # 放 voc_model*.pth（按 models/说明.txt）
├── output/                       # 运行结果（自动生成 run_时间戳/）
├── requirements.txt
├── run_cli.bat / run_cli.sh      # 一键运行脚本
└── README.md
```

## 安装依赖

```bash
pip install -r requirements.txt
```

> 需要与原工程相同的 Python 环境（torch / pandas / matplotlib / folium 等），
> 若使用原工程的 .venv 可直接复用。

## 使用前准备

将原工程中的文件复制进来（或用参数直接指向原工程目录，无需复制）：

| 本工程位置 | 来源文件 | 说明 |
|---|---|---|
| data/ | 语料库带经维度2020-2025.txt | 疑似源坐标知识库（缺失时坐标用默认值） |
| data/ | 安庆监测点位及分区 202403.kml | 烟羽可视化必需 |
| data/ | 测试文本*.txt 等 | 任意含"传感器…，位置为…"行的文本 |
| models/ | voc_model_retrained.pth 或 voc_model.pth | 模型权重 |

## 运行

```bash
# 最基本：自动随机选文件与样本行
python app/cli.py

# 固定随机源、每个文件抽 12 行、只生成 3 张烟羽图
python app/cli.py --seed 42 --files 2 --max-lines 12 --plume-events 3

# 指定样本文件（不做随机文件选择），读取全部行
python app/cli.py --inputs data/测试文本2025-10-10.txt --max-lines 0

# 直接复用原工程数据/模型目录
python app/cli.py --data-dir D:/temp/pyproject/Shihua/data --models-dir D:/temp/pyproject/Shihua/models

# 生成后自动用浏览器打开烟羽总览页
python app/cli.py --open-browser
```

## 保留/输出的信息（与原 GUI 对应关系）

原 Qt 界面里的功能全部前移到终端与 output 目录：

| 原 Qt 界面内容 | 本 CLI 对应输出 |
|---|---|
| 模型预测页“批量输入” | 自动读取 data/ 随机文本，无需手动选择 |
| 结果表格（输入文本/预测源/置信度/Top3） | 终端逐条打印 + output/预测结果.xlsx |
| 置信度背景色分级（绿/黄/红） | 终端标注 高/中/低置信度 |
| 事件下拉框“事件N: 预测源” | 终端“事件列表”段落 |
| 统计图：预测源分布（Top12 柱状图） | output/预测源分布_Top12.png |
| 统计图：置信度分布（直方图） | output/置信度分布.png |
| 烟羽扩散可视化（溯源地图） | output/run_*/plume/ 下 event_*.html + index.html 总览 |
| 预测结果 Excel（含传感器信息/经纬度/风速风向/疑似源分析） | output/run_*/预测结果.xlsx |
| 状态栏消息（模型加载/批量完成/生成成功等） | 终端 [状态]/[输出]/[警告] 信息 |

样本行中若自带“疑似源为…”（如语料库文件），还会在终端对照显示真实标签与预测是否一致。

## 命令行参数

| 参数 | 默认 | 说明 |
|---|---|---|
| --data-dir | data/ | 样本/语料/KML 目录 |
| --models-dir | models/ | 模型目录 |
| --output-dir | output/ | 输出根目录 |
| --inputs | 无 | 指定样本文件（可多个），不指定则自动随机 |
| --files | 随机 1~3 | 随机抽取文件数 |
| --max-lines | 随机 5~20 | 每文件抽样行数；<=0 表示读取全部 |
| --plume-events | 5 | 生成烟羽事件数；<=0 表示全部 |
| --seed | 随机 | 随机种子（复现用） |
| --open-browser | 关 | 生成后打开浏览器 |
| --no-plume | 关 | 跳过烟羽可视化 |

## 输出样例（终端摘要）

```
[状态] 本次随机选中的样本文件：
  - 测试文本2025-10-10.txt  (抽取 8 行)
[状态] 模型加载成功: models/voc_model_retrained.pth
[1] 预测疑似源: 厂外商储罐区（117.018,30.534） | 置信度: 0.9876 (高置信度)
    预测Top-3: ...
[输出] 预测源分布图已保存: output/run_xxx/预测源分布_Top12.png
[输出] 事件1 烟羽可视化: output/run_xxx/plume/event_001.html
```

## 常见问题

- 未放模型：提示“未找到模型文件”，请把 voc_model*.pth 放入 models/ 或用 --models-dir 指向原工程。
- 中文标签乱码：控制台执行 `chcp 65001`，并确认系统装有黑体/雅黑字体。
- 烟羽图瓦片不显示：HTML 已生成，但底图瓦片需要联网，浏览器打开时联网即可。
自动兼容两类模型 checkpoint：
- 新格式：model_state_dict / tokenizer_state / label_mapping / model_args（原 V8 界面训练产物）
- 旧格式：model_state_dict / tokenizer(实例) / source2idx（历史脚本训练产物）
加载时自动识别，无需手动区分。

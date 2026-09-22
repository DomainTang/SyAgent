# 石化疑似源识别 MCP 服务（Shihua VOC MCP）

[魔搭（ModelScope）部署指南](./docs/model-scope-deploy.md)

把石化厂区 VOCs 泄漏的**数据生成**与**溯源分析**封装成两个标准 MCP 工具，供 Cursor、
Cherry Studio、通义灵码、Claude、Dify 等支持 MCP 的客户端通过 stdio 或
Streamable HTTP / SSE 调用。工程结构参照 `12306-mcp`：
一个服务入口、少量边界清晰的工具、可在魔搭 MCP 广场托管。

## 功能

| 工具 | 说明 |
| --- | --- |
| `get-monitoring-data` | 生成一批厂区多源监测数据（气体浓度 / 气象 / 设备隐患台账 / 厂区布置） |
| `analyze-source` | 对监测数据做疑似源溯源分析，并绘制统计图（预测源分布 Top12、置信度分布） |

`analyze-source` 的输出包含：

- 逐点位预测：疑似源、经纬度、置信度（高/中/低）、Top3 候选；
- 统计汇总：预测源分布、置信度均值/极值/分档；
- **绘图**：两张 PNG 统计图（MCP 图片内容内联返回，客户端可直接显示）+ 落盘文件；
- 可选：`plume_events > 0` 时逐事件生成烟羽扩散地图 HTML；
- 全流程产物：`output/run_YYYYmmdd_HHMMSS/预测结果.xlsx` 等。

## 目录结构

```
ShihuaCLI/
├── src/shihua_mcp/
│   └── server.py              # ★ MCP 服务入口：两个工具 + stdio/HTTP/SSE 传输
├── app/                       # 核心算法库（无 MCP 依赖，可单独复用）
│   ├── simulated_data.py      # 监测数据生成器（恒定带 SIMULATED 标记）
│   ├── realtime_data.py       # 数据契约 + 程序化校验 + 固定格式文本渲染
│   ├── predictor.py           # 模型加载与单条推理（兼容新旧 checkpoint）
│   ├── model_design.py        # 模型结构 / 字符分词器 / 语料库解析
│   ├── analysis.py            # 溯源分析流水线（输入归一化 → 推理 → 汇总 → 产物）
│   ├── charts.py              # 统计图绘制（返回 PNG 字节，仿真数据加水印）
│   ├── plume_visualization.py # 烟羽扩散可视化（folium 交互式地图）
│   └── paths.py               # 数据/模型/输出目录定位
├── data/                      # 语料库、KML 底图（见 data/说明.txt）
├── models/                    # voc_model*.pth（见 models/说明.txt）
├── output/                    # 运行产物，每次分析新建 run_时间戳/
├── tests/                     # 数据层 / 来源标记 / MCP 工具自检
├── docs/model-scope-deploy.md # 魔搭部署指南
├── Dockerfile
├── pyproject.toml / requirements.txt
└── README.md
```

## 安装

```bash
pip install -r requirements.txt      # 或：pip install -e .
```

## 使用前准备

| 位置 | 需要什么 | 缺失后果 |
| --- | --- | --- |
| `models/` | `voc_model_retrained.pth`（优先）或 `voc_model.pth` | 溯源分析无法执行 |
| `data/语料库带经维度2020-2025.txt` | 疑似源坐标知识库 | 坐标退化为默认值（不阻塞） |
| `data/安庆监测点位及分区 202403.kml` | 烟羽扩散底图 | 自动生成"示例点位底图"（名称带"示例"前缀） |

模型权重约 15 MB。部署到魔搭时请确保服务能读到权重：随仓库提交，或上传后用
`SHIHUA_MODELS_DIR` 指向挂载目录。

## 快速开始

自检（不启动服务，直接跑一遍取数 + 溯源 + 绘图）：

```bash
python -m shihua_mcp.server --self-test
```

本地 stdio 接入（MCP 客户端 / Cherry Studio / Cursor）：

```json
{
  "mcpServers": {
    "shihua-mcp": {
      "command": "python",
      "args": ["-m", "shihua_mcp.server"],
      "env": { "SHIHUA_DATA_MODE": "simulate" }
    }
  }
}
```

容器 / 云端用 HTTP + SSE：

```bash
python -m shihua_mcp.server --http --port 8000
# Streamable HTTP: POST http://127.0.0.1:8000/mcp
# SSE:             GET  http://127.0.0.1:8000/sse
```

部署到魔搭（ModelScope）MCP 广场时推荐 **Stdio 托管**（平台自动生成 SSE 地址），
创建页各项填法与三种部署方式的配置见 [魔搭部署指南](./docs/model-scope-deploy.md)。

## 工具返回内容

### `get-monitoring-data`

| 字段 | 说明 |
| --- | --- |
| `text` | 固定格式监测文本（首行【数据来源】），可直接作为 `analyze-source` 的输入 |
| `sample_lines` | 一行一条的样本行数组，也可直接传入 `analyze-source` |
| `monitoring` | 结构化数据：气体浓度 / 气象 / 设备隐患台账 / 厂区布置 |
| `validation` | 程序化齐备性结论：`proceed=False` 时不得输出溯源结论 |
| `data_source` | `SIMULATED` / `REAL` / `UNAVAILABLE` |

### `analyze-source`

返回三部分：**可读文本报告**（逐点位结果、源分布、置信度统计、产物清单）、
**两张统计图**（图片内容）、**结构化结果**（`results` / `summary` / `artifacts` /
`validation` / `data_source`）。`data` 参数可传固定格式监测文本、`monitoring`
结构化对象、样本行数组，或省略（自动取一批数据后直接分析）。

## 数据来源约定（不可绕过）

`data_source` 是强制字段，取值与含义：

| 取值 | 含义 | 触发条件 |
| --- | --- | --- |
| `SIMULATED` | 本地仿真数据 | `SHIHUA_DATA_MODE=simulate`（默认）或 `auto` 且未配现场数据源 |
| `REAL` | 现场实测数据 | `SHIHUA_DATA_MODE=real/auto` 且 `SHIHUA_REALTIME_ENDPOINT` 调用成功 |
| `UNAVAILABLE` | 未获取到数据 | `real` 模式取数失败，或 `off` 模式 |
| `USER` | 调用方直接给出的样本行 | `analyze-source` 收到未声明来源的样本行 |

三条硬约束：仿真数据的数据体恒定带 `simulation_id`；固定格式文本首行恒定打印
【数据来源】；统计图加水印、烟羽地图加横幅。代码里没有"去掉仿真标记"的开关——
下游是疏散范围与现场排查决策，把仿真值当实测值用一次，代价可能是人身伤害。

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MCP_TRANSPORT` | `stdio` | `stdio` / `http` / `sse`（等价于 `--transport`） |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | HTTP/SSE 监听地址与端口（云平台一般注入 `PORT`） |
| `MCP_HTTP_PATH` | `/mcp` | Streamable HTTP 路径 |
| `MCP_CORS_ORIGINS` | `*` | HTTP 模式允许的来源，公网部署请改成具体域名 |
| `SHIHUA_DATA_MODE` | `simulate` | `simulate` / `real` / `auto` / `off` |
| `SHIHUA_REALTIME_ENDPOINT` | 无 | 现场监测数据 HTTP 端点（返回一节 JSON） |
| `SHIHUA_DATA_DIR` / `SHIHUA_MODELS_DIR` / `SHIHUA_OUTPUT_DIR` | 仓库内 `data/` `models/` `output/` | 目录覆盖 |

接入现场数据示例：

```bash
export SHIHUA_REALTIME_ENDPOINT=http://10.0.0.20:8080/gds/realtime
export SHIHUA_DATA_MODE=real
python -m shihua_mcp.server
```

## 运行产物

```
output/run_20260922_183021/
├── 预测结果.xlsx             # 逐条预测结果（含「数据来源」列）
├── 预测源分布_Top12.png      # 统计图（同时以图片内容返回给客户端）
├── 置信度分布.png            # 统计图（同时以图片内容返回给客户端）
├── 示例点位底图.kml          # 现场 KML 缺失时的示例底图（非真实厂区坐标）
└── plume/
    ├── event_001.html        # 逐事件烟羽扩散地图
    └── index.html            # 事件总览页
```

## 自检

```bash
python tests/test_data_layer.py     # 仿真数据结构、单位合法性、校验阻塞、来源标记透传
python tests/test_source_labels.py  # Excel 列、图表水印、烟羽横幅、端到端来源标记
python tests/test_mcp_server.py     # 工具注册、传输解析、两个工具的端到端调用（含图片返回）
```

三个脚本都同时兼容 `pytest tests/`。

## 常见问题

- **找不到模型**：把 `voc_model*.pth` 放进 `models/`，或用 `SHIHUA_MODELS_DIR` 指向模型目录。
- **中文标签乱码**：Linux 容器里装一个中文字体（如 `fonts-wqy-zenhei`）；Windows 用黑体/雅黑。
- **烟羽地图底图空白**：HTML 已生成，地图瓦片需要浏览器联网加载。
- **日志出现 `增强轮廓生成失败，使用备用方法`**：新版 matplotlib 移除了
  `QuadContourSet.collections`，可视化模块自动回退到备用轮廓算法，不影响结果。
- **stderr 出现 `监测数据模式 = simulate` 警告**：提醒当前输出的是仿真数据，
  接入现场数据源后该提示消失。

## License

MIT

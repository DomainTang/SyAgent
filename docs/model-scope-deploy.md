# 魔搭（ModelScope）MCP 部署指南

本文面向「把本仓库发布到魔搭 MCP 广场 / 用 GitHub 快速创建可托管 MCP」的场景，
与 `12306-mcp` 的部署思路保持一致：**优先用 Stdio 子进程托管，让平台自动生成 SSE 地址**。

## 1. 为什么选 Stdio 托管就能拿到 SSE 地址

魔搭 MCP 云端托管的底层是阿里云函数计算（FC）。创建页把**传输/部署方式选成
Stdio（npx / uvx / python 等子进程方式）**时，FC 会在 stdio 子进程前面启动一个
**SSE 代理**，把 STDIO 转成 SSE / Streamable HTTP，并为每个实例生成独立地址，形如：

```text
https://mcp.api-inference.modelscope.net/<你的实例ID>/sse
```

也就是说：**SSE 地址是平台生成的，不是本进程自己监听 /sse 产生的**。本仓库同时内置了
两种远程传输（`POST /mcp`、`GET /sse`），只有在平台直接运行你的容器/进程并给你
HTTP 触发器时才会用到，见第 3 节。

## 2. 推荐做法：Stdio 托管（uvx 或仓库内直接运行）

在**部署方式 / 服务配置**一栏选择 **Stdio**，二选一填配置：

发布到 PyPI / 私有 index 后（推荐，启动最快）：

```json
{
  "mcpServers": {
    "shihua-mcp": {
      "command": "uvx",
      "args": ["--from", "shihua-mcp", "shihua-mcp"],
      "env": {"SHIHUA_DATA_MODE": "simulate"}
    }
  }
}
```

直接跑仓库代码（平台以仓库根目录为工作目录时）：

```json
{
  "mcpServers": {
    "shihua-mcp": {
      "command": "python",
      "args": ["-m", "shihua_mcp.server"],
      "env": {"SHIHUA_DATA_MODE": "simulate"}
    }
  }
}
```

若平台允许填写启动命令，也可以直接用控制台脚本：

```text
pip install -r requirements.txt && shihua-mcp
```

> ⚠️ 本服务依赖 `torch`，首次安装体积较大（数百 MB～2 GB，视平台镜像源而定）。
> 如果托管环境的安装有体积/超时限制，请改用第 3 节的容器 + HTTP 方式。

## 3. 平台要求「配置 SSE / Streamable HTTP」时：自己起 HTTP 服务

平台直接运行你的进程（容器方式）并给你 HTTP 触发器时，本仓库自带两种远程传输：

- Streamable HTTP：`POST /mcp`
- SSE：`GET /sse`

启动命令（自动绑定所有网卡，端口优先读 `PORT` 环境变量）：

```bash
python -m shihua_mcp.server --host 0.0.0.0
# 等价于：MCP_TRANSPORT=http PORT=8000 python -m shihua_mcp.server
```

用 Docker：

```bash
docker build -t shihua-mcp .
docker run -d -p 8000:8000 shihua-mcp
```

平台返回域名后拼上路径：

```text
Streamable HTTP: https://<你的域名>/mcp
SSE:             https://<你的域名>/sse
```

## 4. 模型权重必须可达（部署前必做）

`.gitignore` 默认忽略 `models/`，而溯源分析必须有 `voc_model_retrained.pth`
（约 15 MB）或 `voc_model.pth`。三种做法任选：

1. 把 `models/voc_model_retrained.pth` 一起提交到仓库（推荐，最省事）；
2. 上传到魔搭数据集 / 模型仓库，启动前下载到本地目录，并设置
   `SHIHUA_MODELS_DIR=/path/to/models`；
3. 用环境变量指向平台挂载的模型目录。

启动日志里出现 `模型加载成功: ...` 才算就绪；出现
`未找到 voc_model_retrained.pth / voc_model.pth` 时，`analyze-source` 会如实报错。

## 5. ModelScope 创建页建议填写内容

| 字段 | 建议值 |
| --- | --- |
| 中文名称 | 石化疑似源识别（Shihua VOC） |
| 英文名称 | Shihua VOC Source Identification |
| 服务介绍（README） | 见下方可直接复制的文字 |
| 来源地址 | 你的 GitHub 仓库 URL |
| 托管类型 | 可托管部署 |
| 部署方式 | Stdio（推荐，自动生成 SSE）；或容器 + HTTP（见第 3 节） |
| 环境变量 | `SHIHUA_DATA_MODE=simulate`（演示）；接入现场数据见第 6 节 |

### 可直接复制的“服务介绍”

> 石化疑似源识别 MCP 服务：提供「厂区多源监测数据生成」与「疑似源溯源分析」两个工具。
> `get-monitoring-data` 一次返回气体浓度、气象、设备隐患台账与厂区布置数据；
> `analyze-source` 对监测数据逐点位识别疑似源（含经纬度、置信度、Top3 候选），
> 给出预测源分布与置信度统计，并绘制预测源分布图、置信度分布图（图片内联返回），
> 可选生成烟羽扩散地图。所有输出均如实标注数据来源（SIMULATED / REAL / USER / UNAVAILABLE），
> 仿真数据严禁用于现场处置决策。

## 6. 环境变量清单

| 变量 | 默认 | 用途 |
| --- | --- | --- |
| `MCP_TRANSPORT` | `stdio` | `stdio` / `http` / `sse` |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | HTTP/SSE 监听地址与端口 |
| `MCP_HTTP_PATH` | `/mcp` | Streamable HTTP 路径 |
| `MCP_CORS_ORIGINS` | `*` | 公网部署请改成具体域名 |
| `SHIHUA_DATA_MODE` | `simulate` | `simulate` / `real` / `auto` / `off` |
| `SHIHUA_REALTIME_ENDPOINT` | 无 | 现场 GDS/DCS 数据端点，`real` 模式使用 |
| `SHIHUA_MODELS_DIR` | 仓库 `models/` | 模型权重目录 |
| `SHIHUA_DATA_DIR` | 仓库 `data/` | 语料库与 KML 目录 |
| `SHIHUA_OUTPUT_DIR` | 仓库 `output/` | 产物目录（需可写；只读文件系统请指向 `/tmp`） |

接入现场数据（示例）：

```bash
export SHIHUA_REALTIME_ENDPOINT=http://10.0.0.20:8080/gds/realtime   # 返回一节 JSON 即可
export SHIHUA_DATA_MODE=real
python -m shihua_mcp.server
```

`real` 模式调用失败时如实返回 `UNAVAILABLE` 与失败原因，**不会**静默回落到仿真数据。

## 7. 本地冒烟验证

```bash
# 1) 不启动服务，直接验证两个工具（含绘图）
python -m shihua_mcp.server --self-test

# 2) stdio 模式（客户端 / 平台托管用这个）
python -m shihua_mcp.server

# 3) HTTP / SSE 模式
python -m shihua_mcp.server --http --port 8000
#   POST http://127.0.0.1:8000/mcp     （Streamable HTTP，JSON-RPC）
#   GET  http://127.0.0.1:8000/sse     （SSE 事件流）
```

## 8. 常见坑

- **选了 SSE/HTTP 传输却没填公网地址**：平台不知道要转发给谁，部署会失败或不出 SSE 链接。
  正确做法是选 Stdio 托管，或先自己把容器跑起来再登记地址。
- **模型权重没进仓库/没挂载**：服务能启动，但 `analyze-source` 报“模型加载失败”。
- **只读文件系统**：把 `SHIHUA_OUTPUT_DIR` 指到 `/tmp`（`output/` 需要可写，用于落盘 Excel 与图片）。
- **中文字体缺失**：统计图中文会显示成方框，容器里安装 `fonts-wqy-zenhei` 即可
  （本仓库 Dockerfile 已装）。
- **烟羽地图打不开底图**：HTML 已生成，地图瓦片需要联网加载。
- **容器里 `PORT` 与默认 8000 不一致**：本服务会优先读平台注入的 `PORT`，
  不必手动改端口。

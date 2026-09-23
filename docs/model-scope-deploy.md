# 魔搭（ModelScope）托管部署 + Dify 接入指南

本文只解决一件事：**把本服务托管部署起来，拿到一个公网可用的 MCP 地址，填进 Dify。**

## 1. 先说结论：之前为什么「能创建 MCP，不能托管部署」

创建只是登记仓库信息，托管部署要平台真的把你的进程拉起来。之前仓库里有四个硬伤，
任何一个都会让启动失败、于是拿不到平台生成的 HTTP/SSE 地址：

| # | 硬伤 | 表现 | 现状 |
| --- | --- | --- | --- |
| 1 | 工程是 `src/` 布局，没有根目录启动入口 | 平台按 `python -m shihua_mcp.server` 拉起时 `ModuleNotFoundError: No module named 'shihua_mcp'`（本地能跑只是因为装过 editable 包） | ✅ 新增根目录 [server.py](../server.py)，`python server.py` 裸克隆即可运行 |
| 2 | 平台拉子进程时**不会** `pip install` 依赖 | `ModuleNotFoundError: No module named 'fastmcp' / 'torch'` | ✅ 用 Docker 方式（本文第 2 节 A）或 uvx 方式（B，自动装依赖） |
| 3 | `models/` 被 `.gitignore` 忽略 | 克隆里没有权重 → 分析类工具失败；Docker 构建时 `COPY models ./models` 直接构建失败 | ✅ Dockerfile 不再 COPY `models/`，改为建空目录；权重可提交 / 自动下载 / 挂载（第 3 节） |
| 4 | 服务启动即 import torch（数秒） | 平台健康检查/握手超时 → 判定部署失败 | ✅ 启动只加载轻量模块，首次调用 `analyze-source` 才载入 torch；并新增 `GET /health` |

> 一句话：**要拿公网 HTTP 地址给 Dify，最稳的是第 2 节 A（Docker + HTTP 模式）。**

## 2. 三种可用的托管方式

### A. Docker 镜像 + HTTP 模式（推荐，Dify 直接用）

仓库自带 `Dockerfile`，容器内以 Streamable HTTP + SSE 方式监听 `$PORT`：

```bash
docker build -t shihua-mcp .
docker run -d --name shihua-mcp -p 8000:8000 -e PORT=8000 shihua-mcp
```

确认服务健康（这就是平台/网关的探活地址）：

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","server":"shihua-voc-mcp","version":"0.3.0","tools":["get-monitoring-data","analyze-source"]}
```

服务暴露的端点：

| 端点 | 用途 |
| --- | --- |
| `POST /mcp` | Streamable HTTP（Dify「MCP (HTTP / Streamable HTTP)」填这个） |
| `GET /sse` | SSE（Dify「MCP (SSE)」填这个） |
| `GET /health` | 健康检查（平台探活、排错第一站） |
| `GET /` | 服务信息（工具列表与端点） |

在魔搭创建页选择容器/HTTP 方式，把平台分配的公网域名拼上路径：

```text
Streamable HTTP: https://<你的域名>/mcp
SSE:             https://<你的域名>/sse
```

如果平台只让你填「启动命令」而由平台负责构建镜像，用：

```text
docker build -t shihua-mcp . && docker run -p $PORT:$PORT -e PORT=$PORT shihua-mcp
```

或直接给启动命令（平台镜像里已含依赖时）：

```text
python server.py --http --host 0.0.0.0
```

### B. Stdio 托管（平台在前面挂 SSE 代理，自动生成 SSE 地址）

平台把 stdio 子进程包一层，转成 SSE/Streamable HTTP 并给你一个
`https://mcp.api-inference.modelscope.net/<实例ID>/sse` 形式地址。两种填法：

```json
{
  "mcpServers": {
    "shihua-mcp": {
      "command": "python",
      "args": ["server.py"],
      "env": {}
    }
  }
}
```

> 前提：运行环境里已装好 `requirements.txt`（平台镜像自带依赖时用这个）。

如果平台支持 **uvx**（会自动创建环境并安装依赖，无需预装）：

```json
{
  "mcpServers": {
    "shihua-mcp": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/<你的账号>/ShihuaCLI", "shihua-mcp"],
      "env": {}
    }
  }
}
```

> uvx 方式会安装 `torch`（数百 MB～2 GB），首次构建较慢，注意平台的构建时长与磁盘限制。
> 若超限，请改用 A（Docker，构建缓存可复用），或把 torch 换成 CPU 版镜像源
> （见 Dockerfile 内的注释）。

### C. 本地/公网自建 + 登记地址

平台只帮你登记一个「已经在跑的公网服务」时，先在任意有公网 IP 的机器上跑起来：

```bash
docker run -d -p 8000:8000 -e PORT=8000 shihua-mcp
# 然后把 http://<公网IP>:8000/sse （或 /mcp）填进平台
```

## 3. 权重与测试数据准备（决定分析工具能不能用）

| 资产 | 是否必需 | 说明 |
| --- | --- | --- |
| `models/voc_model_retrained.pth` | ✅ 必需 | 约 15 MB，`analyze-source` 用它推理 |
| `data/测试文本*.txt` | ⭕ 可选 | `get-monitoring-data(source=sample)` 的抽样来源；缺失时自动改用仿真数据 |
| `data/语料库带经维度2020-2025.txt` | ⭕ 可选 | 疑似源坐标知识库；缺失时坐标用默认值 |
| `data/*.kml` | ⭕ 可选 | 烟羽底图；缺失时自动生成“示例点位底图” |

权重三种做法任选（推荐 1 或 2）：

1. **提交进仓库**：`.gitignore` 已不再忽略 `models/*.pth`，`git add models/voc_model_retrained.pth` 即可。
2. **自动下载**：把权重上传到魔搭数据集/模型仓库，运行时设置
   `SHIHUA_MODEL_URL=https://.../voc_model_retrained.pth`，首次分析自动下载到 `models/`。
3. **挂载目录**：`-v /host/models:/app/models -e SHIHUA_MODELS_DIR=/app/models`。

启动日志里出现 `模型加载成功: ...` 说明就绪；`analyze-source` 返回
“模型加载失败：未在 ... 找到 voc_model_retrained.pth”说明权重没到位。

## 4. 创建页建议填写内容

| 字段 | 建议值 |
| --- | --- |
| 中文名称 | 石化疑似源识别（Shihua VOC） |
| 英文名称 | Shihua VOC Source Identification |
| 服务介绍（README） | 见下方可直接复制的文字 |
| 来源地址 | 你的 GitHub 仓库 URL |
| 托管类型 | 可托管部署 |
| 部署方式 | 容器 / HTTP（推荐，直接产出 `/mcp`、`/sse`）；或 Stdio（平台代理出 SSE） |
| 启动命令 | `python server.py --http --host 0.0.0.0`（容器方式）；`python server.py`（Stdio 方式） |
| 健康检查路径 | `/health` |
| 环境变量 | `PORT`（平台一般自动注入）；权重走第 3 节任一方式 |

### 可直接复制的“服务介绍”

> 石化疑似源识别 MCP 服务，只提供两个工具：`get-monitoring-data`（数据获取）与
> `analyze-source`（模型分析并绘图）。取数支持仓库内置历史监测文本随机抽样（SAMPLE）
> 与本地仿真生成（SIMULATED，含气体浓度、气象、设备隐患台账、厂区布置）；
> 分析工具逐点位识别疑似源（含经纬度、置信度、Top3 候选），给出预测源分布与置信度统计，
> 并绘制预测源分布图、置信度分布图（图片内联返回），可选生成烟羽扩散地图。
> 所有输出均如实标注数据来源（SAMPLE / SIMULATED / USER），仿真数据严禁用于现场处置决策。
> 同时提供 `GET /health` 健康检查端点，便于容器平台探活。

## 5. 本地冒烟验证（部署前先跑这三步）

```bash
# 1) 自检：两个工具 + 两种数据来源 + 绘图，全流程
python server.py --self-test

# 2) HTTP 模式（与容器里跑的完全一致）
python server.py --http --port 8000
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/

# 3) stdio 模式（平台代理方式）
python server.py
```

## 6. Dify 侧接入步骤

1. Dify → 工具 → 添加 MCP 工具（或「MCP Server」），传输类型二选一：
   - **Streamable HTTP**：URL 填 `https://<你的域名>/mcp`
   - **SSE**：URL 填 `https://<你的域名>/sse`
2. 先点「测试/连接」。连不上时先用浏览器或 `curl` 访问同一域名的 `/health`：
   - `/health` 都打不开 → 部署/网络问题（防火墙、域名、端口）；
   - `/health` 正常但 Dify 报错 → Dify 的 URL 少了 `/mcp` 或 `/sse`，或该 Dify 实例访问不了公网域名。
3. **把超时时间调大**（建议 ≥60 s）。首次调用 `analyze-source` 需要加载 torch 与权重，
   冷启动可能十几秒；之后每次推理在秒级。
4. 工具可见性：Dify 只会把它「拉到」当前智能体的工具里；确认这两个工具已勾选：
   `get-monitoring-data`、`analyze-source`。
5. 推荐调用顺序（提示词里可以明确写死）：
   `get-monitoring-data` → 把返回的 `text` 原样传给 `analyze-source` → 用返回的图片与文本作答；
   回答中必须引用 `data_source`，SIMULATED 时要声明“基于本地仿真数据，不得用于现场处置决策”。

## 7. 排错清单（对着容器日志逐条看）

| 日志/症状 | 原因 | 处理 |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'shihua_mcp'` | 用 `python -m shihua_mcp.server` 拉起裸克隆 | 改用 `python server.py` |
| `ModuleNotFoundError: No module named 'fastmcp' / 'torch'` | 平台没装依赖 | 用 Docker（第 2 节 A）或 uvx（B） |
| 构建时报 `COPY failed: models: not found` | 旧版 Dockerfile 依赖 `models/` 目录 | 已修：Dockerfile 建空目录，不 COPY models |
| 健康检查失败 / 部署超时 | 端口不对或启动太慢 | 服务优先读平台注入的 `PORT`；`/health` 已就绪，冷启动约 1～2 s |
| `analyze-source` 报“模型加载失败” | 权重没到位 | 提交权重、设 `SHIHUA_MODEL_URL`、或挂载 `SHIHUA_MODELS_DIR` |
| 统计图中文显示成方框 | 容器缺中文字体 | 本仓库 Dockerfile 已装 `fonts-wqy-zenhei` |
| Dify 调用超时 | 首次加载模型 + Dify 默认超时过短 | 把工具超时调到 ≥60 s，或先手动调一次预热 |
| 取数返回 `status=error` | `data/` 里没有任何含“传感器…，位置为…”的文本 | 用 `source="simulated"`，或把测试文本放回 `data/` |

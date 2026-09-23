# 石化疑似源识别 MCP 服务：容器内以 Streamable HTTP + SSE 方式监听 $PORT
#
# 构建：docker build -t shihua-mcp .
# 运行：docker run -d -p 8000:8000 -e PORT=8000 shihua-mcp
# 端点：POST /mcp （Streamable HTTP，Dify「MCP(HTTP)」用这个）
#       GET  /sse （SSE，Dify「MCP(SSE)」用这个）
#       GET  /health（健康检查）
#
# 权重获取三种方式，任选其一：
#   1) 把 models/voc_model_retrained.pth 提交进仓库（本文件会 COPY 进来）；
#   2) 运行时给 SHIHUA_MODEL_URL=<可下载地址>，首次分析自动下载；
#   3) 运行时挂载目录：-v /host/models:/app/models -e SHIHUA_MODELS_DIR=/app/models
FROM python:3.11-slim

WORKDIR /app

# 中文字体（统计图里的中文标签）+ 构建期不需要的包一律不装
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg \
    HOST=0.0.0.0 \
    PORT=8000 \
    FASTMCP_HOME=/app/output/.fastmcp \
    FASTMCP_CHECK_FOR_UPDATES=off

# 依赖先装，充分利用镜像层缓存。
# 想显著减小镜像体积（省掉 CUDA 依赖）可把这行换成：
#   RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
#       && pip install --no-cache-dir -r requirements.txt
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY src ./src
COPY server.py pyproject.toml README.md ./

# data/ 是仓库自带目录（内置测试文本、语料库、KML）
COPY data ./data

# models/ 默认被 .gitignore 忽略，克隆下来可能不存在 → 这里只建空目录，
# 绝不在构建期 COPY（否则镜像构建会直接失败，托管部署就卡在这一步）。
RUN mkdir -p models output

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,urllib.request as u;u.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health').read()"

# 平台/客户端若选择 stdio 方式，可覆盖为：
#   docker run --rm -i shihua-mcp python server.py
CMD ["python", "server.py", "--http", "--host", "0.0.0.0"]

# 石化疑似源识别 MCP 服务：默认以 Streamable HTTP + SSE 方式监听 8000 端口
FROM python:3.11-slim

WORKDIR /app

# 中文字体（统计图中的中文标签）+ 构建依赖
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

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY src ./src
COPY data ./data
COPY models ./models
COPY pyproject.toml README.md ./

# stdio 模式：docker run --rm -i shihua-mcp python -m shihua_mcp.server
EXPOSE 8000
CMD ["python", "-m", "shihua_mcp.server", "--http", "--host", "0.0.0.0"]

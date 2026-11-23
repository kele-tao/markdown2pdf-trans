# syntax=docker/dockerfile:1
# ---- 构建阶段：仅装 Python 依赖，便于利用层缓存 ----
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

# ---- 运行阶段 ----
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=UTF-8 \
    M2P_DATA_DIR=/app/data \
    M2P_FONT_DIR=/app/fonts \
    LOG_LEVEL=INFO \
    M2P_PORT=5000

# 系统依赖：
#   pandoc                Markdown → HTML
#   libpango/libharfbuzz  WeasyPrint 排版与文本塑形（阿拉伯语需要 harfbuzz）
#   fonts-noto-cjk        中日韩字体
#   fonts-noto-core       Noto 基础字体（含阿拉伯语 Naskh/Naskh Arabic）
RUN apt-get update && apt-get install -y --no-install-recommends \
        pandoc \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libharfbuzz-subset0 \
        libfontconfig1 \
        libgdk-pixbuf-2.0-0 \
        shared-mime-info \
        fonts-noto-cjk \
        fonts-noto-core \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
WORKDIR /app
COPY . /app

RUN mkdir -p /app/data && useradd --create-home --uid 10001 m2p && chown -R m2p:m2p /app
USER m2p

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5000/api/health || exit 1

CMD ["python", "-m", "src.app"]

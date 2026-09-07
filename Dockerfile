# 多阶段构建，减小镜像体积
FROM python:3.11-slim as builder

ARG BUILDTIME
ARG VERSION
ARG REVISION

WORKDIR /app

# 构建期依赖（curl_cffi 需要 libcurl 头文件 & 编译器才能 build wheel，预装 wheel 通常足够，
# 但保留 build-essential 以兜底 musl/aarch64 等环境）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 运行阶段
FROM python:3.11-slim

# 创建非root用户
RUN groupadd -r appuser && useradd -r -g appuser appuser

# 运行时依赖：curl_cffi 需要 libcurl 的 ssl 后端
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY --chown=appuser:appuser . .

RUN mkdir -p /app/logs /app/config \
    && chown -R appuser:appuser /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

ARG BUILDTIME
ARG VERSION
ARG REVISION
LABEL org.opencontainers.image.created=${BUILDTIME}
LABEL org.opencontainers.image.version=${VERSION}
LABEL org.opencontainers.image.revision=${REVISION}
LABEL org.opencontainers.image.licenses="MIT"

EXPOSE 8181

ENV DOCKER_CONTAINER=true
CMD ["python", "run.py", "--mode", "web"]

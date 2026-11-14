# 专利写作 Agent 服务化镜像
FROM python:3.10-slim

# 避免 pyc / 缓冲，利于容器日志实时输出
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖（利用 Docker 层缓存，源码变更不触发重装）
COPY requirements.txt .
RUN pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 复制项目源码
COPY . .

# 数据/输出/断点目录
RUN mkdir -p data output jobs/checkpoints

EXPOSE 8000

# 默认启动 HTTP 服务；可通过 SERVE_PORT 覆盖
CMD ["uvicorn", "serve.app:app", "--host", "0.0.0.0", "--port", "8000"]

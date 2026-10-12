# Prag 应用容器镜像构建文件。

FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    --no-deps \
    torch==2.9.1+cpu \
    && pip install --no-cache-dir \
    filelock \
    typing-extensions \
    sympy \
    networkx \
    jinja2 \
    fsspec \
    && pip install --no-cache-dir \
    -c constraints.txt \
    -r requirements.txt

COPY app ./app
COPY frontend ./frontend
COPY init.sql ./init.sql

RUN mkdir -p /app/data/uploads /app/data/logs

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

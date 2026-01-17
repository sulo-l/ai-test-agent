FROM python:3.10-slim

# ========== 基础环境 ==========
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RUN_MODE=docker

WORKDIR /app

# ========== 系统依赖（PDF / 字体 / SSL） ==========
RUN apt-get update && apt-get install -y \
    build-essential \
    poppler-utils \
    fonts-noto-cjk \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ========== Python 依赖 ==========
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ========== 项目代码 ==========
COPY . .

# ========== 临时目录（⚠️ 关键） ==========
# Docker 内允许写
RUN mkdir -p /data/tmp && chmod -R 777 /data

# ========== 端口 ==========
EXPOSE 8000

# ========== 启动 ==========
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# app/main.py
# -*- coding: utf-8 -*-

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ===============================
# 项目内部依赖
# ===============================
from app.settings import TMP_DIR

# Redis / ARQ pool（用于 enqueue_job 的连接池）
from app.infra.redis_client import init_redis, close_redis
from app.infra.arq_pool import init_arq_pool, close_arq_pool


# =====================================================
# 初始化 TMP 目录（确保临时文件夹存在）
# =====================================================
os.makedirs(TMP_DIR, exist_ok=True)


# =====================================================
# Lifespan：启动/关闭时初始化基础设施
# =====================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_redis()
    await init_arq_pool()
    yield
    await close_arq_pool()
    await close_redis()


# =====================================================
# FastAPI 应用初始化
# =====================================================
app = FastAPI(
    title="AI Test Agent Platform",
    version="1.0.0",
    lifespan=lifespan,
)


# =====================================================
# CORS 中间件
# =====================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =====================================================
# 注册 Router
# =====================================================

# 1️⃣ workflow：创建 / 上传 / 存文本
from app.workflow.router import router as workflow_router
app.include_router(workflow_router)

# 2️⃣ A 分支：需求分析智能体
# analysis_app/router.py 内部已经定义了 prefix="/analysis"
# 这里不要重复传 prefix，否则会变成 /analysis/analysis/*
from app.analysis_app.router import router as analysis_router
app.include_router(analysis_router)

# 3️⃣ B 分支：测试用例生成智能体
# testcase_app/router.py 内部已自带 prefix="/testcase"
# 这里不要重复加 prefix
from app.testcase_app.router import router as testcase_router
app.include_router(testcase_router)

# 4️⃣ C 分支：测试策略智能体
# strategy_app/router.py 内部应自带 prefix="/strategy"
# 这里同样不要重复加 prefix
from app.strategy_app.router import router as strategy_router
app.include_router(strategy_router)


# =====================================================
# 健康检查
# =====================================================
@app.get("/health")
def health_check():
    """
    健康检查端点
    """
    return {"status": "ok"}
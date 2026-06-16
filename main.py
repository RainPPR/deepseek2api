"""
DeepSeek2API: 通过 DeepSeek 官网聊天页提供逆向 API 服务。
启动时 Playwright 可能会要求完成验证码，通过后即可正常使用。
"""

import os
import sys
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from browser import (
    global_state,
    setup_browser,
    stop_browser,
    async_playwright,
)
from api import router

# --------------- Windows 终端颜色 ---------------

if os.name == 'nt':
    os.system('color')

# --------------- 加载环境变量 ---------------

load_dotenv()

CREDENTIALS = {
    "cookie": os.getenv("cookie"),
    "userToken": os.getenv("userToken")
}

CUSTOM_PORT = int(os.getenv("PORT", 8000))

# HEADLESS 环境变量处理
# 当设置为 0 / false / no / off / n / f 等（不区分大小写）时，headless=False
_headless_env = os.getenv("HEADLESS", "").lower()
HEADLESS = _headless_env not in ("0", "false", "no", "off", "n", "f")

# --------------- FastAPI 生命周期 ---------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """管理应用生命周期：启动浏览器，结束后清理。"""
    print("⏳ 初始化浏览器环境中...")
    global_state.playwright = await async_playwright().start()
    await setup_browser(global_state.playwright, CREDENTIALS, headless=HEADLESS)
    print("✅ 浏览器就绪，API 服务启动成功。")

    yield

    await stop_browser()

# --------------- 创建 FastAPI 应用 ---------------

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载 API 路由
app.include_router(router)

# --------------- 入口 ---------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=CUSTOM_PORT)

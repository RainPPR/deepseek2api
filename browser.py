"""
DeepSeek2API: Playwright 浏览器自动化模块。
"""

import json
import asyncio
import sys
from typing import Dict, Any, AsyncIterator, Tuple

from seleniumbase import cdp_driver
from playwright.async_api import async_playwright
import httpx


# --------------- 全局状态 ---------------

class BrowserState:
    """管理浏览器及拦截数据的全局状态"""
    def __init__(self):
        self.playwright: Any = None
        self.browser: Any = None
        self.page: Any = None
        self.intercepted_data: Dict = {"headers": {}, "payload": None}
        self.intercept_event = asyncio.Event()
        self.chat_lock = asyncio.Lock()


global_state = BrowserState()


# --------------- Playwright 路由拦截器 ---------------

async def _handle_route(route):
    """拦截 chat/completion 请求，提取 headers 和 payload"""
    request = route.request
    if request.method == "POST":
        raw_headers = await request.all_headers()
        clean_headers = {k: v for k, v in raw_headers.items() if not k.startswith(':')}
        global_state.intercepted_data["headers"] = clean_headers
        global_state.intercepted_data["payload"] = request.post_data
        global_state.intercept_event.set()
        await route.abort()
    else:
        await route.continue_()


# --------------- 浏览器操作 ---------------

async def setup_browser(p, credentials: dict, headless: bool = True):
    """初始化浏览器，登录 DeepSeek 并等待页面就绪。
    
    Args:
        p: Playwright 主控对象
        credentials: 包含 cookie 和 userToken 的字典
        headless: 是否以无头模式运行，默认 True
    """
    driver = await cdp_driver.start_async(headless=headless)
    endpoint_url = driver.get_endpoint_url()
    browser = await p.chromium.connect_over_cdp(endpoint_url)

    # 复用 CDP 自带的默认 context，避免 new_context() 多弹出一个空窗口
    context = browser.contexts[0]

    # 注入 Cookie
    cookies = []
    for item in credentials["cookie"].split(';'):
        if '=' in item:
            name, value = item.split('=', 1)
            cookies.append({
                'name': name.strip(),
                'value': value.strip(),
                'domain': '.deepseek.com',
                'path': '/'
            })
    await context.add_cookies(cookies)

    # 复用默认 context 中已有的 page（避免 new_page 再开新窗口）
    page = context.pages[0] if context.pages else await context.new_page()
    await page.route("**/api/v0/chat/completion", _handle_route)
    await page.goto("https://chat.deepseek.com")

    # 注入 userToken
    auth_script = (
        f"window.localStorage.setItem('userToken', "
        f"JSON.stringify({{value: '{credentials['userToken']}', __version: '0'}}));"
    )
    await page.evaluate(auth_script)
    await page.reload()

    try:
        await page.wait_for_selector(
            'textarea[placeholder*="给 DeepSeek 发送消息"]',
            timeout=15000
        )
    except Exception:
        print("❌ 登录失败，请检查 Token 与 Cookie 是否有效。")
        sys.exit(1)

    global_state.browser = browser
    global_state.page = page
    return browser, page


async def stop_browser():
    """关闭浏览器环境。"""
    print("关闭浏览器环境...")
    if global_state.browser:
        await global_state.browser.close()
        global_state.browser = None
    if global_state.playwright:
        await global_state.playwright.stop()
        global_state.playwright = None


# --------------- 页面设置 ---------------

async def apply_settings(page, model_choice: str, use_think: bool, use_search: bool):
    """设置对话模型、深度思考与智能搜索开关。"""
    # 1. 重置为“开启新对话”
    new_chat_btn = page.locator('span:text-is("开启新对话")')
    if await new_chat_btn.count() > 0 and await new_chat_btn.first.is_visible():
        await new_chat_btn.first.click()
        await asyncio.sleep(0.5)

    # 2. 切换模型
    model_type = "expert" if model_choice == '2' else "default"
    model_btn = page.locator(f'div[data-model-type="{model_type}"]')
    if await model_btn.count() > 0 and await model_btn.first.is_visible():
        await model_btn.first.click()

    # 3. 切换深度思考 / 智能搜索
    for label, want_on in [("深度思考", use_think), ("智能搜索", use_search)]:
        btn = page.locator('div.ds-toggle-button', has_text=label).first
        if await btn.count() > 0 and await btn.is_visible():
            aria_pressed = await btn.get_attribute('aria-pressed')
            is_on = aria_pressed == 'true'
            if want_on != is_on:
                await btn.click()

    await asyncio.sleep(0.5)


# --------------- 模型 ID 解析 ---------------

def parse_model_id(model_id: str):
    """解析请求的模型 ID 为可操作指令。"""
    model_id = model_id.lower()
    is_expert = "expert" in model_id
    use_think = "thinking" in model_id
    use_search = "search" in model_id
    model_choice = "2" if is_expert else "1"
    return model_choice, use_think, use_search


# --------------- 流式数据获取 ---------------

async def fetch_deepseek_stream(
    headers: dict, payload: str
) -> AsyncIterator[Tuple[str, str]]:
    """建立与 DeepSeek 官方接口的流式连接。

    返回 AsyncIterator[Tuple[type, content]]，其中 type 为 "content" 或 "reasoning".
    """
    url = "https://chat.deepseek.com/api/v0/chat/completion"
    is_thinking = False

    async with httpx.AsyncClient(timeout=300.0) as client:
        async with client.stream("POST", url, headers=headers, content=payload) as response:
            response.raise_for_status()

            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue

                json_str = line[6:]
                if json_str == "[DONE]":
                    break

                try:
                    data = json.loads(json_str)
                except json.JSONDecodeError:
                    continue

                v = data.get("v")

                # 极简增量
                if "p" not in data and isinstance(v, str):
                    if is_thinking:
                        yield "reasoning", v
                    else:
                        yield "content", v
                    continue

                # 状态切换 (APPEND 事件)
                if data.get("p") == "response/fragments" and data.get("o") == "APPEND":
                    frags = v if isinstance(v, list) else []
                    for frag in frags:
                        f_type = frag.get("type")
                        if f_type == "RESPONSE":
                            if is_thinking:
                                is_thinking = False
                            yield "content", frag.get("content", "")
                        elif f_type == "THINK":
                            is_thinking = True
                            yield "reasoning", frag.get("content", "")
                    continue

                # 初始数据包
                if isinstance(v, dict) and "response" in v:
                    frags = v["response"].get("fragments", [])
                    for frag in frags:
                        f_type = frag.get("type")
                        if f_type == "THINK":
                            is_thinking = True
                            yield "reasoning", frag.get("content", "")
                        elif f_type == "RESPONSE":
                            yield "content", frag.get("content", "")
                    continue

                # 指定路径追加
                if data.get("p") == "response/fragments/-1/content" and isinstance(v, str):
                    if is_thinking:
                        yield "reasoning", v
                    else:
                        yield "content", v
                    continue

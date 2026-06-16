"""
DeepSeek2API: FastAPI 路由与 OpenAI 兼容接口。
使用 encoding_dsv4 进行正确的 prompt 编码。
"""

import json
import time
import asyncio

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import httpx as _httpx

from browser import (
    global_state,
    parse_model_id,
    apply_settings,
    fetch_deepseek_stream,
)
from encoding.encoding_dsv4 import encode_messages

router = APIRouter()


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """兼容 OpenAI 格式的对话接口。"""
    body = await request.json()
    model = body.get("model", "deepseek-fast")
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    if not messages:
        raise HTTPException(status_code=400, detail="The 'messages' array is required.")

    # 显式禁用工具调用：当前通过网页逆向获取的 API 不稳定且不安全，不支持 tool calling
    if body.get("tools"):
        raise HTTPException(
            status_code=400,
            detail="Tool calling is not supported because it is unstable and insecure in the current web-based reverse-engineered setup."
        )

    # 根据 model id 解析 thinking mode 和 reasoning effort
    thinking_mode = "thinking" if "thinking" in model.lower() else "chat"
    reasoning_effort = "max" if "max" in model.lower() else None

    # 使用 encoding_dsv4 进行正确的 prompt 编码
    # 之前的简单拼接无法处理 system 消息、历史多轮对话、thinking mode 等
    try:
        query_text = encode_messages(
            messages,
            thinking_mode=thinking_mode,
            reasoning_effort=reasoning_effort,
            drop_thinking=False
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to encode messages: {e}")

    # 解析模型指令（用于网页 UI 自动化操作）
    m_choice, use_think, use_search = parse_model_id(model)
    req_id = int(time.time())

    # 获取队列锁，防止多请求同时操控浏览器
    await global_state.chat_lock.acquire()

    try:
        page = global_state.page
        global_state.intercept_event.clear()

        await apply_settings(page, m_choice, use_think, use_search)

        text_area = page.locator('textarea[placeholder*="给 DeepSeek 发送消息"]')
        await text_area.fill(query_text)
        await text_area.press("Enter")

        try:
            await asyncio.wait_for(global_state.intercept_event.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Timeout waiting for browser interception.")

        request_headers = global_state.intercepted_data["headers"]
        request_payload = global_state.intercepted_data["payload"]

    except Exception as e:
        global_state.chat_lock.release()
        raise HTTPException(status_code=500, detail=str(e))

    # 生成流式响应
    if stream:
        async def event_generator():
            try:
                async for chunk_type, chunk_text in fetch_deepseek_stream(request_headers, request_payload):
                    delta = {}
                    if chunk_type == "content":
                        delta["content"] = chunk_text
                    elif chunk_type == "reasoning":
                        delta["reasoning_content"] = chunk_text

                    chunk_data = {
                        "id": f"chatcmpl-{req_id}",
                        "object": "chat.completion.chunk",
                        "model": model,
                        "choices": [{"index": 0, "delta": delta}]
                    }
                    yield f"data: {json.dumps(chunk_data)}\n\n"
                yield "data: [DONE]\n\n"
            except _httpx.HTTPError:
                # 服务端在流式传输中途断开连接，优雅结束
                yield "data: [DONE]\n\n"
            finally:
                global_state.chat_lock.release()

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # 生成阻塞响应（非流式）
    else:
        full_content = ""
        full_reasoning = ""

        try:
            async for chunk_type, chunk_text in fetch_deepseek_stream(request_headers, request_payload):
                if chunk_type == "content":
                    full_content += chunk_text
                elif chunk_type == "reasoning":
                    full_reasoning += chunk_text
        except _httpx.HTTPError:
            # 服务端在传输过程中断开，返回当前已收集的部分内容
            pass
        finally:
            global_state.chat_lock.release()

        return JSONResponse(content={
            "id": f"chatcmpl-{req_id}",
            "object": "chat.completion",
            "model": model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": full_content,
                    "reasoning_content": full_reasoning
                }
            }]
        })

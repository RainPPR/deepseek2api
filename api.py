"""
DeepSeek2API: FastAPI 路由与 OpenAI 兼容接口。
"""

import json
import time
import asyncio

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

from browser import (
    global_state,
    parse_model_id,
    apply_settings,
    fetch_deepseek_stream,
)

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

    # 合并历史消息为单次查询输入
    query_text = "\n".join(
        f"{msg.get('role', 'user')}: {msg.get('content', '')}" for msg in messages
    )

    # 解析模型指令
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

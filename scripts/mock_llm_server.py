"""Mock OpenAI-compatible LLM server for local/E2E tests (no API key needed).

Implements POST /v1/chat/completions with deterministic canned answers
selected by system-prompt markers, supporting stream=true (SSE chunks)
and stream=false. Run inside the kb-test container:

    python mock_llm_server.py   # listens on :8011

Point the app at it with:
    KB_AI_BASE_URL=http://127.0.0.1:8011/v1 KB_AI_API_KEY=mock KB_AI_MODEL=mock-1
"""
import asyncio
import json
import re
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()


def _first_page_name(system: str) -> str:
    m = re.search(r"【当前页面[:：](.+?)】", system)
    if m:
        return m.group(1)
    m = re.search(r"### (.+)", system)
    return m.group(1) if m else "KB-看板"


def _canned(system: str, user: str) -> str:
    # Structural branches first — the chat system prompt embeds retrieved page
    # content, which could accidentally contain editor-action keywords below.
    if system.startswith("你是用户个人知识库"):
        page = _first_page_name(system)
        return (
            f"根据你的知识库,这个问题可以参考 [[{page}]] 里的内容。\n\n"
            "- mock 回答第一点\n- mock 回答第二点\n\n"
            "以上由 mock LLM 生成,用于无 key 环境的端到端测试。"
        )
    if system.startswith("你是知识库的链接编辑"):
        # Parse the structured user message from ai.suggest_links()
        name_m = re.search(r"页面名[:：]\s*(.+)", user)
        content_m = re.search(r"页面内容[:：]\n(.*?)\n\n库中其他页面名列表", user, re.S)
        list_m = re.search(r"页面名列表[:：]\n(.+)", user)
        content = content_m.group(1) if content_m else ""
        target = (list_m.group(1).split(",")[0].strip() if list_m else "") or "KB-看板"
        first_line = next((l.strip() for l in content.splitlines() if len(l.strip()) >= 5), "")
        anchor = first_line[:15] if first_line else content.strip()[:15]
        if not anchor:
            return "[]"
        return json.dumps(
            [{"anchor": anchor, "target": target, "reason": "mock: 内容提到了这个概念"}],
            ensure_ascii=False,
        )
    if system.startswith("润色"):
        return f"✨（润色）{user}"
    if system.startswith("续写"):
        return "（AI 续写）顺着这个思路,可以再补充一两个具体例子,让结论更有说服力。"
    if system.startswith("总结"):
        return "- 要点一:核心问题\n- 要点二:现有做法\n- 要点三:下一步计划"
    if system.startswith("扩展"):
        return f"{user}\n（AI 扩展:补充了一个具体例子）"
    return f"（mock）{user}"


@app.get("/health")
async def health():
    return {"ok": True, "ts": time.time()}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    system = next((m["content"] for m in messages if m["role"] == "system"), "")
    user = next((m["content"] for m in messages if m["role"] == "user"), "")
    answer = _canned(system, user)

    if not body.get("stream"):
        return {
            "choices": [
                {"message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}
            ]
        }

    async def gen():
        # 8-char chunks with a small delay so streaming is visible in the UI
        for i in range(0, len(answer), 8):
            chunk = {
                "choices": [{"delta": {"content": answer[i : i + 8]}}]
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.03)
        yield 'data: [DONE]\n\n'

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8011, log_level="warning")

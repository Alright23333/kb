"""AI endpoints: status, grounded chat (SSE stream), editor actions,
link suggestions + apply. All read their config from ai.py env vars."""
import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import ai
from ai import AIError, ai_enabled
from database import get_db

router = APIRouter(prefix="/ai")


def _require_enabled() -> None:
    if not ai_enabled():
        raise HTTPException(503, "AI 未配置:设置环境变量 KB_AI_API_KEY 后重启即可启用")


@router.get("/status")
async def ai_status():
    return {
        "enabled": ai_enabled(),
        "model": ai.AI_MODEL,
        "provider": ai.AI_BASE_URL.split("/api")[0],
    }


# ── 问: grounded chat ─────────────────────────────────────────────

class ChatIn(BaseModel):
    messages: list[dict]
    page: Optional[str] = None


@router.post("/chat")
async def ai_chat(body: ChatIn):
    _require_enabled()
    messages = body.messages[-20:]
    clean = []
    for m in messages:
        role = m.get("role")
        content = str(m.get("content", ""))[:8000]
        if role in ("user", "assistant") and content.strip():
            clean.append({"role": role, "content": content})
    if not clean or clean[-1]["role"] != "user":
        raise HTTPException(422, "最后一条消息必须是用户消息")

    last_user = clean[-1]["content"]
    try:
        context = await ai.build_chat_context(last_user, body.page)
    except Exception as e:  # retrieval must never kill the chat
        context = f"（检索失败:{e}）"

    full = [{"role": "system", "content": ai.CHAT_SYSTEM + context}] + clean

    async def gen():
        try:
            async for delta in ai.chat_stream(full):
                yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
            yield 'data: {"done": true}\n\n'
        except AIError as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': f'AI 调用异常: {e}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 写: editor actions ────────────────────────────────────────────

class EditIn(BaseModel):
    action: str
    text: str
    instruction: Optional[str] = None


@router.post("/edit")
async def ai_edit(body: EditIn):
    _require_enabled()
    if not body.text.strip():
        raise HTTPException(422, "没有选中文本")
    try:
        result = await ai.chat(ai.edit_messages(body.action, body.text, body.instruction))
    except AIError as e:
        raise HTTPException(502, str(e))
    return {"result": result.strip()}


# ── 连: link suggestions ──────────────────────────────────────────

class LinksIn(BaseModel):
    name: str


class LinkApplyIn(BaseModel):
    name: str
    anchor: str
    target: str


async def _load_page(name: str):
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT id, name, content FROM pages WHERE name = ? COLLATE NOCASE", (name,)
        )
        row = await cur.fetchone()
    finally:
        await db.close()
    if not row:
        raise HTTPException(404, f"页面不存在: {name}")
    return row


@router.post("/links")
async def ai_links(body: LinksIn):
    _require_enabled()
    page = await _load_page(body.name)
    db = await get_db()
    try:
        cur = await db.execute("SELECT name FROM pages ORDER BY name")
        rows = await cur.fetchall()
    finally:
        await db.close()
    all_names = [r["name"] for r in rows]
    try:
        suggestions = await ai.suggest_links(page["name"], page["content"] or "", all_names)
    except AIError as e:
        raise HTTPException(502, str(e))
    return {"suggestions": suggestions}


@router.post("/links/apply")
async def ai_links_apply(body: LinkApplyIn):
    page = await _load_page(body.name)
    new_content = ai.apply_link(page["content"] or "", body.anchor, body.target)
    if new_content is None:
        raise HTTPException(422, "原文片段已变化,无法定位(可能已应用过)")

    from routers.api import _sync  # late import: api.py pulls heavier deps

    db = await get_db()
    try:
        await db.execute(
            "UPDATE pages SET content = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (new_content, page["id"]),
        )
        await _sync(db, page["id"], new_content)
        await db.commit()
    finally:
        await db.close()
    return {"content": new_content}

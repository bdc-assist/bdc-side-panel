"""The side panel's API (bdc-assist#64, #66). Stateless: the client sends everything a turn needs.

    GET  /health                   {status, forms}: how many forms are loaded (0: none yet)
    GET  /tools                    {"form": [FormDef]}: the forms the panel can draft (forms.py)
    POST /tools/form/{id}/open     {history, question} -> {summary, values, reply, blocked}: the first draft
    POST /tools/form/{id}/chat     {values, edited, messages, summary, question} -> {values, reply, blocked}

404: no such form; 422: a malformed body; 503: no forms loaded yet, or busy ({"busy": true}, Retry-After);
502: the LLM provider failed. KG routes can sit beside the form ones later, under /tools/kg/.
"""

import logging
import time
from contextlib import aclosing
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from openai import OpenAIError
from pydantic import BaseModel, Field, field_validator

from .admission import Admission, Full
from .config import (CORS_ORIGINS, DOC_MCP_URL, FORMS_REFRESH_SECONDS, LOG_LEVEL, MAX_RUNNING_CHATS,
                     MAX_WAITING_CHATS, PORT, get_llm)
from .forms import FormStore, NoForms
from .turn import chat_turn, open_turn

logging.basicConfig(level=LOG_LEVEL, format="%(levelname)s:     %(name)s: %(message)s")
log = logging.getLogger(__name__)

store = FormStore(DOC_MCP_URL, FORMS_REFRESH_SECONDS)
admission = Admission(MAX_RUNNING_CHATS, MAX_WAITING_CHATS)

app = FastAPI(title="BDC side panel", version="0.1.0", description="Drafts BDC site forms from the chat")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


async def _as_json_error(request, exc):
    # copied from bdc-doc-mcp: a JSON detail every client can read, not a plain-text 500.
    # OpenAIError: the LLM provider unreachable, an expired or missing key, a rate limit (openai/azure/vllm).
    status = 502 if isinstance(exc, OpenAIError) else 500
    return JSONResponse(status_code=status, content={"detail": f"{type(exc).__name__}: {exc}"})


async def _no_forms(request, exc):
    return JSONResponse(status_code=503, content={"detail": "no forms loaded: is bdc-doc-mcp up?"})


app.add_exception_handler(OpenAIError, _as_json_error)
app.add_exception_handler(ValueError, _as_json_error)
app.add_exception_handler(Exception, _as_json_error)
app.add_exception_handler(NoForms, _no_forms)


class MainMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class OpenRequest(BaseModel):
    history: list[MainMessage] = []  # the main chat up to the answer that offered the form
    question: str | None = None  # that answer's question, contextualized; None from the header's Get help


class PanelMessage(BaseModel):
    role: Literal["user", "tool"]
    text: str


class ChatRequest(BaseModel):
    values: dict[str, str] = {}  # no personal fields: the client keeps those
    edited: list[str] = []  # the fields the user set by hand
    messages: list[PanelMessage] = Field(min_length=1)  # the panel chat, ending with the user's new message
    summary: str = ""  # from open
    question: str | None = None

    @field_validator("messages")
    @classmethod
    def _ends_with_the_user(cls, messages):
        if messages[-1].role != "user":
            raise ValueError("the last message must be the user's")
        return messages


@app.get("/health")
async def health():
    try:
        forms = len(await store.forms())
    except NoForms:
        forms = 0
    return {"status": "ok", "forms": forms}


@app.get("/tools")
async def tools():
    return {"form": await store.forms()}


async def _form(form_id: str) -> dict:
    form = await store.get(form_id)
    if form is None:
        raise HTTPException(404, f"no form {form_id!r}")
    return form


async def _in_turn(turn):
    """Runs turn() once a slot is free; a 503 when every slot and place in line is taken."""
    try:
        async with aclosing(admission.wait_turn()) as places:
            async for _ in places:
                pass
    except Full:
        eta = admission.eta(admission.waiting + 1)
        return JSONResponse(status_code=503, headers={"Retry-After": str(round(eta or 30))},
                            content={"busy": True, "detail": "busy: every slot and place in line is taken; try again shortly"})
    start = time.monotonic()
    try:
        return await turn()
    except Exception as e:
        # answered here, inside CORS: the catch-all handler runs outside it, where the browser can't read it
        log.exception("panel turn failed")
        return await _as_json_error(None, e)
    finally:
        admission.release(time.monotonic() - start)


@app.post("/tools/form/{form_id}/open")
async def open_form(form_id: str, req: OpenRequest):
    form = await _form(form_id)
    history = [m.model_dump() for m in req.history]
    return await _in_turn(lambda: open_turn(get_llm(), form, history, req.question))


@app.post("/tools/form/{form_id}/chat")
async def chat_form(form_id: str, req: ChatRequest):
    form = await _form(form_id)
    messages = [m.model_dump() for m in req.messages]
    return await _in_turn(lambda: chat_turn(get_llm(), form, req.values, req.edited, messages, req.summary,
                                            req.question))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)

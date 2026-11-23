"""One panel turn: one structured-output LLM call (no tools), then the server's checks on what it wrote, and
the guardrails, as bdc-assist's: a chat turn's message is screened beside the drafting call, and every draft
and revision is checked before it's returned. A block or a rejection changes nothing.

open: the first draft, from the main chat (history) and its last question, plus a summary of the main chat
that later turns carry instead of it. chat: a revision, from the panel chat. Personal fields never reach the
LLM, not even their names (only their labels, as fields the user fills in), and nothing it writes into them is
kept. Stateless: the client sends everything a turn needs.
"""

import asyncio
import math
import re
from datetime import date
from pathlib import Path

import yaml
from langchain_core.exceptions import ModelRefusalError
from pydantic import BaseModel

PROMPTS = yaml.safe_load((Path(__file__).parent / "prompts.yaml").read_text(encoding="utf-8"))
BLOCKED_REPLY = PROMPTS["blocked_reply"]  # the message was screened out, or the provider filtered the turn
REJECTED_REPLY = PROMPTS["rejected_reply"]  # the output check rejected the draft
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class FieldValue(BaseModel):
    name: str
    value: str


class ChatOut(BaseModel):
    # a list of pairs, not a {name: value} object: strict JSON-schema output takes no open-ended keys
    values: list[FieldValue]
    reply: str


class OpenOut(ChatOut):
    summary: str


def shown(form: dict, values: dict[str, str]) -> list[dict]:
    """The fields the form shows for these values: each `when` entry's field has one of its choices."""
    return [f for f in form["fields"] if all(values.get(k) in c for k, c in (f.get("when") or {}).items())]


def _valid(field: dict, value: str) -> str | None:
    """The value as the form takes it, or None when it doesn't fit the field."""
    if not value:
        return ""
    kind = field["kind"]
    if kind == "dropdown":  # the exact choice, whatever case or stray spaces the LLM wrote
        return next((c for c in field.get("choices") or () if c.casefold() == value.casefold()), None)
    if kind == "date":
        try:
            return value if _DATE.fullmatch(value) and date.fromisoformat(value) else None
        except ValueError:
            return None
    if kind == "number":
        try:
            return value if math.isfinite(float(value)) else None
        except ValueError:
            return None
    if kind == "checkbox":
        return "true" if value.lower() == "true" else None
    return value


def check(form: dict, old: dict[str, str], proposed: dict[str, str]) -> dict[str, str]:
    """old with proposed applied: unknown and personal fields dropped, a value that doesn't fit its field keeps
    the old one, blank unsets, then fields the form wouldn't show are cleared. Only non-blank values."""
    fields = {f["name"]: f for f in form["fields"] if not f.get("personal")}
    values = {k: str(v) for k, v in old.items() if k in fields}
    for name, value in proposed.items():
        if name in fields and (ok := _valid(fields[name], str(value or "").strip())) is not None:
            values[name] = ok
    values = {k: v for k, v in values.items() if v}
    while True:  # clearing a dropdown can hide the fields of its sections in turn
        keep = {f["name"] for f in shown(form, values)}
        trimmed = {k: v for k, v in values.items() if k in keep}
        if trimmed == values:
            return values
        values = trimmed


def _field_line(f: dict) -> str:
    line = f'- {f["name"]}: "{f["label"]}" ({f["kind"]}{", required" if f["required"] else ""})'
    if f.get("choices"):
        line += "; choices: " + " | ".join(f["choices"])
    for k, choices in (f.get("when") or {}).items():
        line += f"; only when {k} is " + " or ".join(choices)
    return line


def _system(form: dict) -> str:
    personal = [f["label"] for f in form["fields"] if f.get("personal")]
    return PROMPTS["system"].format(personal=", ".join(personal) or "(none)")


def _common(form: dict, question: str | None) -> dict:
    return {"title": form["title"], "question": question or "(none)",
            "fields": "\n".join(_field_line(f) for f in form["fields"] if not f.get("personal"))}


async def _ask(llm, model, form: dict, human: str):
    return await llm.with_structured_output(model).ainvoke([("system", _system(form)), ("human", human)])


def _filtered(e: Exception) -> bool:
    # the provider refused the turn: a block, not an error. Azure's prompt filter says so in the error's code only,
    # not its repr; langchain raises ModelRefusalError when the model refuses or its reply is stopped by the filter
    return (getattr(e, "code", None) == "content_filter" or "content_filter" in repr(e)
            or isinstance(e, ModelRefusalError))


def _lines(values: dict[str, str]) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in values.items()) or "(none yet)"


async def _verdict(llm, msgs) -> str | None:
    """A guardrail's answer, lowercased; None when the provider's content filter refused the check itself."""
    try:
        return (await llm.ainvoke(msgs)).text.strip().lower()
    except Exception as e:
        if not _filtered(e):
            raise
        return None


async def _blocked_message(llm, form: dict, text: str) -> bool:
    """bdc-assist's input guardrail for a panel message: yes blocks it; so does a filtered check."""
    v = await _verdict(llm, [("system", PROMPTS["input_guardrail_system"]),
                             ("human", PROMPTS["input_guardrail_human"].format(title=form["title"], input=text))])
    return v is None or v.startswith("yes")


async def _rejected(llm, form: dict, context: str, request: str, values: dict[str, str], reply: str) -> bool:
    """bdc-assist's output guardrail for a draft, as a screen for what has no place in a help request: anything
    but yes rejects it. context: what the main chat was about. values: after check(), so never a personal field's."""
    v = await _verdict(llm, [("human", PROMPTS["output_guardrail_human"].format(
        title=form["title"], context=context, request=request, values=_lines(values), reply=reply))])
    return v is None or not v.startswith("yes")


async def _draft(llm, model, form: dict, human: str):
    """The drafting call; None when the provider's content filter refused it."""
    try:
        return await _ask(llm, model, form, human)
    except Exception as e:
        if not _filtered(e):
            raise
        return None


async def open_turn(llm, form: dict, history: list[dict], question: str | None) -> dict:
    """The first draft -> {summary, values, reply, blocked}. history: the main chat, [{role, content}]. No input
    screen: the main chat went through bdc-assist's."""
    chat = "\n".join(f'{"User" if m["role"] == "user" else "Assistant"}: {m["content"]}' for m in history)
    human = PROMPTS["open_human"].format(**_common(form, question), history=chat or "(empty)")
    out = await _draft(llm, OpenOut, form, human)
    if out is None:
        return {"summary": "", "values": {}, "reply": BLOCKED_REPLY, "blocked": True}
    summary, values, reply = out.summary.strip(), check(form, {}, {v.name: v.value for v in out.values}), out.reply.strip()
    if await _rejected(llm, form, summary or question or "(unknown)", question or "(none: opened from the chat)",
                       values, reply):
        return {"summary": "", "values": {}, "reply": REJECTED_REPLY, "blocked": True}
    return {"summary": summary, "values": values, "reply": reply, "blocked": False}


async def chat_turn(llm, form: dict, values: dict[str, str], edited: list[str], messages: list[dict],
                    summary: str, question: str | None) -> dict:
    """A revision -> {values, reply, blocked}. messages: the panel chat, [{role: user|tool, text}], the user's last."""
    old = check(form, values, {})  # what the request carried, minus personal and unknown fields
    # set by hand, cleared ones included; never a personal field's name
    hand = [n for n in edited if any(f["name"] == n and not f.get("personal") for f in form["fields"])]
    chat = "\n".join(f'{"User" if m["role"] == "user" else "You"}: {m["text"]}' for m in messages)
    human = PROMPTS["chat_human"].format(**_common(form, question), summary=summary or "(none)", messages=chat,
                                         values=_lines(old), edited=", ".join(hand) or "(none)")
    # the screen runs beside the draft, as bdc-assist's beside classify: no longer a wait
    blocked, out = await asyncio.gather(_blocked_message(llm, form, messages[-1]["text"]),
                                        _draft(llm, ChatOut, form, human))
    if blocked or out is None:
        return {"values": old, "reply": BLOCKED_REPLY, "blocked": True}
    new, reply = check(form, old, {v.name: v.value for v in out.values}), out.reply.strip()
    if await _rejected(llm, form, summary or question or "(unknown)", messages[-1]["text"], new, reply):
        return {"values": old, "reply": REJECTED_REPLY, "blocked": True}
    return {"values": new, "reply": reply, "blocked": False}

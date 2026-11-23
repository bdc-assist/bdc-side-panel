"""Form definitions: bdc-llm's ```form blocks, as bdc-doc-mcp's GET /chunks?doc_type=form serves them, turned
into what the panel shows and the LLM drafts.

    FormDef   = {id, title, url, fields: [FormField]}
    FormField = {name, label, kind, required, choices? (dropdowns), when? (section fields), personal? (True only)}
    when      = {field name: [choice, ...]}: shown only while every named field has one of its choices

A field's `sections` become fields right after it, each shown while that field has one of the section's choices.
"""

import logging
import time
from pathlib import PurePosixPath

import httpx
import yaml

from .config import LEFT_OUT_FIELDS, PERSONAL_FIELDS

log = logging.getLogger(__name__)

# the export's three type vocabularies (Freshdesk, HubSpot, the newsletter's plain HTML) -> the panel's kinds;
# a field with choices is a dropdown whatever its type, and any other type is text
KINDS = {
    "custom_paragraph": "paragraph", "PARAGRAPH": "paragraph",
    "custom_date": "date", "DATE": "date",
    "custom_number": "number",
    "custom_checkbox": "checkbox",
    "custom_dropdown": "dropdown", "default_ticket_type": "dropdown", "DROPDOWN": "dropdown",
    "custom_text": "text", "default_requester": "text", "TEXT": "text", "text": "text", "email": "text",
}


def _fields(entries: list, when: dict) -> list[dict]:
    out = []
    for e in entries:
        name = str(e["name"])
        if name in LEFT_OUT_FIELDS:
            continue
        label = str(e.get("label") or name)
        # Get Help lists its dropdown's prompt ("Select the Type of Assistance Needed") as a choice
        choices = [str(c) for c in e.get("choices") or [] if str(c).strip().casefold() != label.strip().casefold()]
        kind = "dropdown" if choices else KINDS.get(str(e.get("type")))
        if kind is None:
            log.warning("unknown field type %r of %s: drafted as text", e.get("type"), name)
            kind = "text"
        field = {"name": name, "label": label, "kind": kind, "required": bool(e.get("required"))}
        if choices:
            field["choices"] = choices
        if when:
            field["when"] = when
        if name in PERSONAL_FIELDS:
            field["personal"] = True
        out.append(field)
        for section in e.get("sections") or []:
            out += _fields(section.get("fields") or [], {**when, name: [str(c) for c in section.get("choices") or []]})
    return out


def to_form(content: str, metadata: dict) -> dict:
    """One form chunk -> FormDef. ValueError when the block isn't a mapping with a url and a list of fields."""
    block = yaml.safe_load(content)
    if not isinstance(block, dict) or not block.get("url") or not isinstance(block.get("fields"), list):
        raise ValueError("expected a mapping with url and a list of fields")
    source = PurePosixPath(metadata["source"].split("#")[0])
    return {"id": source.stem, "title": metadata.get("title") or source.stem, "url": str(block["url"]),
            "fields": _fields(block["fields"], {})}


def to_forms(chunks: list[dict]) -> list[dict]:
    """/chunks' [{content, metadata}] -> FormDefs. A block that doesn't parse is logged and skipped; a second form
    whose page has a name already taken is <folder>-<name>."""
    forms = []
    for chunk in chunks:
        try:
            form = to_form(chunk["content"], chunk["metadata"])
        except Exception as e:
            log.warning("form %s skipped: %s: %s", chunk.get("metadata", {}).get("source"), type(e).__name__, e)
            continue
        if any(f["id"] == form["id"] for f in forms):
            form["id"] = f"{PurePosixPath(chunk['metadata']['source']).parent.name}-{form['id']}"
        forms.append(form)
    return forms


class NoForms(Exception):
    """No form list yet: bdc-doc-mcp hasn't answered since startup."""


async def _fetch_chunks(url: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{url}/chunks", params={"doc_type": "form"})
        resp.raise_for_status()
        return resp.json()


class FormStore:
    """The forms, fetched from bdc-doc-mcp (url) when first needed and again once older than `refresh` seconds. A
    failed fetch keeps the last list, so doc-mcp going down costs nothing until it's back; with no list yet, NoForms."""

    def __init__(self, url: str, refresh: float, fetch=None):
        self.url, self.refresh, self.fetch = url, refresh, fetch or _fetch_chunks
        self._forms: list[dict] | None = None
        self._at = 0.0

    async def forms(self) -> list[dict]:
        if self._forms is None or time.monotonic() - self._at >= self.refresh:
            try:
                self._forms, self._at = to_forms(await self.fetch(self.url)), time.monotonic()
            except Exception as e:
                log.warning("forms not refreshed from %s: %s: %s", self.url, type(e).__name__, e)
        if self._forms is None:
            raise NoForms
        return self._forms

    async def get(self, form_id: str) -> dict | None:
        return next((f for f in await self.forms() if f["id"] == form_id), None)

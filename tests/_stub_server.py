"""Throwaway stub for trying the web client's side panel (bdc-assist-client) without real services: serves the real
api app on :8013 with the forms from tests/fixtures/forms.json (no bdc-doc-mcp) and a scripted LLM (no model). Run:
uv run python tests/_stub_server.py, then VITE_SIDE_PANEL_URL=http://127.0.0.1:8013 npm run dev in bdc-assist-client
(with bdc-assist's own stub, VITE_API_URL=http://127.0.0.1:8011: its get_help keyword offers the Get Help form).

The real turn code runs on what the scripted LLM writes: the checks (personal and unknown fields dropped, values
that don't fit their field), the shown/hidden sections and the guardrail plumbing. What it writes:
  open   each dropdown the choice sharing most words with the question (none shared: its first if required, else
         left blank), then every shown required field: text = the question (cut to 60 characters), paragraph =
         "From the chat: <question>" (both left blank without a question), date 2027-03-01, number 1; a stub
         summary and reply
  chat   fills the first shown empty field, in form order, and says which
A keyword (a whole word) in a panel message picks another path:
  block    the input screen blocks it: canned reply, values unchanged
  reject   the output check rejects the revision: canned reply, values unchanged
Each drafting call takes about a second, so the panel's busy state shows.
"""

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # runnable from anywhere

import uvicorn
from langchain_core.messages import AIMessage

from side_panel import api
from side_panel.forms import FormStore, to_forms
from side_panel.turn import shown

PORT = 8013  # beside a real bdc-side-panel on 8012, as bdc-assist's stub (8011) is beside its api (8010)
CHUNKS = json.loads((Path(__file__).parent / "fixtures" / "forms.json").read_text(encoding="utf-8"))
FORMS = {f["title"]: f for f in to_forms(CHUNKS)}


def _has(text: str, word: str) -> bool:
    return re.search(rf"\b{word}\b", text, re.I) is not None


def _after(text: str, label: str) -> str:
    """The rest of the prompt line that starts with label."""
    m = re.search(rf"^{re.escape(label)}\s*(.*)$", text, re.M)
    return m.group(1).strip() if m else ""


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z]{4,}", s.lower()))  # 4+ letters: "login", not "a" or "my"


def _open(form: dict, question: str) -> dict:
    """The first draft's values: dropdowns first (they decide which sections show), then the required fields."""
    values = {}
    for f in form["fields"]:
        if f["kind"] == "dropdown" and not f.get("personal") and f in shown(form, values):
            best = max(f["choices"], key=lambda c: len(_words(c) & _words(question)))  # ties: the first
            if f["required"] or _words(best) & _words(question):
                values[f["name"]] = best
    text = question[:60].rstrip() + ("…" if len(question) > 60 else "")
    fill = {"date": "2027-03-01", "number": "1"}
    if question:  # opened from the header's Get help: no question to write from
        fill |= {"text": text, "paragraph": f"From the chat: {question}"}
    for f in shown(form, values):
        if f["required"] and not f.get("personal") and f["name"] not in values and f["kind"] in fill:
            values[f["name"]] = fill[f["kind"]]
    return values


def _next(form: dict, values: dict) -> dict | None:
    """The first shown field still empty, personal and checkbox fields aside."""
    return next((f for f in shown(form, values) if f["name"] not in values and not f.get("personal")
                 and f["kind"] != "checkbox"), None)


class ScriptedLLM:
    """Stands in for get_llm(): the guardrails' ainvoke and the drafting call's with_structured_output. Reads the
    form, question and values back out of the real prompts (side_panel/prompts.yaml)."""

    async def ainvoke(self, msgs):
        text = msgs[-1][1]
        if msgs[0][0] == "system":  # the input screen: yes blocks
            return AIMessage("Yes" if _has(text, "block") else "No")
        return AIMessage("No" if _has(text, "reject") else "Yes")  # the output check: anything but yes rejects

    def with_structured_output(self, model):
        class Runner:
            async def ainvoke(self, msgs):
                await asyncio.sleep(1)
                human = msgs[-1][1]
                form = FORMS[_after(human, "The form:")]
                if "summary" in model.model_fields:  # open
                    question = _after(human, "The user's last question there, on its own:")
                    question = "" if question == "(none)" else question
                    values = _open(form, question)
                    return model(values=[{"name": k, "value": v} for k, v in values.items()],
                                 reply=f"(stub) Here's a first draft of {form['title']}; tell me what to change.",
                                 summary=f"(stub) The user asked: {question or 'nothing yet'}")
                block = human.split("Its current values:", 1)[1].split("Fields the user set by hand", 1)[0]
                values = dict(re.findall(r"^- (\S+): (.*)$", block, re.M))
                f = _next(form, values)
                if not f:
                    return model(values=[], reply="(stub) Every field is filled in already.")
                value = (f["choices"][0] if f["kind"] == "dropdown" else
                         {"date": "2027-03-01", "number": "1"}.get(f["kind"], f"(stub) {f['label'][:40]}"))
                return model(values=[{"name": f["name"], "value": value}],
                             reply=f"(stub) Filled in “{f['label']}”.")

        return Runner()


async def _fixture(url):
    return CHUNKS


api.store = FormStore("stub", refresh=600, fetch=_fixture)
api.get_llm = lambda: ScriptedLLM()

if __name__ == "__main__":
    print(f"bdc-side-panel stub on http://127.0.0.1:{PORT}: {len(FORMS)} fixture forms, scripted LLM")
    print("panel keywords: block, reject")
    uvicorn.run(api.app, host="127.0.0.1", port=PORT)

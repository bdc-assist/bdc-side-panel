# bdc-side-panel workflow

The mermaid source of `workflow.png`: the service's endpoints, who calls them, and one panel turn
(`side_panel/turn.py`), drawn like bdc-assist's workflow pictures. Drawn by hand, not from the code: keep it in
step with `api.py`, `forms.py` and `turn.py`, then redraw the picture (mermaid.ink renders it, so the text below
leaves this machine):

    uv run python -c "import re, pathlib as p; d = p.Path('docs'); m = re.search(r'```mermaid\n(.*?)```', (d / 'workflow.md').read_text(encoding='utf-8'), re.S); from langchain_core.runnables.graph_mermaid import draw_mermaid_png; (d / 'workflow.png').write_bytes(draw_mermaid_png(m[1]))"

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	assist("bdc-assist: form_offer"):::ext
	client("web client"):::ext
	health("GET /health")
	tools("GET /tools")
	open("POST /tools/form/{id}/open")
	chat("POST /tools/form/{id}/chat")
	forms("the forms")
	mcp("bdc-doc-mcp: GET /chunks?doc_type=form"):::ext
	subgraph turn ["a panel turn"]
	input_guardrail("input_guardrail")
	draft("draft")
	gate("gate")
	check("check")
	output_guardrail("output_guardrail")
	end
	reply(["values, reply, blocked"]):::last
	assist --> tools;
	client --> tools;
	client --> open;
	client --> chat;
	health --> forms;
	tools --> forms;
	forms -. &nbsp;every 10 min&nbsp; .-> mcp;
	open --> draft;
	chat --> input_guardrail;
	chat --> draft;
	input_guardrail --> gate;
	draft --> gate;
	gate -. &nbsp;blocked or filtered&nbsp; .-> reply;
	gate -. &nbsp;passed&nbsp; .-> check;
	check --> output_guardrail;
	output_guardrail -. &nbsp;passed or rejected&nbsp; .-> reply;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef last fill:#bfb6fc
	classDef agent fill:#fecaca,stroke:#f87171
	classDef ext fill:#bbf7d0,stroke:#4ade80
	class draft agent
```

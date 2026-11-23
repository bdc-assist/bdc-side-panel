# When the chat offers a form

The mermaid source of `form_offer.png`: how an answer in the main chat gets a form's card. bdc-assist's
`form_offer` step (its BDC config, `examples/bdc/nodes.py` and `prompts.yaml`) decides, and the web client
(`src/lib/bdc-adapter.ts`) shows the card and sends back the forms it was offered; this service only lists the
forms and drafts the one the user opens. Drawn by hand: keep it in step with those, then redraw the picture
(mermaid.ink renders it, so the text below leaves this machine):

    uv run python -c "import re, pathlib as p; d = p.Path('docs'); m = re.search(r'```mermaid\n(.*?)```', (d / 'form_offer.md').read_text(encoding='utf-8'), re.S); from langchain_core.runnables.graph_mermaid import draw_mermaid_png; (d / 'form_offer.png').write_bytes(draw_mermaid_png(m[1]))"

Pastel red: an LLM call, with the prompt it uses. One card per answer at most. A refused question never gets
here, and a rejected answer drops the offer.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	start(["bdc-assist: an answer is done (its form_offer step)"]):::first
	forms("GET /tools: this service's forms")
	links("the forms the answer links or cites: its sources, and the links in its text")
	pick("form_offer_pick: which one is the question about?"):::llm
	need("form_offer: does the user explicitly need one?"):::llm
	seen("offered before in this conversation? The client sends them back, each with its question")
	again("form_offer_again: a new request, or the one it was offered for?"):::llm
	card(["a card under the answer: opening it drafts the form here, as a new draft"]):::last
	none(["no card"]):::last
	start --> forms;
	forms -. &nbsp;none, this service is down&nbsp; .-> none;
	forms --> links;
	links -. &nbsp;one&nbsp; .-> seen;
	links -. &nbsp;several&nbsp; .-> pick;
	links -. &nbsp;none&nbsp; .-> need;
	pick -. &nbsp;one of them&nbsp; .-> seen;
	pick -. &nbsp;none fits&nbsp; .-> none;
	need -. &nbsp;one&nbsp; .-> seen;
	need -. &nbsp;none, most questions&nbsp; .-> none;
	seen -. &nbsp;no&nbsp; .-> card;
	seen -. &nbsp;yes&nbsp; .-> again;
	again -. &nbsp;new&nbsp; .-> card;
	again -. &nbsp;same, or unsure&nbsp; .-> none;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
	classDef llm fill:#fecaca,stroke:#f87171
```

"""Env-driven settings. The LLM setup is copied from bdc-assist (r_assist/config.py), with the same var
names, so one .env's values serve both; not imported: bdc-side-panel doesn't depend on bdc-assist."""

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()  # real env vars win over .env

# the port `python -m side_panel.api` binds (bdc-assist's demo scripts read it too); not PORT or API_PORT, so a
# .env copied from bdc-assist's doesn't move it
PORT = int(os.getenv("SIDE_PANEL_PORT", "8012"))
# bdc-doc-mcp, whose GET /chunks?doc_type=form serves bdc-llm's form blocks
DOC_MCP_URL = os.getenv("DOC_MCP_URL", "http://127.0.0.1:8001").rstrip("/")
FORMS_REFRESH_SECONDS = float(os.getenv("FORMS_REFRESH_SECONDS", "600"))
# browser origins allowed to call the API, comma-separated; "*" = any (demo default)
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
# admission (admission.Admission): turns that run at once, and how many more may wait, before a 503. 0 = no cap
MAX_RUNNING_CHATS = int(os.getenv("MAX_RUNNING_CHATS", "0"))
MAX_WAITING_CHATS = int(os.getenv("MAX_WAITING_CHATS", "0"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "WARNING")

# Personal fields: shown in the panel and filled in by hand only. Their values never reach the LLM and the LLM
# never fills them. shortcut: a placeholder (the name and email fields) until the real list is defined.
PERSONAL_FIELDS = frozenset({"cf_requestors_name", "requester", "full_name", "email", "first_name", "name"})
# Left-out fields: never in GET /tools, so never drafted or linked; the user completes them on the real form.
# The export already leaves most out on purpose (e.g. Get Help's consent box); this is the one it carries.
LEFT_OUT_FIELDS = frozenset({"cf_by_submitting_this_form_i_agree_to_the_terms_and_conditions_of_this_offering"})

COMPLETION_TEMPERATURE = float(os.getenv("COMPLETION_TEMPERATURE", "0"))  # every get_llm() provider
# unset for non-reasoning models (gpt-4o, gpt-4o-mini); none|low|medium|high for reasoning ones. openai/azure/vllm only.
COMPLETION_REASONING_EFFORT = os.getenv("COMPLETION_REASONING_EFFORT") or None


def _self_hosted_key():
    # self-hosted vLLM ignores the key, but the openai client refuses to start without one
    return os.getenv("OPENAI_API_KEY") or "EMPTY"


def _provider(kind: str) -> str:
    explicit = os.getenv(f"{kind}_MODEL_PROVIDER")
    if explicit:
        # split on '#': a trailing .env comment can survive into the value and is unreadable as an error
        return explicit.split("#")[0].strip().lower()
    url = os.getenv(f"{kind}_URL")
    if url:
        if "ollama" in url:
            return "ollama"
        return "azure" if "azure.com" in url else "vllm"
    return "openai"


def _openai_kwargs() -> dict:
    """Temperature and reasoning for the OpenAI-family clients, by COMPLETION_REASONING_EFFORT (see bdc-assist's
    r_assist/config.py for what each model accepted)."""
    if not COMPLETION_REASONING_EFFORT:
        return {"temperature": COMPLETION_TEMPERATURE}
    if COMPLETION_REASONING_EFFORT == "none":
        return {"temperature": COMPLETION_TEMPERATURE, "reasoning_effort": "none"}
    return {"reasoning_effort": COMPLETION_REASONING_EFFORT, "use_responses_api": True}


@lru_cache
def get_llm():
    provider = _provider("COMPLETION")
    url = os.getenv("COMPLETION_URL")
    model = os.getenv("COMPLETION_MODEL")
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model or "gpt-4o-mini", **_openai_kwargs())
    if provider == "azure":
        # COMPLETION_MODEL is the *deployment* name here, and COMPLETION_URL the resource root
        from langchain_openai import AzureChatOpenAI
        return AzureChatOpenAI(
            azure_endpoint=url, azure_deployment=model, **_openai_kwargs(),
            api_version=os.getenv("AZURE_API_VERSION", "2024-10-21"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"),
        )
    if provider == "vllm":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(base_url=url, model=model, **_openai_kwargs(), api_key=_self_hosted_key())
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(base_url=url, model=model, temperature=COMPLETION_TEMPERATURE)
    raise ValueError(f"Unsupported COMPLETION_MODEL_PROVIDER: {provider}")

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from side_panel.forms import FormStore, NoForms, to_forms

CHUNKS = json.loads((Path(__file__).parent / "fixtures" / "forms.json").read_text(encoding="utf-8"))
FORMS = {f["id"]: f for f in to_forms(CHUNKS)}


def field(form, name):
    return next(f for f in FORMS[form]["fields"] if f["name"] == name)


def test_five_forms_by_page():
    assert set(FORMS) == {"get-help", "contact-bdc", "costs", "publication-submission", "stay-connected"}
    assert FORMS["get-help"]["title"] == "Get Help"
    assert FORMS["get-help"]["url"] == "https://biodatacatalyst.nhlbi.nih.gov/help/get-help/"


def test_sections_follow_their_field_with_when():
    names = [f["name"] for f in FORMS["get-help"]["fields"]]
    assert names[names.index("ticket_type") + 1] == "cf_paper_title_or_working_title"
    assert field("get-help", "cf_journal_name")["when"] == {"ticket_type": ["Publishing Research"]}
    assert "when" not in field("get-help", "cf_subject")
    assert "sections" not in field("get-help", "ticket_type")


def test_placeholder_choice_dropped():
    choices = field("get-help", "ticket_type")["choices"]
    assert choices[0] == "Exploring/Accessing BDC-hosted Data" and len(choices) == 12


def test_every_export_type_has_a_kind(caplog):
    kinds = {f["kind"] for form in FORMS.values() for f in form["fields"]}
    assert kinds <= {"text", "paragraph", "date", "number", "checkbox", "dropdown"}
    assert field("costs", "cf_cloud_credits_estimated_need")["kind"] == "number"
    assert field("costs", "cf_cloud_credits_support_during_course_from_bdc")["kind"] == "checkbox"
    assert field("get-help", "cf_expected_date_of_publication_if_known")["kind"] == "date"
    assert field("contact-bdc", "select_type_of_contact")["kind"] == "dropdown"
    assert field("get-help", "cf_submitted_description")["kind"] == "paragraph"
    assert field("get-help", "cf_subject")["kind"] == "text" and field("get-help", "cf_subject")["required"] is True
    to_forms(CHUNKS)
    assert "unknown field type" not in caplog.text  # every type the five forms use today is known


def test_unknown_type_is_text_with_a_warning(caplog):
    [form] = to_forms([{"content": "url: https://a/\nfields:\n  - {name: x, label: X, type: custom_slider}",
                        "metadata": {"source": "a.md#form", "title": "A"}}])
    assert form["fields"] == [{"name": "x", "label": "X", "kind": "text", "required": False}]
    assert "custom_slider" in caplog.text


def test_consent_box_left_out_other_checkboxes_kept():
    names = {f["name"] for f in FORMS["costs"]["fields"]}
    assert "cf_by_submitting_this_form_i_agree_to_the_terms_and_conditions_of_this_offering" not in names
    assert "cf_cloud_credits_certify_insufficient_funding" in names


def test_personal_fields_flagged():
    assert field("get-help", "requester")["personal"] is True
    assert field("stay-connected", "email")["personal"] is True
    assert "personal" not in field("get-help", "cf_subject")


def test_a_bad_block_is_skipped_not_fatal(caplog):
    forms = to_forms([{"content": "url: [", "metadata": {"source": "x.md#form", "title": "X"}}, *CHUNKS])
    assert len(forms) == 5 and "x.md#form" in caplog.text


def test_clashing_ids_get_their_folder():
    a = {"content": "url: https://a/\nfields: []", "metadata": {"source": "help/join.md#form", "title": "A"}}
    b = {"content": "url: https://b/\nfields: []", "metadata": {"source": "news/join.md#form", "title": "B"}}
    assert [f["id"] for f in to_forms([a, b])] == ["join", "news-join"]


def test_store_recovers_and_keeps_last_good():
    calls = []

    async def fetch(url):
        calls.append(url)
        if len(calls) in (1, 3):
            raise httpx.ConnectError("down")
        return CHUNKS

    store = FormStore("http://mcp", refresh=0, fetch=fetch)
    with pytest.raises(NoForms):
        asyncio.run(store.forms())  # down, nothing yet
    assert len(asyncio.run(store.forms())) == 5  # up: loaded without a restart
    assert len(asyncio.run(store.forms())) == 5  # refresh fails: last good kept
    assert asyncio.run(store.get("costs"))["id"] == "costs"
    assert asyncio.run(store.get("nope")) is None
    assert calls[0] == "http://mcp"


def test_store_waits_out_its_refresh_interval():
    calls = []

    async def fetch(url):
        calls.append(url)
        return CHUNKS

    store = FormStore("http://mcp", refresh=600, fetch=fetch)
    asyncio.run(store.forms())
    asyncio.run(store.forms())
    assert len(calls) == 1

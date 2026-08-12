from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.integrations.feishu.cards import (
    FeishuCardView,
    render_private_resolution,
    render_shared_card,
)
from app.models.enums import AlertStatus


def card_view(status: AlertStatus) -> FeishuCardView:
    return FeishuCardView(
        binding_id=uuid4(),
        revision=1,
        nonce="test-nonce",
        alert_id=uuid4(),
        alert_name="SyntheticHighCPUUsage",
        service="synthetic-checkout",
        severity="critical",
        status=status,
        summary=None,
        knowledge_missing=False,
        error_code=None,
        case_status=None,
        actor_label=None,
        decision_action=None,
        decision_at=None,
        web_url="https://alerts.example.test/alerts/test",
    )


def component_tags(value: Any) -> list[str]:
    if isinstance(value, dict):
        tags = [value["tag"]] if isinstance(value.get("tag"), str) else []
        for child in value.values():
            tags.extend(component_tags(child))
        return tags
    if isinstance(value, list):
        return [tag for child in value for tag in component_tags(child)]
    return []


@pytest.mark.parametrize(
    ("status", "expected_actions"),
    [
        (AlertStatus.RECEIVED, ["start"]),
        (AlertStatus.FAILED, ["retry"]),
        (AlertStatus.WAITING_FOR_APPROVAL, ["approve", "reject"]),
    ],
)
def test_json_2_cards_use_direct_buttons_instead_of_legacy_action_container(
    status: AlertStatus,
    expected_actions: list[str],
) -> None:
    card = render_shared_card(card_view(status))

    assert card["schema"] == "2.0"
    assert "action" not in component_tags(card)
    assert [
        element["value"]["action"]
        for element in card["body"]["elements"]
        if element.get("tag") == "button" and "value" in element
    ] == expected_actions


def test_private_resolution_card_removes_business_actions() -> None:
    view = replace(
        card_view(AlertStatus.REJECTED),
        actor_label="Primary on-call",
        decision_action="reject",
        decision_at=datetime.now(UTC),
    )

    card = render_private_resolution(view)

    assert card["header"]["title"]["content"].startswith("已驳回：")
    assert "form" not in component_tags(card)
    assert not [
        element
        for element in card["body"]["elements"]
        if element.get("tag") == "button" and "value" in element
    ]
    assert "Primary on-call" in card["body"]["elements"][0]["content"]


def test_reanalysis_form_has_unique_named_required_components() -> None:
    card = render_shared_card(card_view(AlertStatus.WAITING_FOR_APPROVAL))
    form = next(element for element in card["body"]["elements"] if element["tag"] == "form")
    input_component = next(element for element in form["elements"] if element["tag"] == "input")
    submit = next(
        element for element in form["elements"] if element.get("action_type") == "form_submit"
    )

    assert form["name"] == "reanalysis_feedback"
    assert input_component["name"] == "feedback"
    assert input_component["required"] is True
    assert submit["name"] == "reanalyze_submit"
    assert len({form["name"], input_component["name"], submit["name"]}) == 3

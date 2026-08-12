from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.models.enums import AlertStatus, KnowledgeSyncStatus


@dataclass(frozen=True, slots=True)
class FeishuCardView:
    binding_id: UUID
    revision: int
    nonce: str
    alert_id: UUID
    alert_name: str
    service: str
    severity: str
    status: AlertStatus
    summary: str | None
    knowledge_missing: bool
    error_code: str | None
    case_status: KnowledgeSyncStatus | None
    actor_label: str | None
    decision_action: str | None
    decision_at: datetime | None
    web_url: str


def render_shared_card(view: FeishuCardView) -> dict[str, Any]:
    status_text = str(getattr(view.status, "value", view.status)).replace("_", " ")
    lines = [
        f"**服务：** {escape_text(view.service)}",
        f"**级别：** {escape_text(view.severity)}",
        f"**状态：** {escape_text(status_text)}",
    ]
    if view.summary:
        lines.append(f"**诊断摘要：** {escape_text(view.summary[:1200])}")
    if view.knowledge_missing:
        lines.append("**证据缺失：** 知识检索不可用或未返回可用来源")
    if view.error_code:
        lines.append(f"**错误码：** `{escape_text(view.error_code)}`")
    if view.case_status:
        case_status = str(getattr(view.case_status, "value", view.case_status))
        lines.append(f"**案例同步：** {escape_text(case_status)}")
    if view.decision_action and view.actor_label and view.decision_at:
        lines.append(
            "**最近人工动作：** "
            f"{escape_text(view.actor_label)} · {escape_text(view.decision_action)} · "
            f"{view.decision_at.isoformat()}"
        )
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": "\n".join(lines)},
        {"tag": "hr"},
    ]
    actions = _actions_for_status(view)
    if actions:
        # Card JSON 2.0 removed the legacy ``action`` container. Interactive
        # components are direct body elements (or children of a supported
        # container such as ``form``).
        elements.extend(actions)
    if view.status == AlertStatus.WAITING_FOR_APPROVAL:
        elements.append(
            {
                "tag": "form",
                "name": "reanalysis_feedback",
                "elements": [
                    {
                        "tag": "input",
                        "name": "feedback",
                        "required": True,
                        "label": {"tag": "plain_text", "content": "重新分析反馈"},
                        "placeholder": {
                            "tag": "plain_text",
                            "content": "必填，最多 1,000 字符",
                        },
                        "max_length": 1000,
                    },
                    {
                        **_button(view, "提交反馈并重新分析", "reanalyze", "default"),
                        "action_type": "form_submit",
                        "name": "reanalyze_submit",
                    },
                ],
            }
        )
    elements.append(
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "在 Web 查看完整详情"},
            "type": "default",
            "url": view.web_url,
        }
    )
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "streaming_mode": False},
        "header": {
            "title": {"tag": "plain_text", "content": view.alert_name[:80]},
            "template": _header_template(view.status),
        },
        "body": {"elements": elements},
    }


def render_private_reminder(view: FeishuCardView) -> dict[str, Any]:
    card = render_shared_card(view)
    card["header"]["title"]["content"] = f"待确认：{view.alert_name}"[:80]
    return card


def render_private_resolution(view: FeishuCardView) -> dict[str, Any]:
    action_labels = {
        "approve": "已批准",
        "reject": "已驳回",
        "reanalyze": "已提交重新分析",
    }
    action = str(view.decision_action or "")
    result = action_labels.get(action, "已处理")
    lines = [
        f"**服务：** {escape_text(view.service)}",
        f"**处理结果：** {result}",
    ]
    if view.actor_label:
        lines.append(f"**处理人：** {escape_text(view.actor_label)}")
    if view.decision_at:
        lines.append(f"**处理时间：** {view.decision_at.isoformat()}")
    lines.append("后续状态请查看群共享卡片或 Web 详情。")
    return {
        "schema": "2.0",
        "config": {"update_multi": True, "streaming_mode": False},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"{result}：{view.alert_name}"[:80],
            },
            "template": "red" if action == "reject" else "green",
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": "\n".join(lines)},
                {"tag": "hr"},
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "在 Web 查看完整详情"},
                    "type": "default",
                    "url": view.web_url,
                },
            ]
        },
    }


def _actions_for_status(view: FeishuCardView) -> list[dict[str, Any]]:
    if view.status == AlertStatus.RECEIVED:
        return [_button(view, "启动诊断", "start", "primary")]
    if view.status == AlertStatus.FAILED:
        return [_button(view, "重试诊断", "retry", "primary")]
    if view.status == AlertStatus.WAITING_FOR_APPROVAL:
        return [
            _button(view, "批准", "approve", "primary", confirm=True),
            _button(view, "驳回", "reject", "danger", confirm=True),
        ]
    return []


def _button(
    view: FeishuCardView,
    label: str,
    action: str,
    button_type: str,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    button: dict[str, Any] = {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": button_type,
        "value": {
            "binding_id": str(view.binding_id),
            "action": action,
            "revision": view.revision,
            "nonce": view.nonce,
        },
    }
    if confirm:
        button["confirm"] = {
            "title": {"tag": "plain_text", "content": "确认操作"},
            "text": {"tag": "plain_text", "content": f"确定要{label}这份诊断吗？"},
        }
    return button


def _header_template(status: AlertStatus) -> str:
    if status in {AlertStatus.FAILED, AlertStatus.REJECTED}:
        return "red"
    if status == AlertStatus.COMPLETED:
        return "green"
    if status == AlertStatus.WAITING_FOR_APPROVAL:
        return "orange"
    return "blue"


def escape_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("`", "\\`").replace("<", "&lt;")

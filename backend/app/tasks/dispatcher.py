import logging

from app.models.enums import OutboxTopic
from app.models.outbox import OutboxMessage
from app.observability.logging import (
    bind_log_context,
    celery_correlation_headers,
)
from app.schemas.outbox import (
    CaseSyncMessage,
    WorkflowResumeMessage,
    WorkflowRetryMessage,
    WorkflowStartMessage,
)

logger = logging.getLogger(__name__)


class CeleryOutboxPublisher:
    """Translate a validated durable Outbox row into one Celery delivery."""

    def publish(self, message: OutboxMessage) -> None:
        topic = OutboxTopic(str(message.topic))
        task_id = f"outbox-{message.id}"
        correlation: dict[str, object] = {
            **message.correlation,
            "outbox_message_id": message.id,
        }
        with bind_log_context(**correlation):
            headers = celery_correlation_headers()
            if topic is OutboxTopic.WORKFLOW_START:
                from app.tasks.workflows import run_workflow_start

                payload = WorkflowStartMessage.model_validate(message.payload)
                run_workflow_start.apply_async(
                    args=[str(payload.workflow_run_id)],
                    task_id=task_id,
                    headers=headers,
                )
                aggregate_context = {"workflow_run_id": str(payload.workflow_run_id)}
            elif topic is OutboxTopic.WORKFLOW_RESUME:
                from app.tasks.workflows import run_workflow_resume

                payload = WorkflowResumeMessage.model_validate(message.payload)
                run_workflow_resume.apply_async(
                    args=[str(payload.workflow_run_id), str(payload.decision_id)],
                    task_id=task_id,
                    headers=headers,
                )
                aggregate_context = {"workflow_run_id": str(payload.workflow_run_id)}
            elif topic is OutboxTopic.WORKFLOW_RETRY:
                from app.tasks.workflows import run_workflow_retry

                payload = WorkflowRetryMessage.model_validate(message.payload)
                run_workflow_retry.apply_async(
                    args=[str(payload.workflow_run_id)],
                    task_id=task_id,
                    headers=headers,
                )
                aggregate_context = {"workflow_run_id": str(payload.workflow_run_id)}
            else:
                from app.tasks.cases import run_case_sync

                payload = CaseSyncMessage.model_validate(message.payload)
                run_case_sync.apply_async(
                    args=[str(payload.case_id)],
                    task_id=task_id,
                    headers=headers,
                )
                aggregate_context = {"case_id": str(payload.case_id)}

            logger.info(
                "celery.task.dispatched",
                extra={
                    "operation": topic.value,
                    "topic": topic.value,
                    "task_id": task_id,
                    **aggregate_context,
                },
            )

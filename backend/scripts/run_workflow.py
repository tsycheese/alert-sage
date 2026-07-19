import argparse
import asyncio
import json
import selectors
import sys
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.enums import HumanDecisionAction
from app.schemas.workflow import HumanDecisionCommand
from app.workflows.alert.checkpoint import open_alert_workflow_service
from app.workflows.alert.service import WorkflowExecutionResult


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the single-process alert workflow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Start a workflow and run to interruption")
    start.add_argument("--alert-id", required=True)
    start.add_argument("--idempotency-key", required=True)

    resume = subparsers.add_parser("resume", help="Resume a workflow with a human decision")
    resume.add_argument("--workflow-run-id", required=True)
    resume.add_argument(
        "--action",
        choices=[item.value for item in HumanDecisionAction],
        required=True,
    )
    resume.add_argument("--decision-key", required=True)
    resume.add_argument("--actor", required=True)
    resume.add_argument("--comment")
    return parser


async def run(args: argparse.Namespace) -> WorkflowExecutionResult:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with open_alert_workflow_service(
            session_factory=session_factory,
            database_url=settings.database_url,
        ) as service:
            if args.command == "start":
                return await service.start(
                    alert_id=UUID(args.alert_id),
                    idempotency_key=args.idempotency_key,
                )
            return await service.resume(
                workflow_run_id=UUID(args.workflow_run_id),
                command=HumanDecisionCommand(
                    idempotency_key=args.decision_key,
                    action=HumanDecisionAction(args.action),
                    comment=args.comment,
                ),
                actor=args.actor,
            )
    finally:
        await engine.dispose()


def result_payload(result: WorkflowExecutionResult) -> dict[str, Any]:
    return {
        "workflow_run_id": str(result.workflow_run_id),
        "thread_id": result.thread_id,
        "status": str(result.status),
        "current_node": result.current_node,
        "report_version": result.state.report_version,
        "final_status": result.state.final_status,
    }


def main() -> None:
    args = build_parser().parse_args()
    if sys.platform == "win32":
        result = asyncio.run(
            run(args),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        result = asyncio.run(run(args))
    print(json.dumps(result_payload(result), ensure_ascii=False))


if __name__ == "__main__":
    main()

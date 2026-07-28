import argparse
import asyncio
import json
import selectors
import sys
from dataclasses import dataclass
from time import monotonic
from typing import Any

import httpx

SCENARIO_VERSION = "v2.7a-checkout-database-regression"
ALERT_PAYLOAD: dict[str, object] = {
    "source": "demo",
    "external_alert_id": SCENARIO_VERSION,
    "alert_name": "HighCPUUsage",
    "service": "checkout-service",
    "instance": "checkout-api-01",
    "severity": "critical",
    "value": 96.4,
    "threshold": 80.0,
    "started_at": "2026-07-26T10:00:00+08:00",
    "payload": {
        "summary": "CPU saturation after release while database queries exceed two seconds",
        "release": "checkout-2026.07.26.1",
        "scenario": SCENARIO_VERSION,
    },
}
WORKFLOW_KEY = f"demo:{SCENARIO_VERSION}:workflow"
DECISION_KEY = f"demo:{SCENARIO_VERSION}:approve"
ACTIVE_STATUSES = {"queued", "running", "waiting_for_approval", "reanalyzing"}
READY_STATUSES = {"waiting_for_approval", "completed", "rejected"}


class DemoBootstrapError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DemoBootstrapResult:
    alert_id: str
    workflow_run_id: str
    workflow_status: str
    alert_replayed: bool
    workflow_dispatched: bool
    failed_tool_observed: str | None
    case_status: str | None
    case_id: str | None
    web_url: str

    def as_dict(self) -> dict[str, object]:
        return {
            "scenario": SCENARIO_VERSION,
            "alert": {
                "id": self.alert_id,
                "replayed": self.alert_replayed,
                "url": self.web_url,
            },
            "workflow": {
                "run_id": self.workflow_run_id,
                "status": self.workflow_status,
                "dispatched": self.workflow_dispatched,
                "failed_tool_observed": self.failed_tool_observed,
            },
            "case": {
                "id": self.case_id,
                "status": self.case_status,
            },
        }


class DemoBootstrapper:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        web_base_url: str,
        timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> None:
        self.client = client
        self.web_base_url = web_base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    async def run(
        self,
        *,
        approve: bool,
        expected_failed_tool: str | None,
    ) -> DemoBootstrapResult:
        await self._wait_until_ready()
        alert_response = await self._request("POST", "/api/v1/alerts", json=ALERT_PAYLOAD)
        alert = alert_response.json()
        alert_id = str(alert["id"])

        workflow_response = await self._request(
            "POST",
            f"/api/v1/alerts/{alert_id}/workflow",
            json={"idempotency_key": WORKFLOW_KEY},
        )
        workflow = workflow_response.json()
        workflow_run_id = str(workflow["workflow_run_id"])
        workflow_status = await self._wait_for_workflow(alert_id, READY_STATUSES)

        failed_tool_observed = await self._verify_failed_tool(alert_id, expected_failed_tool)

        if approve and workflow_status == "waiting_for_approval":
            await self._request(
                "POST",
                f"/api/v1/alerts/{alert_id}/decisions",
                json={
                    "idempotency_key": DECISION_KEY,
                    "action": "approve",
                    "actor": "demo-user",
                    "comment": "Approved by the deterministic V2.7A demo bootstrap.",
                },
            )
            workflow_status = await self._wait_for_workflow(alert_id, {"completed", "rejected"})

        case_status: str | None = None
        case_id: str | None = None
        if workflow_status == "completed":
            case = await self._wait_for_case(alert_id)
            case_status = str(case["knowledge_sync_status"])
            case_id = str(case["id"])

        return DemoBootstrapResult(
            alert_id=alert_id,
            workflow_run_id=workflow_run_id,
            workflow_status=workflow_status,
            alert_replayed=alert_response.status_code == httpx.codes.OK,
            workflow_dispatched=bool(workflow["dispatched"]),
            failed_tool_observed=failed_tool_observed,
            case_status=case_status,
            case_id=case_id,
            web_url=f"{self.web_base_url}/alerts/{alert_id}",
        )

    async def _wait_until_ready(self) -> None:
        deadline = monotonic() + self.timeout_seconds
        last_error = "API has not responded"
        while monotonic() < deadline:
            try:
                response = await self.client.get("/api/v1/health/ready")
                if response.status_code == httpx.codes.OK:
                    return
                last_error = f"readiness returned HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                last_error = type(exc).__name__
            await asyncio.sleep(self.poll_interval_seconds)
        raise DemoBootstrapError(f"API readiness timed out: {last_error}")

    async def _wait_for_workflow(self, alert_id: str, targets: set[str]) -> str:
        deadline = monotonic() + self.timeout_seconds
        last_status = "unknown"
        while monotonic() < deadline:
            response = await self._request("GET", f"/api/v1/alerts/{alert_id}/workflow")
            last_status = str(response.json()["run"]["status"])
            if last_status == "failed":
                raise DemoBootstrapError("demo workflow entered failed state")
            if last_status in targets:
                return last_status
            if last_status not in ACTIVE_STATUSES:
                raise DemoBootstrapError(f"unexpected workflow status: {last_status}")
            await asyncio.sleep(self.poll_interval_seconds)
        raise DemoBootstrapError(f"workflow timed out in status {last_status}")

    async def _verify_failed_tool(
        self, alert_id: str, expected_failed_tool: str | None
    ) -> str | None:
        response = await self._request(
            "GET", f"/api/v1/alerts/{alert_id}/events", params={"limit": 200}
        )
        failed_tools = {
            str(item["payload"].get("tool_name"))
            for item in response.json()["items"]
            if item["event_type"] == "tool_failed"
        }
        if expected_failed_tool is None:
            return None
        if expected_failed_tool not in failed_tools:
            raise DemoBootstrapError(
                f"expected failed tool {expected_failed_tool!r} was not observed"
            )
        return expected_failed_tool

    async def _wait_for_case(self, alert_id: str) -> dict[str, Any]:
        deadline = monotonic() + self.timeout_seconds
        last_status = "missing"
        while monotonic() < deadline:
            response = await self.client.get(f"/api/v1/alerts/{alert_id}/case")
            if response.status_code == httpx.codes.NOT_FOUND:
                await asyncio.sleep(self.poll_interval_seconds)
                continue
            self._raise_for_status(response)
            case = response.json()
            last_status = str(case["knowledge_sync_status"])
            if last_status == "synced":
                return case
            if last_status == "failed":
                raise DemoBootstrapError("demo case knowledge sync failed")
            await asyncio.sleep(self.poll_interval_seconds)
        raise DemoBootstrapError(f"case sync timed out in status {last_status}")

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await self.client.request(method, path, **kwargs)
        self._raise_for_status(response)
        return response

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            error = response.json().get("error", {})
            detail = error.get("code") or error.get("message")
        except (ValueError, AttributeError):
            detail = None
        suffix = f": {detail}" if detail else ""
        raise DemoBootstrapError(
            f"{response.request.method} {response.request.url.path} returned "
            f"HTTP {response.status_code}{suffix}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare the deterministic Alert Sage V2.7A demo scenario"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--web-base-url", default="http://localhost:15173")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=1.0)
    parser.add_argument("--expect-failed-tool")
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Approve the report and wait for case synchronization instead of pausing.",
    )
    return parser


async def run(args: argparse.Namespace) -> DemoBootstrapResult:
    timeout = httpx.Timeout(10.0)
    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=timeout) as client:
        return await DemoBootstrapper(
            client,
            web_base_url=args.web_base_url,
            timeout_seconds=args.timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        ).run(
            approve=args.approve,
            expected_failed_tool=args.expect_failed_tool,
        )


def main() -> None:
    args = build_parser().parse_args()
    if args.timeout_seconds <= 0 or args.poll_interval_seconds < 0:
        raise SystemExit("timeouts must be positive and poll interval cannot be negative")
    if sys.platform == "win32":
        result = asyncio.run(
            run(args),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        result = asyncio.run(run(args))
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

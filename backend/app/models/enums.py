from enum import StrEnum


class AlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    RECEIVED = "received"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    REANALYZING = "reanalyzing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"

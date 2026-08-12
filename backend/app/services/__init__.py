from typing import Any

__all__ = ["AlertService"]


def __getattr__(name: str) -> Any:
    if name == "AlertService":
        from app.services.alerts import AlertService

        return AlertService
    raise AttributeError(name)

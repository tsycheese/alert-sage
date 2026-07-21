import os
import sys
from pathlib import Path


def prepare_multiprocess_directory() -> None:
    raw_path = os.getenv("PROMETHEUS_MULTIPROC_DIR")
    if not raw_path:
        return
    metrics_path = Path(raw_path).resolve()
    if not metrics_path.is_absolute() or metrics_path == Path(metrics_path.anchor):
        raise RuntimeError("PROMETHEUS_MULTIPROC_DIR must be a safe absolute directory")
    metrics_path.mkdir(parents=True, exist_ok=True)
    for metric_file in metrics_path.glob("*.db"):
        if metric_file.is_file():
            metric_file.unlink()


def main() -> None:
    prepare_multiprocess_directory()
    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "app.tasks.celery_app:celery_app",
            "worker",
            "--loglevel=INFO",
            "--concurrency=2",
        ],
    )


if __name__ == "__main__":
    main()

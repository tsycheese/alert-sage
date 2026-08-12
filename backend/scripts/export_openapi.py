import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

os.environ.update(
    {
        "ALERT_SAGE_RUNTIME_PROFILE": "test",
        "ALERT_SAGE_COMPONENT_ROLE": "test",
        "ALERT_SAGE_KNOWLEDGE_PROVIDER": "mock",
        "ALERT_SAGE_DIAGNOSTIC_MODEL_PROVIDER": "mock",
        "ALERT_SAGE_FEISHU_ENABLED": "false",
    }
)
for secret_name in (
    "ALERT_SAGE_DIFY_API_KEY",
    "ALERT_SAGE_DIAGNOSTIC_MODEL_API_KEY",
    "ALERT_SAGE_FEISHU_APP_SECRET",
    "ALERT_SAGE_FEISHU_VERIFICATION_TOKEN",
    "ALERT_SAGE_FEISHU_ENCRYPT_KEY",
):
    os.environ.pop(secret_name, None)

from app.main import app  # noqa: E402 - path/profile must be fixed before app import

OUTPUT_PATH = REPOSITORY_ROOT / "openapi" / "openapi.json"


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="\n") as output_file:
        output_file.write(
            json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
    print(f"Exported OpenAPI schema to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

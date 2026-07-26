import asyncio
import json
from pathlib import Path

from app.core.config import Settings
from app.evaluation.loader import load_evaluation_set
from app.integrations.knowledge.cases import CaseDocument
from app.integrations.knowledge.factory import create_evaluation_case_publisher

DEFAULT_EVALUATION_SET = Path("evaluation_sets/rag-v2.6-baseline.json")
REPOSITORY_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


async def publish_corpus() -> dict[str, object]:
    settings = Settings(_env_file=REPOSITORY_ENV_FILE)
    if settings.knowledge_provider != "dify":
        raise RuntimeError("evaluation corpus publishing requires the Dify provider")
    if not settings.dify_evaluation_dataset_id:
        raise RuntimeError("ALERT_SAGE_DIFY_EVALUATION_DATASET_ID is required")

    loaded = load_evaluation_set(DEFAULT_EVALUATION_SET)
    publisher = create_evaluation_case_publisher(settings)
    document_ids: list[str] = []
    for item in loaded.evaluation_set.documents:
        external_id = await publisher.publish(
            CaseDocument(
                case_id=item.case_id,
                title=item.title,
                symptom=item.symptom,
                root_cause=item.root_cause,
                resolution=item.resolution,
                evidence=[
                    {
                        "type": "evaluation-set",
                        "key": item.key,
                        "evaluation_set_id": loaded.evaluation_set.id,
                        "evaluation_set_version": loaded.evaluation_set.version,
                    }
                ],
                tags=item.tags,
            ),
            idempotency_key=(
                f"rag-evaluation-corpus:{loaded.evaluation_set.id}:"
                f"{loaded.evaluation_set.version}:{item.case_id}"
            ),
        )
        document_ids.append(external_id)
    return {
        "evaluation_set_id": loaded.evaluation_set.id,
        "evaluation_set_version": loaded.evaluation_set.version,
        "evaluation_set_sha256": loaded.sha256,
        "published_document_count": len(document_ids),
        "document_ids": document_ids,
    }


def main() -> None:
    print(json.dumps(asyncio.run(publish_corpus()), ensure_ascii=False))


if __name__ == "__main__":
    main()

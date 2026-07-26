from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.evaluation.schemas import RagEvaluationSet

MAX_EVALUATION_SET_BYTES = 2_000_000


class EvaluationSetLoadError(ValueError):
    """Raised when a versioned evaluation set cannot be safely loaded."""


@dataclass(frozen=True, slots=True)
class LoadedEvaluationSet:
    evaluation_set: RagEvaluationSet
    sha256: str
    path: Path


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationSetLoadError(f"evaluation set contains duplicate JSON key: {key}")
        result[key] = value
    return result


def load_evaluation_set(path: str | Path) -> LoadedEvaluationSet:
    resolved = Path(path).resolve()
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise EvaluationSetLoadError("evaluation set cannot be read") from exc
    if len(raw) > MAX_EVALUATION_SET_BYTES:
        raise EvaluationSetLoadError("evaluation set exceeds the size limit")

    try:
        decoded = raw.decode("utf-8")
        payload = json.loads(decoded, object_pairs_hook=_reject_duplicate_keys)
        evaluation_set = RagEvaluationSet.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise EvaluationSetLoadError("evaluation set is not valid strict UTF-8 JSON") from exc

    return LoadedEvaluationSet(
        evaluation_set=evaluation_set,
        sha256=hashlib.sha256(raw).hexdigest(),
        path=resolved,
    )

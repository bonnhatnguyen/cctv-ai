"""Pilot metrics for evidence quality; not person-level surveillance scoring."""

from dataclasses import dataclass
from typing import Sequence

from app.schemas import CaseOutcome, TransactionStatus


@dataclass(frozen=True)
class EvaluationReport:
    event_precision: float
    event_recall: float
    review_precision: float
    false_positive_rate: float
    insufficient_observation_rate: float


def evaluate_cases(predictions: Sequence[CaseOutcome], labels: Sequence[CaseOutcome]) -> EvaluationReport:
    expected = {item.case_id: item.status for item in labels}
    predicted = {item.case_id: item.status for item in predictions}
    matched = sum(predicted.get(case_id) == status for case_id, status in expected.items())
    total = len(expected)
    review_predictions = [case_id for case_id, status in predicted.items() if status is TransactionStatus.REVIEW_REQUIRED]
    review_true = sum(expected.get(case_id) is TransactionStatus.REVIEW_REQUIRED for case_id in review_predictions)
    insufficient = sum(status is TransactionStatus.INSUFFICIENT_OBSERVATION for status in predicted.values())
    return EvaluationReport(event_precision=matched / len(predicted) if predicted else 0.0,
        event_recall=matched / total if total else 0.0,
        review_precision=review_true / len(review_predictions) if review_predictions else 0.0,
        false_positive_rate=sum(expected.get(case_id) is not TransactionStatus.REVIEW_REQUIRED for case_id in review_predictions) / len(review_predictions) if review_predictions else 0.0,
        insufficient_observation_rate=insufficient / len(predicted) if predicted else 0.0)

from app.evaluation import evaluate_cases
from app.schemas import CaseOutcome, TransactionStatus


def test_evaluation_reports_review_precision_and_unknown_rate():
    predictions = [CaseOutcome(case_id="1", status=TransactionStatus.REVIEW_REQUIRED), CaseOutcome(case_id="2", status=TransactionStatus.INSUFFICIENT_OBSERVATION)]
    labels = [CaseOutcome(case_id="1", status=TransactionStatus.REVIEW_REQUIRED), CaseOutcome(case_id="2", status=TransactionStatus.CLOSED)]
    report = evaluate_cases(predictions, labels)
    assert report.review_precision == 1.0
    assert report.insufficient_observation_rate == 0.5

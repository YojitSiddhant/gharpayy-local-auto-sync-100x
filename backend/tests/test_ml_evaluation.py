from backend.app.ml.evaluation.metrics import (
    character_error_rate, exact_match_accuracy, invalid_number_acceptance_rate,
    precision_recall, review_rate, row_detection_metrics, word_error_rate,
    wrong_assignment_rate,
)


def test_phone_metrics_do_not_award_missing_predictions():
    assert exact_match_accuracy(["+919876543210", None], ["+919876543210", "+919123456789"]) == 0.5
    assert precision_recall(["+919876543210", None], ["+919876543210", "+919123456789"]) == {"precision": 1.0, "recall": 0.5}


def test_invalid_numbers_and_false_positives_are_counted():
    assert invalid_number_acceptance_rate(["+919876543210", "+91BAD", None]) == 0.5
    assert precision_recall(["wrong", None], ["right", None])["precision"] == 0.0


def test_text_and_row_metrics():
    assert character_error_rate(["hello", ""], ["hello", "world"]) == 0.5
    assert word_error_rate(["hello there"], ["hello world"]) == 0.5
    assert row_detection_metrics([1, 2], [2, 3]) == {"precision": 0.5, "recall": 0.5}
    assert wrong_assignment_rate(["row-2", None], ["row-1", "row-3"]) == 0.5


def test_review_and_zero_denominators_are_deterministic():
    assert review_rate(["review", "done", "abstain"]) == 2 / 3
    assert review_rate([]) == 0.0
    assert exact_match_accuracy([], []) == 0.0


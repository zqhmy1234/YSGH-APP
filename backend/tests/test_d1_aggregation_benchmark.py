"""D1 workbook adapter and member scoring regressions."""

import json
import sys
from io import BytesIO

import pytest
from openpyxl import Workbook

from research.aggregation_benchmark.d1_input import (
    D1_HEADERS,
    D1_SHEET,
    InputError,
    load_predictions,
    load_reference,
)
from research.aggregation_benchmark.run_eval import main, score_groups


def _workbook(*rows):
    book = Workbook()
    sheet = book.active
    sheet.title = D1_SHEET
    for column, header in enumerate(D1_HEADERS, 1):
        sheet.cell(3, column, f"字段\n{header}")
    sheet.cell(5, 1, "example.jpg")
    sheet.cell(5, 4, "example-group")
    for row_number, row in enumerate(rows, 12):
        for column, value in enumerate(row, 1):
            sheet.cell(row_number, column, value)
    d2 = book.create_sheet("D2批_主题事件标注")
    d2.cell(12, 1, "long-window.jpg")
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


def test_import_excludes_example_rows_and_d2_and_retains_ids():
    reference = load_reference(
        _workbook(
            ("test-1.jpg", None, None, "01", "first wording"),
            ("test-2", None, None, "01", "second wording"),
            ("test-3", None, None, 2, "another group"),
        )
    )
    assert reference.groups == {"01": {"test-1.jpg", "test-2"}, "2": {"test-3"}}
    assert reference.photo_rows == {"test-1.jpg": 12, "test-2": 13, "test-3": 14}
    assert reference.event_ids_with_multiple_names == ["01"]


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ((), "no D1 annotation rows"),
        ((("test-1", None, None, None),), "row 12: photo_id and final_event_id"),
        (
            (("test-1", None, None, "a"), ("test-1 ", None, None, "b")),
            "row 13: repeated photo_id from row 12",
        ),
    ],
)
def test_import_rejects_incomplete_or_duplicate_memberships(rows, reason):
    with pytest.raises(InputError, match=reason):
        load_reference(_workbook(*rows))


def test_group_matching_optimizes_globally_and_reports_split_merge():
    expected = {"a": {"1", "2", "3", "4", "5", "6", "7"}, "b": {"8", "9", "10"}, "c": {"11"}}
    predictions = {"x": {"1", "2", "3", "4", "8", "9", "10"}, "y": {"5", "6", "7"}, "z": {"extra"}}
    result = score_groups(expected, predictions)
    assert result["micro"] == {"tp": 6, "fp": 5, "fn": 5, "precision": 6 / 11, "recall": 6 / 11, "f1": 6 / 11}
    assert [row["matched_predicted_id"] for row in result["by_expected_group"]] == ["y", "x", None]
    assert result["split_groups"] == [{"expected_id": "a", "predicted_ids": ["x", "y"]}]
    assert result["merged_groups"] == [{"predicted_id": "x", "expected_ids": ["a", "b"]}]
    assert result["unknown_photo_ids"] == ["extra"]
    assert result["unassigned_photo_ids"] == ["11"]
    assert result["unmatched_predicted_groups"] == [{"id": "z", "photo_ids": ["extra"]}]


def test_group_ids_do_not_affect_match_and_empty_output_scores_as_missed():
    expected = {"1": {"a", "b"}, "2": {"c"}}
    assert score_groups(expected, {"x": {"c"}, "y": {"a", "b"}})["micro"]["f1"] == 1
    empty_score = score_groups(expected, {})["micro"]
    assert empty_score == {"tp": 0, "fp": 0, "fn": 3, "precision": None, "recall": 0, "f1": 0}


def test_prediction_duplicate_ids_fail_instead_of_inflating_metrics():
    payload = {
        "run_id": "duplicate",
        "kind": "synthetic",
        "groups": [{"id": "one", "photo_ids": ["a"]}, {"id": "two", "photo_ids": ["a"]}],
    }
    with pytest.raises(InputError, match="group 2: missing or repeated photo_id"):
        load_predictions(json.dumps(payload).encode())


def test_cli_import_and_score_write_distinct_report_states(tmp_path, monkeypatch):
    workbook = tmp_path / "source.xlsx"
    workbook.write_bytes(_workbook(("photo-a", None, "private place", "g1", "private title", "private note")))
    report = tmp_path / "report.json"
    args = ["run_eval", "--workbook", str(workbook), "--output", str(report)]
    monkeypatch.setattr(sys, "argv", args)
    main()
    first = json.loads(report.read_text(encoding="utf-8"))
    assert first["state"] == "imported"
    assert first["predictions"] is None
    assert first["scores"] is None

    predictions = tmp_path / "predictions.json"
    exported = {"run_id": "fixture", "kind": "synthetic", "groups": [{"id": "a", "photo_ids": ["photo-a"]}]}
    predictions.write_text(json.dumps(exported), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [*args, "--predictions", str(predictions)])
    main()
    text = report.read_text(encoding="utf-8")
    second = json.loads(text)
    assert second["state"] == "scored"
    assert second["predictions"]["kind"] == "synthetic"
    assert second["source"] == first["source"]
    assert second["scores"]["micro"]["f1"] == 1
    assert "private" not in text

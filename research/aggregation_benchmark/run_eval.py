"""Import D1 annotations and score an offline event-group export."""

from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import d1_input
from .d1_input import D1_SHEET, FIRST_DATA_ROW, Groups, InputError, load_predictions, load_reference


def _metrics(tp: int, fp: int, fn: int) -> dict:
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "f1": 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None,
    }


def score_groups(expected: Groups, predicted: Groups) -> dict:
    """Match groups one-to-one by the maximum number of shared photo IDs."""
    from scipy.optimize import linear_sum_assignment

    expected_ids = sorted(expected)
    predicted_ids = sorted(predicted)
    overlaps = [[len(expected[e] & predicted[p]) for p in predicted_ids] for e in expected_ids]
    pairs = {}
    if expected_ids and predicted_ids:
        rows, cols = linear_sum_assignment(overlaps, maximize=True)
        pairs = {expected_ids[r]: predicted_ids[c] for r, c in zip(rows, cols, strict=True) if overlaps[r][c]}

    matched_predictions = set(pairs.values())
    expected_photos = set().union(*expected.values())
    predicted_photos = set().union(*predicted.values())
    by_expected = []
    total_matches = 0
    split_groups = []
    for row_index, event_id in enumerate(expected_ids):
        truth = expected[event_id]
        match = pairs.get(event_id)
        observed = predicted[match] if match is not None else set()
        shared = len(truth & observed)
        total_matches += shared
        overlapping = [predicted_ids[i] for i, count in enumerate(overlaps[row_index]) if count]
        if len(overlapping) > 1:
            split_groups.append({"expected_id": event_id, "predicted_ids": overlapping})
        by_expected.append(
            {
                "expected_id": event_id,
                "matched_predicted_id": match,
                "missing_photo_ids": sorted(truth - observed),
                "extra_photo_ids": sorted(observed - truth),
                **_metrics(shared, len(observed) - shared, len(truth) - shared),
            }
        )

    merged_groups = []
    for col_index, prediction_id in enumerate(predicted_ids):
        overlapping = [expected_ids[i] for i, counts in enumerate(overlaps) if counts[col_index]]
        if len(overlapping) > 1:
            merged_groups.append({"predicted_id": prediction_id, "expected_ids": overlapping})

    return {
        "micro": _metrics(total_matches, len(predicted_photos) - total_matches, len(expected_photos) - total_matches),
        "by_expected_group": by_expected,
        "split_groups": split_groups,
        "merged_groups": merged_groups,
        "unmatched_predicted_groups": [
            {"id": group_id, "photo_ids": sorted(predicted[group_id])}
            for group_id in predicted_ids
            if group_id not in matched_predictions
        ],
        "unassigned_photo_ids": sorted(expected_photos - predicted_photos),
        "unknown_photo_ids": sorted(predicted_photos - expected_photos),
    }


def _write_report(output: Path, report: dict) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    temporary = None
    try:
        with NamedTemporaryFile(dir=output.parent, mode="w", encoding="utf-8", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(serialized)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True, help="D1 source XLSX")
    parser.add_argument("--predictions", type=Path, help="offline aggregation output JSON")
    parser.add_argument("--first-data-row", type=int, default=FIRST_DATA_ROW, help="first annotated row, default 12")
    parser.add_argument("--output", type=Path, required=True, help="destination JSON report")
    args = parser.parse_args()
    inputs = [path.resolve() for path in (args.workbook, args.predictions) if path is not None]
    if args.output.resolve() in inputs:
        parser.error("--output must differ from input paths")

    try:
        raw_workbook = args.workbook.read_bytes()
        reference = load_reference(raw_workbook, args.first_data_row)
        report = {
            "schema_version": 1,
            "benchmark": "d1_workbook_event_groups",
            "reference_status": "consensus_and_materials_unverified",
            "state": "imported",
            "source": {
                "sheet": D1_SHEET,
                "workbook_sha256": sha256(raw_workbook).hexdigest(),
                "first_data_row": args.first_data_row,
            },
            "implementation_sha256": {
                name: sha256(Path(path).read_bytes()).hexdigest()
                for name, path in (("input", d1_input.__file__), ("scoring", __file__))
            },
            "reference": {
                "photo_count": len(reference.photo_rows),
                "group_count": len(reference.groups),
                "groups": {group_id: sorted(photos) for group_id, photos in sorted(reference.groups.items())},
                "photo_rows": reference.photo_rows,
                "event_ids_with_multiple_names": reference.event_ids_with_multiple_names,
            },
            "predictions": None,
            "scores": None,
        }
        if args.predictions is not None:
            raw_predictions = args.predictions.read_bytes()
            predictions = load_predictions(raw_predictions)
            report["state"] = "scored"
            report["predictions"] = {
                "kind": predictions.kind,
                "run_id": predictions.run_id,
                "sha256": sha256(raw_predictions).hexdigest(),
                "group_count": len(predictions.groups),
            }
            report["scores"] = score_groups(reference.groups, predictions.groups)
        _write_report(args.output, report)
    except (InputError, OSError) as exc:
        parser.error(str(exc))

    print(f"D1 {report['state']}: {len(reference.photo_rows)} photos, {len(reference.groups)} reference event groups")


if __name__ == "__main__":
    main()

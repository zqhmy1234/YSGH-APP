"""Read D1 workbook memberships and offline aggregation output."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

D1_SHEET = "D1批_连续照片事件分组"
D1_HEADERS = ("photo_id", "timestamp", "location", "final_event_id", "event_label", "note")
FIRST_DATA_ROW = 12
Groups = dict[str, set[str]]


class InputError(ValueError):
    """Input validation error that can be displayed without workbook contents."""


@dataclass
class Reference:
    groups: Groups
    photo_rows: dict[str, int]
    event_ids_with_multiple_names: list[str]


@dataclass
class Predictions:
    run_id: str
    kind: str
    groups: Groups


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def load_reference(workbook: bytes, first_data_row: int = FIRST_DATA_ROW) -> Reference:
    if first_data_row < 5:
        raise InputError("--first-data-row must be at least 5")
    try:
        book = load_workbook(BytesIO(workbook), read_only=True, data_only=True, keep_links=False)
    except (BadZipFile, InvalidFileException, KeyError, ValueError) as exc:
        raise InputError("cannot read the given file as an XLSX workbook") from exc

    groups: Groups = defaultdict(set)
    photo_rows: dict[str, int] = {}
    event_names: dict[str, set[str]] = defaultdict(set)
    try:
        if D1_SHEET not in book.sheetnames:
            raise InputError(f"missing worksheet: {D1_SHEET}")
        sheet = book[D1_SHEET]
        headers = next(sheet.iter_rows(min_row=3, max_row=3, max_col=len(D1_HEADERS), values_only=True))
        if any(field not in _text(value).splitlines() for field, value in zip(D1_HEADERS, headers, strict=True)):
            raise InputError("D1 headers in A3:F3 do not match the expected workbook layout")

        rows = sheet.iter_rows(min_row=first_data_row, max_col=len(D1_HEADERS), values_only=True)
        for row_number, row in enumerate(rows, first_data_row):
            if all(value is None or value == "" for value in row):
                continue
            photo_id, _, _, event_id, event_name, _ = row
            photo_id = _text(photo_id)
            event_id = str(event_id) if type(event_id) is int else _text(event_id)
            if not photo_id or not event_id:
                raise InputError(f"D1 row {row_number}: photo_id and final_event_id are required")
            if photo_id in photo_rows:
                raise InputError(f"D1 row {row_number}: repeated photo_id from row {photo_rows[photo_id]}")
            photo_rows[photo_id] = row_number
            groups[event_id].add(photo_id)
            if name := _text(event_name):
                event_names[event_id].add(name)
    finally:
        book.close()

    if not photo_rows:
        raise InputError("no D1 annotation rows found in the selected data range")
    conflicts = sorted(event_id for event_id, names in event_names.items() if len(names) > 1)
    return Reference(dict(groups), photo_rows, conflicts)


def load_predictions(raw: bytes) -> Predictions:
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise InputError("predictions must be a valid JSON file") from exc
    if not isinstance(payload, dict) or not (run_id := _text(payload.get("run_id"))):
        raise InputError("predictions must specify a non-empty run_id")
    kind = _text(payload.get("kind"))
    if kind not in ("algorithm_output", "synthetic"):
        raise InputError("prediction kind must be algorithm_output or synthetic")
    raw_groups = payload.get("groups")
    if not isinstance(raw_groups, list):
        raise InputError("prediction groups must be a list")

    groups: Groups = {}
    seen_photos: set[str] = set()
    for position, group in enumerate(raw_groups, 1):
        if not isinstance(group, dict) or not (group_id := _text(group.get("id"))) or group_id in groups:
            raise InputError(f"prediction group {position}: missing or repeated id")
        members = group.get("photo_ids")
        if not isinstance(members, list) or not members:
            raise InputError(f"prediction group {position}: photo_ids must be a non-empty list")
        photos = set()
        for member in members:
            photo_id = _text(member)
            if not photo_id or photo_id in seen_photos:
                raise InputError(f"prediction group {position}: missing or repeated photo_id")
            seen_photos.add(photo_id)
            photos.add(photo_id)
        groups[group_id] = photos
    return Predictions(run_id, kind, groups)

from __future__ import annotations

from datetime import date

import pytest
from ctr_loader.ctr_loader_v2 import _parse_member_sheet


class FakeWorksheet:
    def __init__(self, rows):
        self.rows = rows
        self.nrows = len(rows)
        self.ncols = max(map(len, rows), default=0)

    def cell_value(self, row, column):
        return self.rows[row][column] if column < len(self.rows[row]) else None


@pytest.mark.unit
def test_parse_member_sheet_normalizes_headers_and_values():
    worksheet = FakeWorksheet(
        [
            [None, None, None, None, None],
            ["Member No", "Date of Birth", "Gender", "Annual Salary", "Category Flag"],
            ["M001", "12/03/1985", "female", "75,000", "2"],
            [None, None, None, None, None],
        ]
    )

    result = _parse_member_sheet(worksheet, datemode=0)

    assert result.height == 1
    assert result.columns == ["member_no", "date_of_birth", "gender", "annual_salary", "category_flag"]
    assert result["member_no"].to_list() == ["M001"]
    assert result["date_of_birth"].to_list() == [date(1985, 3, 12)]
    assert result["gender"].to_list() == ["F"]
    assert result["annual_salary"].to_list() == [75000.0]
    assert result["category_flag"].to_list() == [2.0]


@pytest.mark.unit
def test_parse_member_sheet_returns_empty_for_incomplete_sheet():
    worksheet = FakeWorksheet([[None], ["Member No"]])

    result = _parse_member_sheet(worksheet, datemode=0)

    assert result.is_empty()

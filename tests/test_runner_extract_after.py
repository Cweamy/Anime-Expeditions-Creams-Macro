import pytest

from core.runner import MAX_EXTRACT_AFTER, _parse_extract_after


@pytest.mark.parametrize(
    "value, expected",
    [
        (0, 0),
        (1, 1),
        ("5", 5),
        (" 25 ", 25),
        (9999, 9999),
        (10000, MAX_EXTRACT_AFTER),
        ("999999999999999999999999999999999999999999", MAX_EXTRACT_AFTER),
        (None, 1),
        ("", 1),
        (-1, 1),
        ("-1", 1),
        (True, 1),
        ("not a number", 1),
        ("2.3434734346743343e+43", MAX_EXTRACT_AFTER),
    ],
)
def test_extract_after_accepts_decimal_whole_numbers_and_repairs_invalid_values(
        value, expected):
    assert _parse_extract_after(value) == expected

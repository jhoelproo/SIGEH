from datetime import date, datetime
import sqlite3

import pytest

from admission_visit_dates import (
    attention_in_interval,
    clinical_day_id,
    current_turn_mode,
    date_variants,
    duplicate_date_values,
    duplicate_interval,
    operational_interval,
    service_date,
    visit_moment,
)


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-07",
        "07/10/2026",
        "07-10-2026",
        date(2026, 10, 7),
        datetime(2026, 10, 7, 12),
    ],
)
def test_supported_date_formats(value):
    assert service_date(value) == date(2026, 10, 7)
    assert date_variants(value) == ("2026-10-07", "07/10/2026", "07-10-2026")


@pytest.mark.parametrize(
    "value", [None, "", "invalid", "31/02/2026", "2026-13-01", "00/10/2026"]
)
def test_invalid_dates_raise_instead_of_searching_everything(value):
    with pytest.raises(ValueError, match="fecha válida"):
        service_date(value)


@pytest.mark.parametrize("clock", ["22:27:00", "22:27", "10:27 PM", "10:27:00 PM"])
@pytest.mark.parametrize(
    "keys", [("Fecha", "Hora"), ("fecha", "hora"), ("service_date", "service_time")]
)
def test_service_moment_preserves_calendar_date_and_12_hour_clock(clock, keys):
    assert visit_moment({keys[0]: "04/10/2026", keys[1]: clock}) == datetime(
        2026, 10, 4, 22, 27
    )


@pytest.mark.parametrize("clock", [None, "", "24:00", "invalid"])
def test_invalid_clock_is_not_assumed_midnight(clock):
    with pytest.raises(ValueError, match="hora válida"):
        visit_moment({"fecha": "2026-10-04", "hora": clock})


@pytest.mark.parametrize(
    "clock,base", [("07:59:59", 5), ("08:00:00", 6), ("08:00:01", 6), ("23:59:59", 6)]
)
def test_operational_day_has_half_open_eight_am_boundary(clock, base):
    moment = datetime.fromisoformat(f"2026-10-06T{clock}")
    assert operational_interval(moment) == (
        datetime(2026, 10, base, 8),
        datetime(2026, 10, base + 1, 8),
    )


@pytest.mark.parametrize(
    "moment,inside",
    [
        ("2026-10-06T08:00:00", True),
        ("2026-10-06T19:59:59", True),
        ("2026-10-06T20:00:00", False),
        ("2026-10-06T07:59:59", False),
    ],
)
def test_nominal_range_is_used_only_when_it_contains_registration(moment, inside):
    start, end = datetime(2026, 10, 6, 8), datetime(2026, 10, 6, 20)
    parsed = datetime.fromisoformat(moment)
    expected = (start, end) if inside else operational_interval(parsed)
    assert duplicate_interval(start, end, parsed) == expected


@pytest.mark.parametrize(
    "moment,expected",
    [
        ("08:00", True),
        ("07:59", False),
        ("19:59", True),
        ("20:00", False),
        ("invalid", False),
    ],
)
def test_attention_membership_uses_service_clock_and_excludes_upper_boundary(
    moment, expected
):
    assert (
        attention_in_interval(
            {"fecha": "06/10/2026", "hora": moment},
            datetime(2026, 10, 6, 8),
            datetime(2026, 10, 6, 20),
        )
        is expected
    )


def test_duplicate_date_values_include_both_sides_of_midnight():
    assert duplicate_date_values(
        datetime(2026, 10, 6, 8), datetime(2026, 10, 7, 8)
    ) == (
        "2026-10-06",
        "06/10/2026",
        "06-10-2026",
        "2026-10-07",
        "07/10/2026",
        "07-10-2026",
    )


def test_clinical_day_is_transactional_and_idempotent_without_changing_turn():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE dias_operativos(id INTEGER PRIMARY KEY,fecha_base TEXT UNIQUE,fecha_inicio TEXT,fecha_fin TEXT)"
    )
    start, end = datetime(2026, 10, 4, 8), datetime(2026, 10, 5, 8)
    assert clinical_day_id(connection, 9, start, end, datetime(2026, 10, 4, 22)) == 9
    fresh = clinical_day_id(connection, 9, start, end, datetime(2026, 10, 6, 22))
    assert (
        clinical_day_id(connection, 9, start, end, datetime(2026, 10, 6, 23)) == fresh
    )
    assert connection.execute("SELECT COUNT(*) FROM dias_operativos").fetchone()[0] == 1
    connection.rollback()
    assert connection.execute("SELECT COUNT(*) FROM dias_operativos").fetchone()[0] == 0
    connection.close()


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("Este turno", True),
        ("Turno actual", True),
        ("Turno anterior", False),
        ("Todos", False),
        (None, False),
    ],
)
def test_only_current_shift_history_changes_sort_direction(mode, expected):
    assert current_turn_mode(mode) is expected

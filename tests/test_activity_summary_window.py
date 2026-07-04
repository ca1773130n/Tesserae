from datetime import datetime, timezone, timedelta

from tesserae.activity_summary import resolve_windows, in_window, parse_ts, Window


def test_day_window_is_24h_half_open():
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    assert w.start == datetime(2026, 7, 4, tzinfo=timezone.utc)
    assert w.end == datetime(2026, 7, 5, tzinfo=timezone.utc)
    assert w.label == "2026-07-04"


def test_week_expands_to_seven_daily_windows():
    ws = resolve_windows(week="2026-07-04", tz=timezone.utc)
    assert len(ws) == 7
    assert ws[0].start == datetime(2026, 6, 28, tzinfo=timezone.utc)
    assert ws[-1].end == datetime(2026, 7, 5, tzinfo=timezone.utc)


def test_edge_inclusion_half_open():
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    assert in_window(w.start, w) is True                    # start included
    assert in_window(w.end, w) is False                     # end excluded
    assert in_window(w.end - timedelta(seconds=1), w) is True


def test_parse_ts_handles_z_and_naive():
    assert parse_ts("2026-07-04T12:00:00Z") == datetime(2026, 7, 4, 12, tzinfo=timezone.utc)
    assert parse_ts("not-a-date") is None

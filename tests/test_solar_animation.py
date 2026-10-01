"""Tests for solar_animation.py. Run with:  pytest -q"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import solar_animation as sa  # noqa: E402


def write_csv(path, rows):
    """rows: [(timestamp_string, pv_kw), ...] -> FoxESS-style CSV."""
    pd.DataFrame(rows, columns=["time", "pvPower"]).to_csv(path, index=False)
    return path


def embedded_data(html):
    return json.loads(re.search(r"const DATA = (\{.*?\});", html, re.S).group(1))


def test_parses_bst_and_gmt_suffixes(tmp_path):
    f = write_csv(tmp_path / "a.csv", [
        ("2026-09-01 12:00:00 BST+0100", 3.0),
        ("2026-12-01 12:00:00 GMT+0000", 1.0),
    ])
    df = sa.load_pv([f])
    assert list(df["time"].dt.hour) == [12, 12]                     # wall-clock kept
    assert df["utc"].iloc[0].hour == 11 and df["utc"].iloc[1].hour == 12


def test_overlapping_files_are_deduplicated(tmp_path):
    rows = [(f"2026-09-01 10:{m:02d}:00 BST+0100", 2.0) for m in range(0, 60, 5)]
    a = write_csv(tmp_path / "partial.csv", rows[:8])
    b = write_csv(tmp_path / "full.csv", rows)
    assert len(sa.load_pv([a, b])) == len(rows)


def test_clocks_going_back_keeps_both_passes_of_the_repeated_hour(tmp_path):
    first = [(f"2026-10-25 01:{m:02d}:00 BST+0100", 1.0) for m in range(0, 60, 5)]
    second = [(f"2026-10-25 01:{m:02d}:00 GMT+0000", 2.0) for m in range(0, 60, 5)]
    df = sa.load_pv([write_csv(tmp_path / "oct.csv", first + second)])
    assert len(df) == 24
    assert set(df["pvPower"]) == {1.0, 2.0}


def test_daily_kwh_integrates_over_real_elapsed_time(tmp_path):
    # constant 6 kW, a reading every 5 minutes for exactly one hour (+ a closing reading)
    rows = [(f"2026-06-01 12:{m:02d}:00 BST+0100", 6.0) for m in range(0, 60, 5)]
    df = sa.load_pv([write_csv(tmp_path / "c.csv", rows)])
    day = sa.build_data(df)["days"][0]
    assert day["total"] == pytest.approx(6.0, abs=0.01)


def test_irregular_sampling_is_time_weighted(tmp_path):
    # 3 kW for 30 minutes, readings 10 minutes apart then a gap longer than the cap
    rows = [("2026-06-01 12:00:00 BST+0100", 3.0), ("2026-06-01 12:10:00 BST+0100", 3.0),
            ("2026-06-01 12:20:00 BST+0100", 3.0), ("2026-06-01 15:00:00 BST+0100", 0.0)]
    df = sa.load_pv([write_csv(tmp_path / "g.csv", rows)])
    # three 10-minute intervals at 3 kW = 1.5 kWh; the 2h40m gap is capped, not extrapolated
    assert sa.build_data(df)["days"][0]["total"] == pytest.approx(1.5, abs=0.01)


def test_month_labels_gain_a_year_only_when_needed(tmp_path):
    one_year = sa.build_data(sa.load_pv([write_csv(tmp_path / "x.csv", [
        ("2026-07-01 12:00:00 BST+0100", 1.0), ("2026-08-01 12:00:00 BST+0100", 1.0)])]))
    assert [d["month"] for d in one_year["days"]] == ["July", "August"]

    two_years = sa.build_data(sa.load_pv([write_csv(tmp_path / "y.csv", [
        ("2026-07-01 12:00:00 BST+0100", 1.0), ("2027-07-01 12:00:00 BST+0100", 1.0)])]))
    assert [d["month"] for d in two_years["days"]] == ["July 2026", "July 2027"]


def test_series_has_96_slots_with_none_for_missing(tmp_path):
    day = sa.build_data(sa.load_pv([write_csv(tmp_path / "s.csv", [
        ("2026-06-01 12:07:00 BST+0100", 4.0)])]))["days"][0]
    assert len(day["series"]) == 96
    assert day["series"][12 * 4 + 0] == 4.0           # 12:00-12:15 slot
    assert day["series"][0] is None


def test_missing_columns_are_reported(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("time,load\n2026-06-01 12:00:00 BST+0100,1\n")
    with pytest.raises(SystemExit):
        sa.load_pv([f])


def test_cli_builds_html_from_sample_data(tmp_path):
    out = tmp_path / "out.html"
    subprocess.run([sys.executable, str(ROOT / "solar_animation.py"),
                    str(ROOT / "examples" / "sample_synthetic.csv"),
                    "-o", str(out), "--ymax", "9", "--label", "Test array"],
                   check=True, capture_output=True)
    html = out.read_text(encoding="utf-8")
    assert "__DATA_JSON__" not in html and "__YMAX__" not in html and "__KICKER__" not in html
    assert "const maxPv = 9;" in html and "Test array" in html
    assert len(embedded_data(html)["days"]) == 9


def test_folder_input_picks_up_every_csv(tmp_path):
    write_csv(tmp_path / "jun.csv", [("2026-06-01 12:00:00 BST+0100", 1.0)])
    write_csv(tmp_path / "jul.csv", [("2026-07-01 12:00:00 BST+0100", 1.0)])
    assert len(sa.collect_files([str(tmp_path)])) == 2

"""Tests for XML parse, telemetry, layout, overlays, inspect, and roundel."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from bmw_drive_recorder.theme import load_theme
from bmw_drive_recorder.hud_panel import LOGO_SIZE, draw_roundel
from bmw_drive_recorder.inspect import render_inspect_table, summarize
from bmw_drive_recorder.layout import build_filter_graph
from bmw_drive_recorder.overlay import generate_ass
from bmw_drive_recorder.recording import Recording, TelemetryEntry, load_recording, parse_xml
from bmw_drive_recorder.telemetry import interpolated_lat_lon, interpolated_speed, interpolate_telemetry

FIXTURE = Path(__file__).parent / "fixtures" / "sample"


def _entry(i: int, v: float, lat: float = 48.0, lon: float = 11.0) -> TelemetryEntry:
    return TelemetryEntry(i, "01.01.2026", "12:00:00", v, lat, lon)


def test_parse_xml_fixture():
    vin, entries = parse_xml(FIXTURE / "sample.xml")
    assert vin == "TESTVIN00000000000"
    assert len(entries) >= 5
    assert entries[0].id == 1
    assert entries[0].frame_index == 0
    assert entries[-1].velocity_kmh > 0


def test_load_recording_fixture():
    rec = load_recording(FIXTURE)
    assert rec.name == "sample"
    assert set(rec.cameras_processed) == {"Vorn", "Links", "Rechts", "Hinten"}
    assert rec.cameras_raw == {}
    assert rec.duration_s == pytest.approx(5.0, abs=0.2)


def test_interpolate_hold_last():
    entries = [_entry(1, 10, 48.0, 11.0), _entry(16, 20, 49.0, 12.0)]
    frames = interpolate_telemetry(entries, total_frames=20, fps=30)
    assert frames[0].latitude == 48.0
    assert frames[14].latitude == 48.0
    assert frames[15].latitude == 49.0


def test_interpolated_speed_and_gps_midpoint():
    entries = [_entry(1, 0, 48.0, 11.0), _entry(31, 40, 49.0, 12.0)]
    t = entries[0].time_seconds + (entries[1].time_seconds - entries[0].time_seconds) / 2
    assert interpolated_speed(entries, t) == pytest.approx(20.0)
    lat, lon = interpolated_lat_lon(entries, t)
    assert lat == pytest.approx(48.5)
    assert lon == pytest.approx(11.5)


def test_grid2x2_filter_graph():
    rec = Recording(
        folder=Path("/tmp"),
        name="x",
        cameras_processed={
            "Vorn": Path("v.mp4"),
            "Rechts": Path("r.mp4"),
            "Links": Path("l.mp4"),
            "Hinten": Path("h.mp4"),
        },
    )
    paths, graph, w, h = build_filter_graph(rec, "grid2x2", "processed", 1920)
    assert w == 1920 and h == 1080
    assert len(paths) == 4
    assert "hstack" in graph and "vstack" in graph and "[mosaic]" in graph
    assert "hflip" not in graph


def test_hero_bottom_and_missing_camera():
    rec = Recording(
        folder=Path("/tmp"),
        name="x",
        cameras_processed={"Vorn": Path("v.mp4")},
    )
    paths, graph, w, h = build_filter_graph(rec, "hero-bottom", "processed", 1920, flip_rear=True)
    assert len(paths) == 1
    assert "drawtext=text='Left'" in graph
    assert w == 1920
    assert h == 1920 * 9 // 16 + (1920 // 3) * 9 // 16

    _, graph_rear, _, _ = build_filter_graph(
        rec, "hero-bottom", "processed", 1920, hero="rear",
    )
    assert "drawtext=text='Rear'" in graph_rear

    rec4 = Recording(
        folder=Path("/tmp"),
        name="x",
        cameras_processed={cam: Path(f"{cam}.mp4") for cam in ("Vorn", "Links", "Rechts", "Hinten")},
    )
    paths_rear, _, _, _ = build_filter_graph(
        rec4, "hero-bottom", "processed", 1920, hero="rear",
    )
    assert paths_rear[0] == rec4.cameras_processed["Hinten"]


def test_ass_camera_labels(tmp_path: Path):
    rec = Recording(folder=tmp_path, name="x", entries=[_entry(1, 0)])
    out = generate_ass(rec, tmp_path / "c.ass", 1920, 1080, layout="grid2x2")
    text = out.read_text()
    assert "Front" in text and "Rear" in text
    assert "PlayResX: 1920" in text


def test_ass_hero_labels_use_hero_split(tmp_path: Path):
    rec = Recording(folder=tmp_path, name="x", entries=[_entry(1, 0)])
    # 1920x1440 is the default hero canvas (1080 + 360)
    out = generate_ass(
        rec, tmp_path / "h.ass", 1920, 1440, layout="hero-bottom", hero="front",
    )
    text = out.read_text()
    assert "SlotHero" in text and "Front" in text
    assert "Style: SlotHero" in text and ",2,0,0,368," in text
    assert "Style: SlotBot1" in text and ",8,0,0,1088," in text


def test_inspect_hides_vin():
    rec = load_recording(FIXTURE)
    s = summarize(rec)
    assert s.vin == "TESTVIN00000000000"
    assert s.has_gps
    assert s.processed["Vorn"] and not s.raw["Vorn"]
    buf = StringIO()
    render_inspect_table([rec], show_vin=False, console=Console(file=buf, width=140))
    assert "TESTVIN" not in buf.getvalue()
    buf2 = StringIO()
    render_inspect_table(
        [rec], show_vin=True, console=Console(file=buf2, width=220, legacy_windows=False),
    )
    assert "TESTVIN00000000000" in buf2.getvalue()


def test_roundel_rgba_square():
    im = draw_roundel(LOGO_SIZE)
    assert im.mode == "RGBA"
    assert im.size == (LOGO_SIZE, LOGO_SIZE)
    extrema = im.getextrema()
    assert extrema[3][1] == 255


def test_cli_version():
    from bmw_drive_recorder.cli import parse_args

    with pytest.raises(SystemExit) as exc:
        parse_args(["--version"])
    assert exc.value.code == 0


def test_read_key_arrows():
    import os

    from bmw_drive_recorder.cli import _read_key

    r, w = os.pipe()
    os.write(w, b"\x1b[A\x1b[B\r")
    os.close(w)
    assert _read_key(r) == "up"
    assert _read_key(r) == "down"
    assert _read_key(r) == "enter"
    os.close(r)


def test_load_theme_rgb_and_defaults():
    theme = load_theme(Path(__file__).parent / "fixtures" / "theme.yaml")
    assert theme.accent == (255, 80, 0)
    assert theme.card_bg == (20, 20, 24)
    assert theme.speed_scale_kmh == 160
    assert theme.font is None
    assert theme.speed_scale_mph == 90.0

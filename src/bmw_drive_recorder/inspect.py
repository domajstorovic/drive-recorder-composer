"""Summarize a recording for the CLI inspect table."""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .recording import CAMERA_LABELS, CAMERA_NAMES, Recording

# Display order matches the 2x2 mosaic: Front, Right, Left, Rear
_CAM_DISPLAY = ("Vorn", "Rechts", "Links", "Hinten")


@dataclass
class RecordingSummary:
    name: str
    duration_s: float
    processed: dict[str, bool]
    raw: dict[str, bool]
    has_gps: bool
    speed_min_kmh: float | None
    speed_max_kmh: float | None
    telemetry_count: int
    vin: str


def summarize(recording: Recording) -> RecordingSummary:
    """Build a PII-light summary of one recording."""
    processed = {cam: cam in recording.cameras_processed for cam in CAMERA_NAMES}
    raw = {cam: cam in recording.cameras_raw for cam in CAMERA_NAMES}

    gps_pts = [
        (e.latitude, e.longitude)
        for e in recording.entries
        if e.latitude != 0 and e.longitude != 0
    ]
    speeds = [e.velocity_kmh for e in recording.entries]
    return RecordingSummary(
        name=recording.name,
        duration_s=recording.duration_s,
        processed=processed,
        raw=raw,
        has_gps=bool(gps_pts),
        speed_min_kmh=min(speeds) if speeds else None,
        speed_max_kmh=max(speeds) if speeds else None,
        telemetry_count=len(recording.entries),
        vin=recording.vin,
    )


def _cam_cell(present: dict[str, bool]) -> Text:
    parts: list[Text] = []
    for i, cam in enumerate(_CAM_DISPLAY):
        label = CAMERA_LABELS[cam]
        if i:
            parts.append(Text(" "))
        if present[cam]:
            parts.append(Text(label, style="green"))
        else:
            parts.append(Text(label, style="red"))
    return Text.assemble(*parts)


def render_inspect_table(
    recordings: list[Recording],
    *,
    show_vin: bool = False,
    console: Console | None = None,
) -> None:
    """Print a Rich table of recording summaries."""
    console = console or Console()
    table = Table(title="Drive Recorder exports", show_lines=len(recordings) > 1)
    table.add_column("Name", overflow="fold")
    table.add_column("Duration", justify="right")
    table.add_column("Processed")
    table.add_column("Raw")
    table.add_column("GPS")
    table.add_column("Speed", justify="right")
    table.add_column("Telemetry", justify="right")
    if show_vin:
        table.add_column("VIN", overflow="fold", no_wrap=True)

    for rec in recordings:
        s = summarize(rec)
        if s.speed_min_kmh is None:
            speed = "—"
        else:
            speed = f"{s.speed_min_kmh:.0f}–{s.speed_max_kmh:.0f} km/h"
        gps = Text("yes", style="green") if s.has_gps else Text("no", style="red")
        row = [
            s.name,
            f"{s.duration_s:.1f}s",
            _cam_cell(s.processed),
            _cam_cell(s.raw),
            gps,
            speed,
            str(s.telemetry_count),
        ]
        if show_vin:
            row.append(s.vin or "—")
        table.add_row(*row)

    console.print(table)

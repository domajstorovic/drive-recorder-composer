"""Generate ASS subtitle overlay for camera name badges only.

The telemetry HUD (speed, time, GPS) has moved to the rendered instrument
card (hud_panel.py). This file retains the per-camera name badges that sit
at the inner corner of each pane (near the seams).
"""

from __future__ import annotations

from pathlib import Path

from .recording import CAMERA_LABELS, HERO_BOTTOM_ORDER, HERO_NAME_TO_CAM, Recording
from .layout import hero_pane_height

_CAM_LABEL_SIZE = 22
_CAM_LABEL_GAP = 8  # gap from pane seams


def _ass_timestamp(seconds: float) -> str:
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    cs = int((seconds - int(seconds)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _style_line(
    name: str,
    fontsize: int,
    alignment: int,
    margin_l: int,
    margin_r: int,
    margin_v: int,
) -> str:
    # Matches the instrument card: dark bg (30,30,30), light text (240,240,240)
    # ASS colour format: &HAABBGGRR
    return (
        f"Style: {name},DejaVu Sans Mono,{fontsize},"
        f"&H00F0F0F0,&H000000FF,&H001E1E1E,&H001E1E1E,"
        f"-1,0,0,0,100,100,0,0,3,0,0,{alignment},"
        f"{margin_l},{margin_r},{margin_v},1"
    )


def _grid_style_lines(play_res_x: int, play_res_y: int) -> str:
    """Labels at the inner corner of each 2x2 quadrant."""
    g = _CAM_LABEL_GAP
    half_x = play_res_x // 2
    half_y = play_res_y // 2
    specs = [
        ("CamFront", 3, 0, half_x + g, half_y + g),
        ("CamRight", 1, half_x + g, 0, half_y + g),
        ("CamLeft", 9, 0, half_x + g, half_y + g),
        ("CamRear", 7, half_x + g, 0, half_y + g),
    ]
    return "\n".join(
        _style_line(name, _CAM_LABEL_SIZE, align, ml, mr, mv)
        for name, align, ml, mr, mv in specs
    )


def _hero_style_lines(play_res_x: int, play_res_y: int) -> str:
    """Labels on the hero/bottom-row seams, not a fake 2x2 split."""
    g = _CAM_LABEL_GAP
    hero_h = hero_pane_height(play_res_x)
    third = play_res_x // 3
    bottom_h = play_res_y - hero_h
    specs = [
        # Hero: bottom-center of the big pane, just above the strip
        ("SlotHero", 2, 0, 0, bottom_h + g),
        # Bottom-left third: top-right (inner)
        ("SlotBot0", 9, 0, play_res_x - third + g, hero_h + g),
        # Bottom-middle: top-center
        ("SlotBot1", 8, 0, 0, hero_h + g),
        # Bottom-right third: top-left (inner)
        ("SlotBot2", 7, 2 * third + g, 0, hero_h + g),
    ]
    return "\n".join(
        _style_line(name, _CAM_LABEL_SIZE, align, ml, mr, mv)
        for name, align, ml, mr, mv in specs
    )


def generate_ass(
    recording: Recording,
    output_path: Path,
    play_res_x: int,
    play_res_y: int,
    layout: str = "grid2x2",
    hero: str = "front",
) -> Path:
    """Generate an ASS subtitle file with camera name badges."""
    duration_s = recording.duration_s if recording.entries else 1.0
    start_all = _ass_timestamp(0)
    end_all = _ass_timestamp(duration_s)

    if layout == "hero-bottom":
        cam_styles = _hero_style_lines(play_res_x, play_res_y)
        hero_cam = HERO_NAME_TO_CAM.get(hero, "Vorn")
        bottom = [cam for cam in HERO_BOTTOM_ORDER if cam != hero_cam]
        dialogues = [
            ("SlotHero", CAMERA_LABELS[hero_cam]),
            ("SlotBot0", CAMERA_LABELS[bottom[0]]),
            ("SlotBot1", CAMERA_LABELS[bottom[1]]),
            ("SlotBot2", CAMERA_LABELS[bottom[2]]),
        ]
    else:
        cam_styles = _grid_style_lines(play_res_x, play_res_y)
        dialogues = [
            ("CamFront", CAMERA_LABELS["Vorn"]),
            ("CamRight", CAMERA_LABELS["Rechts"]),
            ("CamLeft", CAMERA_LABELS["Links"]),
            ("CamRear", CAMERA_LABELS["Hinten"]),
        ]

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{cam_styles}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines: list[str] = [header]
    for style, label in dialogues:
        lines.append(
            f"Dialogue: 2,{start_all},{end_all},{style},,0,0,0,,{label}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path

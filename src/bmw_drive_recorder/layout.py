"""Build ffmpeg filter_complex graphs for camera mosaic layouts."""

from __future__ import annotations

from pathlib import Path

from .recording import CAMERA_LABELS, HERO_BOTTOM_ORDER, HERO_NAME_TO_CAM, Recording


def _even(n: int) -> int:
    return n - (n % 2)


def hero_pane_height(canvas_width: int) -> int:
    """Height of the top pane; keep in sync with overlay label seams."""
    return _even(canvas_width * 9 // 16)


def build_filter_graph(
    recording: Recording,
    layout: str,
    source: str,
    canvas_width: int,
    flip_rear: bool = False,
    hero: str = "front",
) -> tuple[list[Path], str, int, int]:
    """Build the ffmpeg filter_complex string for the chosen layout.

    Returns:
        (input_paths, filter_complex_string, output_width, output_height)
    """
    cameras = recording.camera_paths(source)

    if layout == "hero-bottom":
        hero_cam = HERO_NAME_TO_CAM.get(hero, "Vorn")
        return _hero_bottom(cameras, canvas_width, flip_rear, hero_cam)
    return _grid2x2(cameras, canvas_width, flip_rear)


def _cam_filter(
    real_idx: int,
    tag: str,
    w: int,
    h: int,
    flip: bool = False,
) -> str:
    """Scale and pad a real camera input to target dimensions."""
    parts = [
        f"[{real_idx}:v]scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black",
    ]
    if flip:
        parts.append("hflip")
    return ",".join(parts) + f"[{tag}]"


def _black_source(tag: str, w: int, h: int, label: str) -> str:
    """Generate a black source with centered label text for a missing camera."""
    safe_label = label.replace("'", "'\\''")
    return (
        f"color=c=black:s={w}x{h}:r=30,"
        f"drawtext=text='{safe_label}':fontcolor=white:fontsize=24"
        f":x=(w-text_w)/2:y=(h-text_h)/2[{tag}]"
    )


def _grid2x2(
    cameras: dict[str, Path],
    canvas_width: int,
    flip_rear: bool,
) -> tuple[list[Path], str, int, int]:
    """2x2 grid: Front/Right on top, Left/Rear on bottom."""
    cell_w = canvas_width // 2
    cell_h = cell_w * 9 // 16
    out_w = canvas_width
    out_h = cell_h * 2

    grid_order = ["Vorn", "Rechts", "Links", "Hinten"]

    input_paths: list[Path] = []
    filter_lines: list[str] = []
    cam_refs: list[str] = []
    real_idx = 0

    for i, cam in enumerate(grid_order):
        tag = f"cam{i}"
        if cam in cameras:
            input_paths.append(cameras[cam])
            flip = flip_rear and cam == "Hinten"
            filter_lines.append(_cam_filter(real_idx, tag, cell_w, cell_h, flip=flip))
            real_idx += 1
        else:
            label = CAMERA_LABELS.get(cam, cam)
            filter_lines.append(_black_source(tag, cell_w, cell_h, label))
        cam_refs.append(f"[{tag}]")

    filter_lines.append(f"{cam_refs[0]}{cam_refs[1]}hstack=inputs=2[top]")
    filter_lines.append(f"{cam_refs[2]}{cam_refs[3]}hstack=inputs=2[bottom]")
    filter_lines.append("[top][bottom]vstack=inputs=2[mosaic]")

    filter_complex = ";\n".join(filter_lines)
    return input_paths, filter_complex, out_w, out_h


def _hero_bottom(
    cameras: dict[str, Path],
    canvas_width: int,
    flip_rear: bool,
    hero_cam: str = "Vorn",
) -> tuple[list[Path], str, int, int]:
    """Hero layout: chosen camera full-width on top, the other three in a row below."""
    hero_w = _even(canvas_width)
    hero_h = hero_pane_height(canvas_width)

    bottom_cell_w = _even(canvas_width // 3)
    bottom_cell_h = _even(bottom_cell_w * 9 // 16)

    out_w = hero_w
    out_h = hero_h + bottom_cell_h

    bottom_cams = [cam for cam in HERO_BOTTOM_ORDER if cam != hero_cam]

    input_paths: list[Path] = []
    filter_lines: list[str] = []
    real_idx = 0

    def _add_cam(cam: str, tag: str, w: int, h: int) -> None:
        nonlocal real_idx
        if cam in cameras:
            input_paths.append(cameras[cam])
            flip = flip_rear and cam == "Hinten"
            filter_lines.append(_cam_filter(real_idx, tag, w, h, flip=flip))
            real_idx += 1
        else:
            filter_lines.append(_black_source(tag, w, h, CAMERA_LABELS.get(cam, cam)))

    _add_cam(hero_cam, "hero", hero_w, hero_h)

    bottom_refs: list[str] = []
    for i, cam in enumerate(bottom_cams):
        tag = f"bot{i}"
        _add_cam(cam, tag, bottom_cell_w, bottom_cell_h)
        bottom_refs.append(f"[{tag}]")

    filter_lines.append(f"{''.join(bottom_refs)}hstack=inputs=3[bottom_raw]")
    filter_lines.append(
        f"[bottom_raw]pad={hero_w}:{bottom_cell_h}:(ow-iw)/2:0:color=black[bottom_row]"
    )
    filter_lines.append("[hero][bottom_row]vstack=inputs=2[mosaic]")

    filter_complex = ";\n".join(filter_lines)
    return input_paths, filter_complex, out_w, out_h

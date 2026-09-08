"""FFmpeg invocation for final encode."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn


def check_ffmpeg() -> str:
    """Verify ffmpeg is available and return its path."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print(
            "Error: ffmpeg not found. Install it with:\n"
            "  sudo apt install ffmpeg   (Ubuntu/Debian)\n"
            "  brew install ffmpeg        (macOS)",
            file=sys.stderr,
        )
        sys.exit(1)
    return ffmpeg


def build_full_filter(
    filter_complex: str,
    *,
    panel_input_idx: int | None,
    panel_position: str,
    panel_w: int,
    panel_h: int,
    canvas_w: int,
    canvas_h: int,
    ass_path: Path | None,
) -> tuple[str, str]:
    """Append panel overlay and ASS burn-in to the mosaic filter graph."""
    full_filter = filter_complex
    if panel_input_idx is not None:
        x_pos, y_pos = _overlay_coords(panel_position, panel_w, panel_h, canvas_w, canvas_h)
        full_filter += (
            f";\n[{panel_input_idx}:v]scale={panel_w}:{panel_h}:flags=lanczos[panel]"
            f";\n[mosaic][panel]overlay=x={x_pos}:y={y_pos}:shortest=1[composed]"
        )
        final_label = "[composed]"
    else:
        final_label = "[mosaic]"

    if ass_path and ass_path.exists():
        ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
        full_filter += f";\n{final_label}ass='{ass_escaped}'[out]"
        final_label = "[out]"
    return full_filter, final_label


def encode(
    input_paths: list[Path],
    filter_complex: str,
    output_path: Path,
    ass_path: Path | None = None,
    panel_path: Path | None = None,
    panel_position: str = "bottom-right",
    panel_w: int = 320,
    panel_h: int = 340,
    canvas_w: int = 1920,
    canvas_h: int = 1080,
    codec: str = "libx264",
    crf: int = 23,
    preset: str = "medium",
    start_s: float | None = None,
    duration_s: float | None = None,
    expected_s: float | None = None,
) -> None:
    """Run ffmpeg to produce the final composed video."""
    ffmpeg = check_ffmpeg()
    console = Console(stderr=True)

    cmd: list[str] = [ffmpeg, "-y"]

    for p in input_paths:
        if start_s is not None:
            cmd.extend(["-ss", str(start_s)])
        cmd.extend(["-i", str(p)])

    panel_input_idx: int | None = None
    if panel_path and panel_path.exists():
        panel_input_idx = len(input_paths)
        cmd.extend(["-i", str(panel_path)])

    full_filter, final_label = build_full_filter(
        filter_complex,
        panel_input_idx=panel_input_idx,
        panel_position=panel_position,
        panel_w=panel_w,
        panel_h=panel_h,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        ass_path=ass_path,
    )

    cmd.extend(["-filter_complex", full_filter])
    cmd.extend(["-map", final_label])
    cmd.extend(_encoder_args(codec, crf, preset))
    cmd.extend(["-pix_fmt", "yuv420p", "-shortest"])
    if duration_s is not None:
        cmd.extend(["-t", str(duration_s)])
    cmd.extend(["-nostats", "-stats_period", "0.05", "-progress", "pipe:1"])
    cmd.append(str(output_path))

    console.print(f"Encoding: {output_path}")
    console.print(f"  Codec: {codec}, CRF: {crf}, Preset: {preset}")
    console.print(f"  Canvas: {canvas_w}x{canvas_h}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    stderr_chunks: list[bytes] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        stderr_chunks.append(proc.stderr.read())

    reader = threading.Thread(target=_drain_stderr, daemon=True)
    reader.start()

    total = expected_s if expected_s and expected_s > 0 else None
    assert proc.stdout is not None
    columns = [
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.percentage:>6.1f}%") if total else TextColumn("{task.completed:.1f}s"),
        TimeRemainingColumn() if total else TextColumn(""),
    ]
    with Progress(*columns, console=console, transient=False, refresh_per_second=20) as progress:
        task = progress.add_task("ffmpeg", total=total if total else None)
        for raw in iter(proc.stdout.readline, b""):
            key, _, value = raw.decode("utf-8", "replace").strip().partition("=")
            # out_time_ms is microseconds (deprecated alias of out_time_us).
            if key != "out_time_us":
                if key == "progress" and value == "end" and total:
                    progress.update(task, completed=total)
                continue
            if value in ("N/A", ""):
                continue
            try:
                seconds = max(0.0, int(value) / 1_000_000.0)
            except ValueError:
                continue
            if total:
                progress.update(task, completed=min(seconds, total))
            else:
                progress.update(task, completed=seconds)

    reader.join()
    if proc.wait() != 0:
        console.print("ffmpeg error:", style="red")
        console.print("".join(c.decode("utf-8", "replace") for c in stderr_chunks), style="red")
        sys.exit(1)

    size_mb = output_path.stat().st_size / 1e6
    console.print(f"Done: {output_path} ({size_mb:.1f} MB)")


def _encoder_args(codec: str, crf: int, preset: str) -> list[str]:
    args = ["-c:v", codec]
    if "nvenc" in codec:
        args.extend(["-cq", str(crf), "-preset", preset])
    elif "videotoolbox" in codec:
        args.extend(["-q:v", str(crf)])
    else:
        args.extend(["-crf", str(crf), "-preset", preset])
    return args


def _overlay_coords(
    position: str,
    overlay_w: int,
    overlay_h: int,
    canvas_w: int,
    canvas_h: int,
    margin: int = 10,
) -> tuple[str, str]:
    """Compute overlay x/y expressions for the given position."""
    if position == "top-left":
        return str(margin), str(margin)
    elif position == "top-right":
        return str(canvas_w - overlay_w - margin), str(margin)
    elif position == "bottom-left":
        return str(margin), str(canvas_h - overlay_h - margin)
    else:
        return str(canvas_w - overlay_w - margin), str(canvas_h - overlay_h - margin)

"""BMW Drive Recorder Composer CLI."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path

from .encode import encode
from .hud_panel import render_hud_panel
from .inspect import render_inspect_table, summarize
from .layout import build_filter_graph
from .overlay import generate_ass
from .recording import Recording, find_recordings, load_recording
from .theme import load_theme

DEFAULT_LAYOUT = "grid2x2"
DEFAULT_SOURCE = "processed"
DEFAULT_HERO = "front"
DEFAULT_MAP_MODE = "follow"
DEFAULT_MAP_POSITION = "bottom-right"
DEFAULT_SPEED_UNIT = "kmh"


def _package_version() -> str:
    try:
        return pkg_version("bmw-drive-recorder")
    except PackageNotFoundError:
        return "0.1.0"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bmw-compose",
        description="Compose BMW Drive Recorder multi-camera exports into a single video.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_package_version()}",
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to a recording folder (or parent folder with --all)",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output video path (default: <folder_name>_composed.mp4)",
    )
    parser.add_argument(
        "--layout",
        choices=["grid2x2", "hero-bottom"],
        default=None,
        help="Camera layout (default: grid2x2)",
    )
    parser.add_argument(
        "--hero",
        choices=["front", "left", "right", "rear"],
        default=None,
        help="Hero-layout camera on top (default: front; ignored for grid2x2)",
    )
    parser.add_argument(
        "--source",
        choices=["processed", "raw"],
        default=None,
        help="Camera source variant (default: processed; raw is unused in practice)",
    )
    parser.add_argument(
        "--codec",
        choices=["libx264", "libx265", "h264_nvenc", "hevc_nvenc", "h264_videotoolbox"],
        default="libx264",
        help="Video codec (default: libx264; NVENC/VideoToolbox if the host supports them)",
    )
    parser.add_argument(
        "--crf",
        type=int,
        default=23,
        help="Constant Rate Factor for quality (default: 23)",
    )
    parser.add_argument(
        "--preset",
        default="medium",
        help="Encoder preset (default: medium)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1920,
        help="Output canvas width in pixels (default: 1920)",
    )
    parser.add_argument(
        "--map-position",
        choices=["top-left", "top-right", "bottom-left", "bottom-right"],
        default=None,
        help="Instrument card position (default: bottom-right)",
    )
    parser.add_argument(
        "--map-size",
        default="320x240",
        help="Map dimensions WxH within the card (default: 320x240)",
    )
    parser.add_argument(
        "--map-mode",
        choices=["follow", "overview"],
        default=None,
        help="Map camera: follow keeps the vehicle centered (default); overview shows the whole trip",
    )
    parser.add_argument(
        "--no-map",
        action="store_true",
        default=None,
        help="Hide the map portion; show only the speed/metadata strip",
    )
    parser.add_argument(
        "--speed-unit",
        choices=["kmh", "mph"],
        default=None,
        help="Speed display unit (default: kmh)",
    )
    parser.add_argument(
        "--flip-rear",
        action="store_true",
        help="Horizontally flip the rear camera",
    )
    parser.add_argument(
        "--start",
        type=float,
        default=None,
        help="Start time in seconds (trim from this point)",
    )
    parser.add_argument(
        "--end",
        type=float,
        default=None,
        help="End time in seconds (trim up to this point)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all recording subfolders",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Print a recording summary table and exit",
    )
    parser.add_argument(
        "--show-vin",
        action="store_true",
        help="Include VIN in the inspect table (hidden by default)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the ffmpeg mosaic filter graph and exit",
    )
    parser.add_argument(
        "--theme",
        type=Path,
        default=None,
        help="YAML theme for HUD colors and font",
    )

    return parser.parse_args(argv)


def _parse_map_size(size_str: str) -> tuple[int, int]:
    parts = size_str.lower().split("x")
    if len(parts) != 2:
        print(f"Error: invalid --map-size '{size_str}', expected WxH", file=sys.stderr)
        sys.exit(1)
    return int(parts[0]), int(parts[1])


def _is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _read_key(fd: int) -> str:
    ch = os.read(fd, 1)
    if ch == b"\x1b":
        rest = os.read(fd, 2)
        if rest == b"[A" or rest == b"OA":
            return "up"
        if rest == b"[B" or rest == b"OB":
            return "down"
        return "esc"
    if ch in (b"\r", b"\n"):
        return "enter"
    if ch == b"\x03":
        raise KeyboardInterrupt
    return ch.decode("utf-8", "replace")


def _pick(title: str, choices: list[str], default: str) -> str:
    """TTY arrow-key menu. Enter confirms; non-Unix falls back to typing."""
    try:
        import termios
        import tty
    except ImportError:
        from rich.prompt import Prompt
        return Prompt.ask(title, choices=choices, default=default)

    idx = choices.index(default) if default in choices else 0
    n_lines = 1 + len(choices)
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    out = sys.stderr
    first = True
    try:
        tty.setcbreak(fd)
        out.write("\033[?25l")
        while True:
            if not first:
                out.write(f"\033[{n_lines}A")
            first = False
            out.write(f"{title}\n")
            for i, choice in enumerate(choices):
                mark = ">" if i == idx else " "
                out.write(f" {mark} {choice}\n")
            out.flush()
            key = _read_key(fd)
            if key == "enter":
                out.write(f"\033[{n_lines}A\033[J")
                out.write(f"{title}: {choices[idx]}\n")
                out.flush()
                return choices[idx]
            if key == "up":
                idx = (idx - 1) % len(choices)
            elif key == "down":
                idx = (idx + 1) % len(choices)
    finally:
        out.write("\033[?25h")
        out.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _apply_defaults(args: argparse.Namespace, recordings: list[Recording]) -> None:
    """Fill omitted flags with the same defaults as a non-interactive run."""
    has_raw = any(r.cameras_raw for r in recordings)
    has_processed = any(r.cameras_processed for r in recordings)
    gps = any(summarize(r).has_gps for r in recordings)

    if args.layout is None:
        args.layout = DEFAULT_LAYOUT
    if args.hero is None and args.layout == "hero-bottom":
        args.hero = DEFAULT_HERO
    if args.source is None:
        args.source = "raw" if has_raw and not has_processed else DEFAULT_SOURCE
    if args.no_map is None:
        args.no_map = not gps
    if args.map_mode is None:
        args.map_mode = DEFAULT_MAP_MODE
    if args.map_position is None:
        args.map_position = DEFAULT_MAP_POSITION
    if args.speed_unit is None:
        args.speed_unit = DEFAULT_SPEED_UNIT


def resolve_compose_options(
    args: argparse.Namespace,
    recordings: list[Recording],
) -> list[Recording]:
    """Prompt for omitted choices on a TTY; otherwise apply defaults.

    Returns the (possibly filtered) list of recordings to compose.
    """
    if not _is_tty():
        _apply_defaults(args, recordings)
        return recordings

    if len(recordings) > 1 and not args.all:
        names = [r.name for r in recordings]
        choice = _pick("Recording", [*names, "all"], "all")
        if choice != "all":
            recordings = [r for r in recordings if r.name == choice]

    has_raw = any(r.cameras_raw for r in recordings)
    has_processed = any(r.cameras_processed for r in recordings)
    gps = any(summarize(r).has_gps for r in recordings)

    if args.layout is None:
        args.layout = _pick("Layout", ["grid2x2", "hero-bottom"], DEFAULT_LAYOUT)
    if args.layout == "hero-bottom" and args.hero is None:
        args.hero = _pick(
            "Hero camera", ["front", "left", "right", "rear"], DEFAULT_HERO,
        )
    if args.source is None:
        if has_raw and has_processed:
            args.source = _pick("Source", ["processed", "raw"], DEFAULT_SOURCE)
        elif has_raw and not has_processed:
            args.source = "raw"
        else:
            args.source = DEFAULT_SOURCE

    if args.no_map is None:
        if not gps:
            args.no_map = True
        else:
            args.no_map = _pick("Include map", ["yes", "no"], "yes") == "no"

    if args.no_map:
        if args.map_mode is None:
            args.map_mode = DEFAULT_MAP_MODE
        if args.map_position is None:
            args.map_position = DEFAULT_MAP_POSITION
    else:
        if args.map_mode is None:
            args.map_mode = _pick(
                "Map mode", ["follow", "overview"], DEFAULT_MAP_MODE,
            )
        if args.map_position is None:
            args.map_position = _pick(
                "Map position",
                ["top-left", "top-right", "bottom-left", "bottom-right"],
                DEFAULT_MAP_POSITION,
            )

    if args.speed_unit is None:
        args.speed_unit = _pick(
            "Speed unit", ["kmh", "mph"], DEFAULT_SPEED_UNIT,
        )
    return recordings


def compose_recording(recording, args: argparse.Namespace, output_path: Path) -> None:
    """Run the full composition pipeline for a single recording."""
    map_w, map_h = _parse_map_size(args.map_size)

    # Compute trim parameters
    start_s = args.start
    end_s = args.end
    duration_s = None
    if start_s is not None and end_s is not None:
        duration_s = max(0.0, end_s - start_s)
    elif end_s is not None:
        duration_s = end_s

    # Build camera mosaic filter graph
    input_paths, filter_complex, canvas_w, canvas_h = build_filter_graph(
        recording,
        layout=args.layout,
        source=args.source,
        canvas_width=args.width,
        flip_rear=args.flip_rear,
        hero=args.hero or DEFAULT_HERO,
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="bmw_compose_"))
    try:
        theme = None
        if args.theme:
            try:
                theme = load_theme(args.theme)
            except OSError as e:
                print(f"Error: could not load theme: {e}", file=sys.stderr)
                sys.exit(1)

        print("Rendering HUD...", file=sys.stderr)
        panel_path = tmp_dir / "hud_panel.mp4"
        include_map = not args.no_map
        result_path, panel_w, panel_h = render_hud_panel(
            recording,
            panel_path,
            map_w=map_w,
            map_h=map_h,
            map_mode=args.map_mode,
            speed_unit=args.speed_unit,
            include_map=include_map,
            position=args.map_position,
            start_s=start_s,
            end_s=end_s,
            theme=theme,
        )
        if result_path is None:
            print("  Warning: HUD panel rendering failed, proceeding without panel", file=sys.stderr)
        panel_path = result_path

        ass_path = tmp_dir / "cam_names.ass"
        generate_ass(
            recording,
            ass_path,
            play_res_x=canvas_w,
            play_res_y=canvas_h,
            layout=args.layout,
            hero=args.hero or DEFAULT_HERO,
        )

        encode(
            input_paths=input_paths,
            filter_complex=filter_complex,
            output_path=output_path,
            ass_path=ass_path,
            panel_path=panel_path,
            panel_position=args.map_position,
            panel_w=panel_w,
            panel_h=panel_h,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            codec=args.codec,
            crf=args.crf,
            preset=args.preset,
            start_s=start_s,
            duration_s=duration_s,
            expected_s=duration_s if duration_s is not None else recording.duration_s,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    input_path = args.input.resolve()
    if not input_path.exists():
        print(f"Error: path does not exist: {input_path}", file=sys.stderr)
        sys.exit(1)

    if args.all:
        recordings = find_recordings(input_path)
    else:
        try:
            recordings = [load_recording(input_path)]
        except FileNotFoundError as e:
            recordings = find_recordings(input_path)
            if not recordings:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)

    if not recordings:
        print("No recordings found.", file=sys.stderr)
        sys.exit(1)

    render_inspect_table(recordings, show_vin=args.show_vin)

    if args.inspect:
        return

    recordings = resolve_compose_options(args, recordings)

    if args.dry_run:
        for rec in recordings:
            _paths, graph, canvas_w, canvas_h = build_filter_graph(
                rec,
                layout=args.layout,
                source=args.source,
                canvas_width=args.width,
                flip_rear=args.flip_rear,
                hero=args.hero or DEFAULT_HERO,
            )
            extra = f"  hero={args.hero}" if args.layout == "hero-bottom" else ""
            print(f"# {rec.name}  {canvas_w}x{canvas_h}  layout={args.layout}{extra}  source={args.source}")
            print(graph)
            print()
        return

    for rec in recordings:
        if args.output and len(recordings) == 1:
            output_path = args.output.resolve()
        else:
            output_path = input_path / f"{rec.name}_composed.mp4"

        compose_recording(rec, args, output_path)


if __name__ == "__main__":
    main()

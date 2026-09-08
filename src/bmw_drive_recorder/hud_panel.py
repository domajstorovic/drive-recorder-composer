"""Render the combined HUD instrument card (map + speed bar + metadata).

Produces a single video file at 30 fps that gets overlaid on the camera
mosaic. The card stacks:
  - Follow-cam (or overview) map on top
  - Speed digits + linear bar + timestamp + lat/lon in a strip below
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .map_render import (
    MAP_FPS,
    _choose_follow_zoom,
    _choose_overview_zoom,
    _crop_centered,
    _draw_marker,
    _draw_trail,
    _heading_rad,
    _lat_lon_to_pixel,
    _map_frame_points,
    _render_track_basemap,
)
from .recording import FPS, Recording
from .telemetry import (
    interpolate_telemetry,
    interpolated_speed,
    kmh_to_mph,
)
from .theme import Theme

STRIP_PADDING = 10
BAR_HEIGHT = 14
BAR_MARGIN_TOP = 4
LINE_SPACING = 4

GPS_LABEL_MARGIN = 6

LOGO_SIZE = 24
ROUNDEL_BLUE = (22, 93, 169, 255)
ROUNDEL_WHITE = (255, 255, 255, 255)
ROUNDEL_RING = (16, 16, 16, 255)

_logo_cache: Image.Image | None = None


def draw_roundel(size: int = LOGO_SIZE) -> Image.Image:
    """Draw a BMW-style roundel (ring + quadrants, no lettering) with Pillow.

    Original geometry, not an official asset. Used on the HUD strip.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, size - 1, size - 1), fill=ROUNDEL_RING)

    ring = max(3, size * 22 // 100)
    inner = (ring, ring, size - 1 - ring, size - 1 - ring)
    # pieslice 0deg = 3 o'clock, clockwise: SE white, SW blue, NW white, NE blue
    draw.pieslice(inner, 0, 90, fill=ROUNDEL_WHITE)
    draw.pieslice(inner, 90, 180, fill=ROUNDEL_BLUE)
    draw.pieslice(inner, 180, 270, fill=ROUNDEL_WHITE)
    draw.pieslice(inner, 270, 360, fill=ROUNDEL_BLUE)
    return img


def _get_logo() -> Image.Image:
    """Return the drawn HUD roundel (cached)."""
    global _logo_cache
    if _logo_cache is None:
        _logo_cache = draw_roundel(LOGO_SIZE)
    return _logo_cache


def _get_font(
    size: int,
    font_path: Path | None = None,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Try to load a monospace or sans font; fall back to default."""
    candidates = []
    if font_path is not None:
        candidates.append(str(font_path))
    candidates.extend([
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        "/System/Library/Fonts/Menlo.ttc",
    ])
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _strip_height() -> int:
    """Compute the telemetry strip height to fit speed + bar + timestamp."""
    return STRIP_PADDING + 26 + BAR_MARGIN_TOP + BAR_HEIGHT + LINE_SPACING + 16 + 2


def _draw_strip(
    card: Image.Image,
    y_start: int,
    card_w: int,
    speed_kmh: float,
    speed_unit: str,
    speed_scale: float,
    date_utc: str,
    time_utc: str,
    theme: Theme | None = None,
) -> None:
    """Draw the telemetry strip on the card below the map."""
    theme = theme or Theme()
    draw = ImageDraw.Draw(card)
    font_big = _get_font(22, theme.font)
    font_med = _get_font(14, theme.font)

    x = STRIP_PADDING
    y = y_start + STRIP_PADDING

    # Speed digits
    if speed_unit == "mph":
        speed_val = kmh_to_mph(speed_kmh)
        unit_label = "mph"
    else:
        speed_val = speed_kmh
        unit_label = "km/h"

    speed_text = f"{int(speed_val)} {unit_label}"
    draw.text((x, y), speed_text, fill=theme.text, font=font_big)

    # Roundel on the right side of the speed line
    logo = _get_logo()
    if logo:
        logo_x = card_w - STRIP_PADDING - LOGO_SIZE
        logo_y = y + (26 - LOGO_SIZE) // 2
        card.paste(logo, (logo_x, logo_y), logo)

    y += 26 + BAR_MARGIN_TOP

    # Linear bar
    bar_x = x
    bar_w = card_w - 2 * STRIP_PADDING
    draw.rounded_rectangle(
        (bar_x, y, bar_x + bar_w, y + BAR_HEIGHT),
        radius=4,
        fill=theme.bar_bg,
    )
    fill_frac = min(1.0, max(0.0, speed_val / speed_scale)) if speed_scale > 0 else 0.0
    fill_w = int(bar_w * fill_frac)
    if fill_w > 0:
        draw.rounded_rectangle(
            (bar_x, y, bar_x + fill_w, y + BAR_HEIGHT),
            radius=4,
            fill=theme.accent,
        )
    y += BAR_HEIGHT + LINE_SPACING

    # Timestamp (XML stores UTC; do not pretend to convert to local)
    draw.text((x, y), f"{date_utc} {time_utc} UTC", fill=theme.text_dim, font=font_med)


def _draw_gps_on_map(
    map_img: Image.Image,
    latitude: float,
    longitude: float,
    theme: Theme | None = None,
) -> None:
    """Draw GPS coordinates as a label at the bottom-right corner of the map."""
    theme = theme or Theme()
    font = _get_font(11, theme.font)
    gps_str = f"{latitude:.6f}, {longitude:.6f}"

    overlay = Image.new("RGBA", map_img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    bbox = draw.textbbox((0, 0), gps_str, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    text_offset_y = bbox[1]

    m = GPS_LABEL_MARGIN
    border = 2
    # Align box flush with bottom-right, inside the border
    box_right = map_img.width - border
    box_bottom = map_img.height - border
    box_left = box_right - tw - m * 2
    box_top = box_bottom - th - m * 2

    draw.rectangle(
        (box_left, box_top, box_right, box_bottom),
        fill=(*theme.card_bg, 255),
    )
    draw.text(
        (box_left + m, box_top + m - text_offset_y + 1),
        gps_str,
        fill=theme.text,
        font=font,
    )

    map_img.paste(Image.alpha_composite(
        map_img.convert("RGBA"), overlay,
    ).convert("RGB"), (0, 0))


def render_hud_panel(
    recording: Recording,
    output_path: Path,
    map_w: int = 320,
    map_h: int = 240,
    map_mode: str = "follow",
    speed_unit: str = "kmh",
    include_map: bool = True,
    position: str = "bottom-right",
    start_s: float | None = None,
    end_s: float | None = None,
    theme: Theme | None = None,
) -> tuple[Path | None, int, int]:
    """Render the combined instrument card as an mp4.

    Returns (path_or_None, card_width, card_height).
    """
    theme = theme or Theme()
    map_on_top = position.startswith("bottom")
    strip_h = _strip_height()
    card_w = map_w
    visible_map_h = int(map_h * 0.60) if include_map else 0
    card_h = visible_map_h + strip_h
    if card_h % 2 != 0:
        card_h += 1

    entries = recording.entries
    if not entries:
        return None, card_w, card_h

    speed_scale = (
        theme.speed_scale_mph if speed_unit == "mph" else theme.speed_scale_kmh
    )

    lats = [e.latitude for e in entries if e.latitude != 0]
    lons = [e.longitude for e in entries if e.longitude != 0]
    has_gps = bool(lats and lons)

    canvas = None
    origin_px = origin_py = 0.0
    map_points_px: list[tuple[float, float]] = []
    zoom = 16

    if include_map and has_gps:
        if map_mode == "overview":
            zoom = _choose_overview_zoom(lats, lons, map_w, map_h)
        else:
            zoom = _choose_follow_zoom(lats[len(lats) // 2], map_w)

        track_px = [_lat_lon_to_pixel(lat, lon, zoom) for lat, lon in zip(lats, lons)]
        canvas, origin_px, origin_py = _render_track_basemap(
            track_px, zoom, pad_x=map_w, pad_y=map_h,
        )
        map_points_px = _map_frame_points(recording, zoom)

    total_frames = entries[-1].frame_index + 1
    frame_telemetry = interpolate_telemetry(entries, total_frames, FPS)
    video_duration_s = total_frames / FPS

    trim_start = start_s if start_s is not None else 0.0
    trim_end = end_s if end_s is not None else video_duration_s
    trim_duration = trim_end - trim_start
    total_card_frames = max(1, int(round(trim_duration * MAP_FPS)))

    overview_frame = None
    ov_left = ov_top = 0
    if include_map and has_gps and map_mode == "overview" and canvas is not None:
        center_lat = (min(lats) + max(lats)) / 2
        center_lon = (min(lons) + max(lons)) / 2
        center_px, center_py = _lat_lon_to_pixel(center_lat, center_lon, zoom)
        overview_frame, ov_left, ov_top = _crop_centered(
            canvas, origin_px, origin_py, center_px, center_py, map_w, map_h,
        )

    panel_output = output_path.with_suffix(".mp4")
    panel_output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{card_w}x{card_h}",
        "-r", str(MAP_FPS),
        "-i", "pipe:0",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "ultrafast",
        "-crf", "18",
        str(panel_output),
    ]
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        return None, card_w, card_h

    stderr_chunks: list[bytes] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        stderr_chunks.append(proc.stderr.read())

    reader = threading.Thread(target=_drain_stderr, daemon=True)
    reader.start()
    assert proc.stdin is not None

    try:
        for i in range(total_card_frames):
            t = trim_start + i / MAP_FPS
            frame_idx = min(int(t * FPS), len(frame_telemetry) - 1)
            ft = frame_telemetry[frame_idx]
            speed_now = interpolated_speed(entries, t)
            card = Image.new("RGB", (card_w, card_h), theme.card_bg)

            map_frame_idx = min(int(t * MAP_FPS), len(map_points_px) - 1) if map_points_px else 0
            if include_map and has_gps and canvas is not None and map_points_px:
                if map_mode == "overview" and overview_frame is not None:
                    map_img = overview_frame.copy()
                    draw_map = ImageDraw.Draw(map_img)

                    def to_ov(px: float, py: float) -> tuple[int, int]:
                        return int(px - origin_px - ov_left), int(py - origin_py - ov_top)

                    _draw_trail(draw_map, map_points_px, map_frame_idx, to_ov)
                    if map_frame_idx < len(map_points_px):
                        mx, my = to_ov(*map_points_px[map_frame_idx])
                        _draw_marker(draw_map, mx, my, _heading_rad(map_points_px, map_frame_idx))
                else:
                    if map_frame_idx < len(map_points_px):
                        cx, cy = map_points_px[map_frame_idx]
                    else:
                        cx, cy = map_points_px[-1]
                    map_img, left, top = _crop_centered(
                        canvas, origin_px, origin_py, cx, cy, map_w, map_h,
                    )
                    draw_map = ImageDraw.Draw(map_img)

                    def to_view(px: float, py: float, _l=left, _t=top) -> tuple[int, int]:
                        return int(px - origin_px - _l), int(py - origin_py - _t)

                    _draw_trail(draw_map, map_points_px, map_frame_idx, to_view)
                    mx, my = to_view(cx, cy)
                    _draw_marker(draw_map, mx, my, _heading_rad(map_points_px, map_frame_idx))

                top_crop = int(map_h * 0.20)
                cropped_map = map_img.crop((0, top_crop, map_w, top_crop + visible_map_h))
                _draw_gps_on_map(cropped_map, ft.latitude, ft.longitude, theme)
                border_draw = ImageDraw.Draw(cropped_map)
                border_draw.rectangle(
                    (0, 0, cropped_map.width - 1, cropped_map.height - 1),
                    outline=theme.card_bg,
                    width=5,
                )
                card.paste(cropped_map, (0, 0 if map_on_top else strip_h))

            strip_y = visible_map_h if map_on_top else 0
            _draw_strip(
                card, strip_y, card_w,
                speed_kmh=speed_now,
                speed_unit=speed_unit,
                speed_scale=speed_scale,
                date_utc=ft.date_utc,
                time_utc=ft.time_utc,
                theme=theme,
            )
            proc.stdin.write(card.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        proc.stdin.close()
        reader.join()
        proc.wait()
        return None, card_w, card_h

    reader.join()
    if proc.wait() != 0:
        return None, card_w, card_h
    return panel_output, card_w, card_h

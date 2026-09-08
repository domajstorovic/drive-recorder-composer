"""Render minimap frames with GPS trail and current position marker.

Uses OpenStreetMap tiles with a local disk cache. Falls back to a dark
background with a polyline if tiles cannot be fetched.

Default mode is follow-cam: the vehicle stays centered and the map pans.
"""

from __future__ import annotations

import io
import math
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

from .recording import FPS, Recording
from .telemetry import interpolated_lat_lon

TILE_SIZE = 256
TILE_CACHE_DIR = Path.home() / ".cache" / "bmw-drive-recorder" / "tiles"
USER_AGENT = (
    "bmw-drive-recorder-composer/0.1 "
    "(https://github.com/domajstorovic/bmw-drive-recorder)"
)
MAP_FPS = 30  # match output video so follow-cam pans every frame
FOLLOW_METERS = 220  # approximate width of the follow-cam view


def _lat_lon_to_pixel(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Convert lat/lon to absolute pixel coordinates at given zoom."""
    n = 2 ** zoom
    px = (lon + 180.0) / 360.0 * n * TILE_SIZE
    lat_rad = math.radians(lat)
    py = (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n * TILE_SIZE
    return px, py


def _choose_overview_zoom(lats: list[float], lons: list[float], map_w: int, map_h: int) -> int:
    """Choose a zoom level that fits the bounding box within the map size."""
    if not lats or not lons:
        return 14

    for zoom in range(18, 5, -1):
        px_min_x, px_min_y = _lat_lon_to_pixel(max(lats), min(lons), zoom)
        px_max_x, px_max_y = _lat_lon_to_pixel(min(lats), max(lons), zoom)
        span_x = px_max_x - px_min_x
        span_y = px_max_y - px_min_y
        if span_x < map_w * 0.8 and span_y < map_h * 0.8:
            return zoom

    return 6


def _choose_follow_zoom(lat: float, map_w: int, meters: float = FOLLOW_METERS) -> int:
    """Choose zoom so the viewport is roughly `meters` wide at this latitude."""
    cos_lat = max(0.2, math.cos(math.radians(lat)))
    for zoom in range(18, 11, -1):
        meters_per_pixel = 156543.03392 * cos_lat / (2 ** zoom)
        if map_w * meters_per_pixel >= meters * 0.75:
            return zoom
    return 16


def _fetch_tile(zoom: int, tx: int, ty: int) -> Image.Image | None:
    """Fetch a tile from OSM, with disk caching."""
    cache_path = TILE_CACHE_DIR / str(zoom) / str(tx) / f"{ty}.png"
    if cache_path.exists():
        try:
            return Image.open(cache_path).convert("RGB")
        except Exception:
            pass

    url = f"https://tile.openstreetmap.org/{zoom}/{tx}/{ty}.png"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)
        return Image.open(io.BytesIO(data)).convert("RGB")
    except (urllib.error.URLError, OSError, TimeoutError):
        return None


def _stitch_tiles(
    tile_x_min: int,
    tile_y_min: int,
    tile_x_max: int,
    tile_y_max: int,
    zoom: int,
) -> Image.Image:
    """Stitch OSM tiles into one image. Missing tiles stay dark gray."""
    canvas_w = (tile_x_max - tile_x_min + 1) * TILE_SIZE
    canvas_h = (tile_y_max - tile_y_min + 1) * TILE_SIZE
    canvas = Image.new("RGB", (canvas_w, canvas_h), (40, 40, 40))

    for tx in range(tile_x_min, tile_x_max + 1):
        for ty in range(tile_y_min, tile_y_max + 1):
            tile_img = _fetch_tile(zoom, tx, ty)
            if tile_img:
                ox = (tx - tile_x_min) * TILE_SIZE
                oy = (ty - tile_y_min) * TILE_SIZE
                canvas.paste(tile_img, (ox, oy))

    return canvas


def _render_track_basemap(
    points_px: list[tuple[float, float]],
    zoom: int,
    pad_x: int,
    pad_y: int,
) -> tuple[Image.Image, float, float]:
    """Stitch tiles covering the track plus padding.

    Returns (canvas, origin_px, origin_py) where origin is the absolute
    pixel coordinate of canvas (0, 0).
    """
    min_x = min(p[0] for p in points_px) - pad_x
    max_x = max(p[0] for p in points_px) + pad_x
    min_y = min(p[1] for p in points_px) - pad_y
    max_y = max(p[1] for p in points_px) + pad_y

    tile_x_min = int(min_x // TILE_SIZE)
    tile_y_min = int(min_y // TILE_SIZE)
    tile_x_max = int(max_x // TILE_SIZE)
    tile_y_max = int(max_y // TILE_SIZE)

    canvas = _stitch_tiles(tile_x_min, tile_y_min, tile_x_max, tile_y_max, zoom)
    origin_px = float(tile_x_min * TILE_SIZE)
    origin_py = float(tile_y_min * TILE_SIZE)
    return canvas, origin_px, origin_py


def _heading_rad(points: list[tuple[float, float]], idx: int) -> float | None:
    """Heading in radians (0 = up / north) from recent motion."""
    for back in range(1, min(idx, 8) + 1):
        x0, y0 = points[idx - back]
        x1, y1 = points[idx]
        dx, dy = x1 - x0, y1 - y0
        if dx * dx + dy * dy > 1.0:
            return math.atan2(dx, -dy)
    return None


def _draw_marker(draw: ImageDraw.ImageDraw, cx: int, cy: int, heading: float | None) -> None:
    """Draw a heading chevron, or a circle if heading is unknown."""
    if heading is None:
        r = 7
        draw.ellipse(
            (cx - r, cy - r, cx + r, cy + r),
            fill=(255, 60, 60),
            outline=(255, 255, 255),
            width=2,
        )
        return

    length, width = 12, 8
    tip = (cx + length * math.sin(heading), cy - length * math.cos(heading))
    left = (
        cx + width * math.sin(heading + 2.4),
        cy - width * math.cos(heading + 2.4),
    )
    right = (
        cx + width * math.sin(heading - 2.4),
        cy - width * math.cos(heading - 2.4),
    )
    draw.polygon([tip, left, right], fill=(255, 60, 60), outline=(255, 255, 255))


def _draw_trail(
    draw: ImageDraw.ImageDraw,
    points_px: list[tuple[float, float]],
    current_idx: int,
    to_view,
) -> None:
    if current_idx < 1:
        return
    trail = [to_view(px, py) for px, py in points_px[: current_idx + 1]]
    if len(trail) >= 2:
        draw.line(trail, fill=(0, 150, 255), width=3)


def _crop_centered(
    canvas: Image.Image,
    origin_px: float,
    origin_py: float,
    center_px: float,
    center_py: float,
    map_w: int,
    map_h: int,
) -> tuple[Image.Image, int, int]:
    """Crop a viewport centered on an absolute pixel coordinate.

    Returns (view, left, top) in canvas coordinates.
    """
    left = int(round(center_px - origin_px - map_w / 2))
    top = int(round(center_py - origin_py - map_h / 2))
    left = max(0, min(left, max(0, canvas.width - map_w)))
    top = max(0, min(top, max(0, canvas.height - map_h)))
    view = canvas.crop((left, top, left + map_w, top + map_h))
    if view.size != (map_w, map_h):
        padded = Image.new("RGB", (map_w, map_h), (40, 40, 40))
        padded.paste(view, (0, 0))
        view = padded
    return view, left, top


def _map_frame_points(
    recording: Recording,
    zoom: int,
) -> list[tuple[float, float]]:
    """Absolute map-pixel positions for each minimap frame.

    Lat/lon are linearly interpolated between the ~2 Hz XML samples so the
    follow-cam pan is continuous at MAP_FPS.
    """
    total_frames = recording.entries[-1].frame_index + 1
    video_duration_s = total_frames / FPS
    map_total_frames = max(1, int(round(video_duration_s * MAP_FPS)))

    points: list[tuple[float, float]] = []
    for mf in range(map_total_frames):
        lat, lon = interpolated_lat_lon(recording.entries, mf / MAP_FPS)
        points.append(_lat_lon_to_pixel(lat, lon, zoom))
    return points

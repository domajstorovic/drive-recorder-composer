"""Interpolate telemetry entries to per-frame values."""

from __future__ import annotations

from dataclasses import dataclass

from .recording import FPS, TelemetryEntry


@dataclass
class FrameTelemetry:
    time_s: float
    date_utc: str
    time_utc: str
    latitude: float
    longitude: float


def interpolate_telemetry(
    entries: list[TelemetryEntry],
    total_frames: int | None = None,
    fps: int = FPS,
) -> list[FrameTelemetry]:
    """Produce per-frame telemetry by holding the last known sample.

    The XML data comes at ~2 Hz (every 15 frames). We hold the most recent
    sample until the next one arrives, which gives clean step-function values
    that match what the car was reporting at that moment.
    """
    if not entries:
        return []

    if total_frames is None:
        total_frames = entries[-1].frame_index + 1

    result: list[FrameTelemetry] = []
    entry_idx = 0

    for frame in range(total_frames):
        while (
            entry_idx + 1 < len(entries)
            and entries[entry_idx + 1].frame_index <= frame
        ):
            entry_idx += 1

        e = entries[entry_idx]
        result.append(FrameTelemetry(
            time_s=frame / fps,
            date_utc=e.date_utc,
            time_utc=e.time_utc,
            latitude=e.latitude,
            longitude=e.longitude,
        ))

    return result


def kmh_to_mph(kmh: float) -> float:
    return kmh * 0.621371


def interpolated_lat_lon(
    entries: list[TelemetryEntry],
    time_s: float,
) -> tuple[float, float]:
    """Linearly interpolate lat/lon at an arbitrary time.

    GPS in the XML is ~2 Hz. Linear interpolation between samples gives a
    continuous path for the follow-cam minimap without inventing extra speed.
    """
    if not entries:
        return 0.0, 0.0
    if time_s <= entries[0].time_seconds:
        return entries[0].latitude, entries[0].longitude
    if time_s >= entries[-1].time_seconds:
        return entries[-1].latitude, entries[-1].longitude

    lo, hi = 0, len(entries) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if entries[mid].time_seconds <= time_s:
            lo = mid
        else:
            hi = mid

    t0 = entries[lo].time_seconds
    t1 = entries[hi].time_seconds
    if t1 <= t0:
        return entries[lo].latitude, entries[lo].longitude

    alpha = (time_s - t0) / (t1 - t0)
    lat = entries[lo].latitude + alpha * (entries[hi].latitude - entries[lo].latitude)
    lon = entries[lo].longitude + alpha * (entries[hi].longitude - entries[lo].longitude)
    return lat, lon


def interpolated_speed(
    entries: list[TelemetryEntry],
    time_s: float,
) -> float:
    """Linearly interpolate speed (km/h) at an arbitrary time.

    Produces a smooth ramp between the ~2 Hz reported speed samples.
    """
    if not entries:
        return 0.0
    if time_s <= entries[0].time_seconds:
        return entries[0].velocity_kmh
    if time_s >= entries[-1].time_seconds:
        return entries[-1].velocity_kmh

    lo, hi = 0, len(entries) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if entries[mid].time_seconds <= time_s:
            lo = mid
        else:
            hi = mid

    t0 = entries[lo].time_seconds
    t1 = entries[hi].time_seconds
    if t1 <= t0:
        return entries[lo].velocity_kmh

    alpha = (time_s - t0) / (t1 - t0)
    return entries[lo].velocity_kmh + alpha * (entries[hi].velocity_kmh - entries[lo].velocity_kmh)

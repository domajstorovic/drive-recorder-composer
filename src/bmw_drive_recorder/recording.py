"""Discover recording folders and parse BMW Drive Recorder XML telemetry."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

CAMERA_NAMES = ("Vorn", "Links", "Rechts", "Hinten")
CAMERA_LABELS = {"Vorn": "Front", "Links": "Left", "Rechts": "Right", "Hinten": "Rear"}
HERO_NAME_TO_CAM = {
    "front": "Vorn",
    "left": "Links",
    "right": "Rechts",
    "rear": "Hinten",
}
# Bottom-row order when that camera is not the hero (matches default Front-on-top).
HERO_BOTTOM_ORDER = ("Links", "Hinten", "Rechts", "Vorn")
FPS = 30


@dataclass
class TelemetryEntry:
    id: int
    date_utc: str
    time_utc: str
    velocity_kmh: float
    latitude: float
    longitude: float

    @property
    def frame_index(self) -> int:
        return self.id - 1

    @property
    def time_seconds(self) -> float:
        return self.frame_index / FPS


@dataclass
class Recording:
    folder: Path
    name: str
    vin: str = ""
    entries: list[TelemetryEntry] = field(default_factory=list)
    cameras_processed: dict[str, Path] = field(default_factory=dict)
    cameras_raw: dict[str, Path] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        if not self.entries:
            return 0.0
        return self.entries[-1].time_seconds + 1.0 / FPS

    def camera_paths(self, source: str = "processed") -> dict[str, Path]:
        if source == "raw":
            return self.cameras_raw
        return self.cameras_processed


def parse_xml(xml_path: Path) -> tuple[str, list[TelemetryEntry]]:
    """Parse the BMW Drive Recorder XML metadata file."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    vin = (root.findtext("VIN") or "").strip()

    entries: list[TelemetryEntry] = []
    for entry_el in root.findall("./LOG/ENTRY"):
        entries.append(TelemetryEntry(
            id=int(entry_el.findtext("ID") or "0"),
            date_utc=(entry_el.findtext("DATE_UTC") or "").strip(),
            time_utc=(entry_el.findtext("TIME_UTC") or "").strip(),
            velocity_kmh=float(entry_el.findtext("VELOCITY_KMH") or "0"),
            latitude=float(entry_el.findtext("LATITUDE") or "0"),
            longitude=float(entry_el.findtext("LONGITUDE") or "0"),
        ))

    entries.sort(key=lambda e: e.id)
    return vin, entries


def discover_cameras(folder: Path, base_name: str) -> tuple[dict[str, Path], dict[str, Path]]:
    """Find camera MP4 files in the recording folder."""
    processed: dict[str, Path] = {}
    raw: dict[str, Path] = {}

    for cam in CAMERA_NAMES:
        proc_path = folder / f"{base_name}_Kamera_{cam}.mp4"
        if proc_path.exists():
            processed[cam] = proc_path

        raw_path = folder / f"{base_name}_Kamera_{cam}_Rohdaten.mp4"
        if raw_path.exists():
            raw[cam] = raw_path

    return processed, raw


def load_recording(folder: Path) -> Recording:
    """Load a single recording folder: XML + camera discovery."""
    folder = folder.resolve()

    xml_files = list(folder.glob("*.xml"))
    if not xml_files:
        raise FileNotFoundError(f"No XML metadata file found in {folder}")
    xml_path = xml_files[0]

    base_name = xml_path.stem
    vin, entries = parse_xml(xml_path)
    processed, raw = discover_cameras(folder, base_name)

    if not processed and not raw:
        raise FileNotFoundError(f"No camera MP4 files found in {folder}")

    return Recording(
        folder=folder,
        name=base_name,
        vin=vin,
        entries=entries,
        cameras_processed=processed,
        cameras_raw=raw,
    )


def find_recordings(path: Path) -> list[Recording]:
    """Find one or more recording folders under `path`.

    If `path` itself is a recording folder (contains XML), return it.
    Otherwise look one level deep for subfolders with XML files.
    """
    path = path.resolve()

    if list(path.glob("*.xml")):
        return [load_recording(path)]

    recordings: list[Recording] = []
    for sub in sorted(path.iterdir()):
        if sub.is_dir() and list(sub.glob("*.xml")):
            try:
                recordings.append(load_recording(sub))
            except FileNotFoundError:
                continue

    return recordings

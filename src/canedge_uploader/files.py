from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


CANEDGE_DEVICE_RE = re.compile(r"^[0-9A-F]{8}$", re.IGNORECASE)
CANEDGE_INDEX_RE = re.compile(r"^\d{8}$")


@dataclass(frozen=True)
class CanedgeLog:
    """A logger-written file at <device>/<session>/<split>.MF4."""

    path: Path
    device_id: str
    session: int
    split: int

    @property
    def position(self) -> tuple[int, int]:
        return self.session, self.split

    @property
    def label(self) -> str:
        return f"{self.device_id}/{self.path.parent.name}/{self.path.name}"


def discover_canedge_logs(folder: Path) -> list[CanedgeLog]:
    """Find CANedge session logs, oldest first per device, ignoring trash and copied files."""
    folder = folder.expanduser().resolve()
    logs: list[CanedgeLog] = []
    for path in discover_mf4_files(folder):
        if any(part.startswith(".") for part in path.relative_to(folder).parts):
            continue
        device, session = path.parent.parent.name, path.parent.name
        if CANEDGE_DEVICE_RE.match(device) and CANEDGE_INDEX_RE.match(session) and CANEDGE_INDEX_RE.match(path.stem):
            logs.append(CanedgeLog(path, device.upper(), int(session), int(path.stem)))
    return sorted(logs, key=lambda log: (log.device_id, log.position))


def discover_mf4_files(folder: Path) -> list[Path]:
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise NotADirectoryError(f"CANedge output folder does not exist: {folder}")
    return sorted(path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() == ".mf4")


def mf4_inputs(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved.is_file():
            if resolved.suffix.lower() != ".mf4":
                raise ValueError(f"Not an MF4 file: {resolved}")
            files.append(resolved)
        elif resolved.is_dir():
            files.extend(discover_mf4_files(resolved))
        else:
            raise FileNotFoundError(f"Input does not exist: {resolved}")
    return sorted(dict.fromkeys(files))


def removable_roots() -> list[Path]:
    if os.name != "nt":
        return []
    import ctypes

    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    roots: list[Path] = []
    for index in range(26):
        if not bitmask & (1 << index):
            continue
        root = f"{chr(65 + index)}:\\"
        if ctypes.windll.kernel32.GetDriveTypeW(root) == 2:
            roots.append(Path(root))
    return roots


def detect_removable_source() -> Path | None:
    candidates = [root for root in removable_roots() if root.exists()]
    with_logs = [root for root in candidates if (root / "LOG").exists() or (root / "LOGS").exists()]
    for root in with_logs + candidates:
        try:
            if discover_canedge_logs(root):
                return root
        except OSError:
            continue
    return candidates[0] if candidates else None


def discover_dbcs(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise NotADirectoryError(f"DBC folder does not exist: {folder}")
    files = sorted(path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() == ".dbc")
    if not files:
        raise FileNotFoundError(f"No DBC files found under {folder}")
    return files


def sha256_file(path: Path, block_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def dbc_bundle_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def eastern_time(value, timezone_name: str):
    """Interpret naive logger header times as UTC and convert to Eastern time."""
    from datetime import timezone

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(timezone_name))


def infer_device_id(source: Path, root: Path) -> str:
    """Use the CANedge session directory, falling back to the file stem."""
    try:
        relative = source.relative_to(root)
        if len(relative.parts) >= 2:
            return relative.parent.name
    except ValueError:
        pass
    return source.parent.name or source.stem


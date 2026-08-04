from __future__ import annotations

import logging
import tempfile
from contextlib import nullcontext
from pathlib import Path
from threading import Event
from typing import Any

from .config import Settings
from .files import discover_dbcs, discover_mf4_files, eastern_time, infer_device_id
from .models import ProgressCallback, ProgressEvent, RecordingContext, RunSummary
from .naming import make_artifact_plans
from . import rules

log = logging.getLogger(__name__)


class CancelledError(RuntimeError):
    pass


class Processor:
    def __init__(
        self,
        settings: Settings,
        destination: Any | None = None,
        progress: ProgressCallback | None = None,
        cancel: Event | None = None,
    ):
        self.settings = settings
        self.destination = destination
        self.progress = progress or (lambda event: None)
        self.cancel = cancel or Event()
        self._remote_name_cache: dict[str, set[str]] = {}
        self._active_file_index = 0
        self._file_total = 0

    def _emit(self, stage: str, message: str, current: int = 0, total: int = 0, file: Path | None = None):
        event = ProgressEvent(
            stage,
            message,
            current,
            total,
            file,
            file_index=self._active_file_index,
            file_total=self._file_total,
        )
        self.progress(event)
        log.info("%s: %s", stage, message)

    def _check_cancel(self):
        if self.cancel.is_set():
            raise CancelledError("Upload cancelled")

    def run(self, source_root: Path, output_dir: Path | None = None, force: bool = False) -> RunSummary:
        source_root = source_root.expanduser().resolve()
        files = discover_mf4_files(source_root)
        dbcs = discover_dbcs(self.settings.dbc_dir)
        summary = RunSummary(discovered=len(files))
        self._file_total = len(files)
        if not files:
            self._emit("scan", "No MF4 files were found")
            return summary
        self._emit("scan", f"Found {len(files)} MF4 files and {len(dbcs)} DBC files")

        if self.destination:
            self._emit("sharepoint", "Connecting to SharePoint")
            self.destination.prepare()
            summary.root_url = self.destination.root_url

        temp_context = nullcontext(output_dir) if output_dir else tempfile.TemporaryDirectory(prefix="canedge-uploader-")
        with temp_context as temp_value:
            work_dir = Path(temp_value)
            work_dir.mkdir(parents=True, exist_ok=True)
            for index, source in enumerate(files, start=1):
                self._active_file_index = index
                self._check_cancel()
                self._emit("file", f"Inspecting {source.name} ({index}/{len(files)})", index, len(files), source)
                try:
                    self._process_one(source, source_root, dbcs, work_dir, summary, force)
                except CancelledError:
                    raise
                except Exception as exc:
                    summary.failed += 1
                    log.exception("Failed to process %s", source)
                    self._emit("error", f"{source}: {exc}", index, len(files), source)
                finally:
                    if not self.cancel.is_set():
                        self._emit(
                            "file_complete",
                            f"Finished file {index} of {len(files)}",
                            index,
                            len(files),
                            source,
                        )

        completed = f"{summary.uploaded} uploaded" if self.destination else f"{summary.saved} saved locally"
        self._emit(
            "complete",
            f"Done: {completed}, {summary.skipped} skipped, {summary.failed} failed",
            len(files),
            len(files),
        )
        return summary

    def _process_one(self, source, source_root, dbcs, work_dir, summary, force):
        try:
            from asammdf import MDF
        except ImportError as exc:
            raise RuntimeError("asammdf is not installed. Run: pip install -e .") from exc

        raw = MDF(str(source))
        decoded = None
        try:
            local_start = eastern_time(raw.header.start_time, self.settings.timezone)
            context = RecordingContext(
                source_path=source,
                source_root=source_root,
                source_digest="",
                dbc_digest="",
                start_time=local_start,
                device_id=infer_device_id(source, source_root),
            )

            plans = None
            if not rules.requires_decoded_data():
                plans = make_artifact_plans(context, rules.build_segments(None, context))
                if self.destination and not force and self._all_remote(plans):
                    summary.skipped += len(plans)
                    self._emit("skip", f"Already uploaded: {', '.join(plan.filename for plan in plans)}", file=source)
                    return

            self._emit("decode", f"Decoding {source.name}", file=source)

            def decode_progress(current, total):
                self._check_cancel()
                self.progress(
                    ProgressEvent(
                        "decode",
                        f"Decoding {source.name}",
                        current,
                        total,
                        source,
                        file_index=self._active_file_index,
                        file_total=self._file_total,
                    )
                )

            decoded = raw.extract_bus_logging(
                database_files={"CAN": [(str(path), 0) for path in dbcs]},
                version="4.10",
                progress=decode_progress,
            )
            decoded.header.start_time = local_start
            summary.decoded += 1
            if plans is None:
                plans = make_artifact_plans(context, rules.build_segments(decoded, context))

            for plan in plans:
                if self.destination and not force:
                    self._remote_names(plan.calendar_date)
                if plan.filename in self._remote_name_cache.get(plan.calendar_date, set()) and not force:
                    summary.skipped += 1
                    self._emit("skip", f"Already uploaded: {plan.filename}", file=source)
                    continue
                self._check_cancel()
                output = work_dir / plan.filename
                artifact = decoded
                owns_artifact = False
                if plan.segment.start is not None or plan.segment.stop is not None:
                    artifact = decoded.cut(start=plan.segment.start, stop=plan.segment.stop, whence=0)
                    owns_artifact = True
                try:
                    artifact.header.start_time = plan.start_time
                    self._emit("save", f"Writing {plan.filename}", file=source)
                    artifact.save(str(output), overwrite=True, compression=2)
                finally:
                    if owns_artifact:
                        artifact.close()

                if self.destination:
                    self._emit("upload", f"Uploading {plan.filename}", file=source)

                    def upload_progress(current, total):
                        self._check_cancel()
                        self.progress(
                            ProgressEvent(
                                "upload",
                                f"Uploading {plan.filename}",
                                current,
                                total,
                                source,
                                file_index=self._active_file_index,
                                file_total=self._file_total,
                            )
                        )

                    result = self.destination.upload_file(output, plan.calendar_date, upload_progress, overwrite=force)
                    summary.uploaded += 1
                    summary.uploaded_urls.append(str(result.get("webUrl", "")))
                    self._remote_name_cache.setdefault(plan.calendar_date, set()).add(plan.filename)
                else:
                    summary.saved += 1
                    self._emit("saved", f"Saved {output}", file=source)
        finally:
            if decoded is not None:
                decoded.close()
            raw.close()

    def _all_remote(self, plans) -> bool:
        for plan in plans:
            if plan.filename not in self._remote_names(plan.calendar_date):
                return False
        return True

    def _remote_names(self, calendar_date: str) -> set[str]:
        if calendar_date not in self._remote_name_cache:
            self._remote_name_cache[calendar_date] = self.destination.names_in_date(calendar_date)
        return self._remote_name_cache[calendar_date]

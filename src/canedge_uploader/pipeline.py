from __future__ import annotations

import logging
import os
import re
import tempfile
from contextlib import nullcontext
from pathlib import Path
from threading import Event
from typing import Any

from . import rules
from .config import Settings
from .files import discover_dbcs, discover_mf4_files, eastern_time, infer_device_id, mf4_inputs
from .models import (
    ArtifactPlan,
    CloudDay,
    ProgressCallback,
    ProgressEvent,
    RecordingContext,
    RunSummary,
    UploadPreview,
    UploadPreviewItem,
)
from .naming import make_artifact_plans, make_raw_artifact_plan

log = logging.getLogger(__name__)

CAN_MESSAGE_COMMENT_RE = re.compile(r"<TX>.*? - ([^<]+)</TX>", re.DOTALL)


class CancelledError(RuntimeError):
    pass


def clean_can_source_names(mdf: Any) -> None:
    """Use DBC message names for decoded CAN source labels."""
    for group in getattr(mdf, "groups", []):
        for channel in getattr(group, "channels", []):
            source = getattr(channel, "source", None)
            if source is None:
                continue

            match = CAN_MESSAGE_COMMENT_RE.search(getattr(source, "comment", "") or "")
            if not match:
                continue

            message_name = match.group(1).strip()
            if not message_name:
                continue

            source.name = message_name
            source.path = message_name


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

    def prepare_destination(self) -> None:
        if not self.destination:
            return
        self._emit("sharepoint", "Connecting to SharePoint")
        self.destination.prepare()

    def preview_upload_from_sd(self, source_root: Path) -> UploadPreview:
        if not self.destination:
            raise RuntimeError("Upload preview requires a SharePoint destination")
        source_root = source_root.expanduser().resolve()
        files = discover_mf4_files(source_root)
        self.prepare_destination()
        self._emit("scan", f"Found {len(files)} MF4 files on {source_root}")
        items: list[UploadPreviewItem] = []
        for index, source in enumerate(files, start=1):
            self._active_file_index = index
            self._file_total = len(files)
            context = self._inspect_context(source, source_root)
            raw_plan = make_raw_artifact_plan(context)
            raw_exists = raw_plan.filename in self._raw_remote_names(raw_plan.calendar_date)
            items.append(
                UploadPreviewItem(
                    source_path=source,
                    calendar_date=raw_plan.calendar_date,
                    raw_filename=raw_plan.filename,
                    raw_exists=raw_exists,
                )
            )
        return UploadPreview(source_root, items, decoded_filenames_known=not rules.requires_decoded_data())

    def upload_from_sd(self, source_root: Path, preview: UploadPreview | None = None) -> RunSummary:
        source_root = source_root.expanduser().resolve()
        files = [item.source_path for item in preview.items] if preview else discover_mf4_files(source_root)
        summary = RunSummary(discovered=len(files))
        self._file_total = len(files)
        if not files:
            self._emit("scan", "No MF4 files were found")
            return summary
        if not self.destination:
            raise RuntimeError("Upload requires a SharePoint destination")
        self.prepare_destination()
        summary.root_url = self.destination.root_url
        with tempfile.TemporaryDirectory(prefix="canedge-uploader-") as directory:
            self._process_files(
                files,
                source_root,
                Path(directory),
                summary,
                upload_decoded=True,
                upload_raw=True,
                skip_existing_decoded=True,
                overwrite_decoded=False,
                overwrite_raw=False,
            )
        return self._complete(summary)

    def decode_local(self, inputs: list[Path], output_dir: Path) -> RunSummary:
        files = mf4_inputs(inputs)
        output_dir = output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        source_root = self._common_root(files)
        summary = RunSummary(discovered=len(files))
        self._file_total = len(files)
        self._process_files(
            files,
            source_root,
            output_dir,
            summary,
            upload_decoded=False,
            upload_raw=False,
            skip_existing_decoded=False,
            overwrite_decoded=True,
            overwrite_raw=False,
        )
        return self._complete(summary)

    def list_cloud_days(self) -> list[CloudDay]:
        if not self.destination:
            raise RuntimeError("Cloud day listing requires a SharePoint destination")
        self.prepare_destination()
        days: list[CloudDay] = []
        for name in self.destination.list_date_folders():
            days.append(CloudDay(name=name, raw_count=len(self.destination.raw_items_in_date(name))))
        return days

    def redecode_cloud_days(self, days: list[str]) -> RunSummary:
        if not self.destination:
            raise RuntimeError("Cloud re-decode requires a SharePoint destination")
        self.prepare_destination()
        day_items = [(day, self.destination.raw_items_in_date(day)) for day in days]
        raw_items = [(day, item) for day, items in day_items for item in items]
        summary = RunSummary(discovered=len(raw_items), root_url=self.destination.root_url)
        self._file_total = len(raw_items)
        if not raw_items:
            self._emit("scan", "No raw MF4 files were found in the selected cloud days")
            return self._complete(summary)

        with tempfile.TemporaryDirectory(prefix="canedge-cloud-redecode-") as directory:
            work_dir = Path(directory)
            dbcs = discover_dbcs(self.settings.dbc_dir)
            index = 0
            for day, items in day_items:
                downloaded: list[tuple[int, Path]] = []
                for item in items:
                    index += 1
                    self._active_file_index = index
                    self._check_cancel()
                    local_raw = work_dir / day / "Raw" / str(item["name"])
                    local_raw.parent.mkdir(parents=True, exist_ok=True)
                    self._emit("download", f"Downloading Raw/{item['name']} from {day}", index, len(raw_items), local_raw)
                    self.destination.download_item(item["id"], local_raw, self._download_progress(local_raw))
                    downloaded.append((index, local_raw))
                self._check_cancel()
                self._emit("cleanup", f"Clearing decoded MF4 files for {day}")
                self.destination.delete_decoded_files(day)
                for file_index, local_raw in downloaded:
                    self._active_file_index = file_index
                    self._process_one(
                        local_raw,
                        work_dir,
                        dbcs,
                        work_dir,
                        summary,
                        upload_decoded=True,
                        upload_raw=False,
                        skip_existing_decoded=False,
                        overwrite_decoded=True,
                        overwrite_raw=False,
                    )
        return self._complete(summary)

    def run(self, source_root: Path, output_dir: Path | None = None) -> RunSummary:
        if self.destination:
            return self.upload_from_sd(source_root)
        if output_dir is None:
            output_dir = Path("decoded_output")
        return self.decode_local([source_root], output_dir)

    def _process_files(
        self,
        files: list[Path],
        source_root: Path,
        work_dir: Path,
        summary: RunSummary,
        *,
        upload_decoded: bool,
        upload_raw: bool,
        skip_existing_decoded: bool,
        overwrite_decoded: bool,
        overwrite_raw: bool,
    ) -> None:
        dbcs = discover_dbcs(self.settings.dbc_dir)
        if not files:
            self._emit("scan", "No MF4 files were found")
            return
        self._emit("scan", f"Processing {len(files)} MF4 files with {len(dbcs)} DBC files")
        with nullcontext(work_dir):
            work_dir.mkdir(parents=True, exist_ok=True)
            for index, source in enumerate(files, start=1):
                self._active_file_index = index
                self._check_cancel()
                self._emit("file", f"Inspecting {source.name} ({index}/{len(files)})", index, len(files), source)
                try:
                    self._process_one(
                        source,
                        source_root,
                        dbcs,
                        work_dir,
                        summary,
                        upload_decoded=upload_decoded,
                        upload_raw=upload_raw,
                        skip_existing_decoded=skip_existing_decoded,
                        overwrite_decoded=overwrite_decoded,
                        overwrite_raw=overwrite_raw,
                    )
                except CancelledError:
                    raise
                except Exception as exc:
                    summary.failed += 1
                    log.exception("Failed to process %s", source)
                    self._emit("error", f"{source}: {exc}", index, len(files), source)
                finally:
                    if not self.cancel.is_set():
                        self._emit("file_complete", f"Finished file {index} of {len(files)}", index, len(files), source)

    def _process_one(
        self,
        source: Path,
        source_root: Path,
        dbcs: list[Path],
        work_dir: Path,
        summary: RunSummary,
        *,
        upload_decoded: bool,
        upload_raw: bool,
        skip_existing_decoded: bool,
        overwrite_decoded: bool,
        overwrite_raw: bool,
    ) -> None:
        try:
            from asammdf import MDF
        except ImportError as exc:
            raise RuntimeError("asammdf is not installed. Run: pip install -e .") from exc

        raw = MDF(str(source))
        decoded = None
        try:
            context = self._context_from_mdf(raw, source, source_root)
            raw_plan = make_raw_artifact_plan(context)
            plans = None
            if not rules.requires_decoded_data():
                plans = make_artifact_plans(context, rules.build_segments(None, context))
                if upload_decoded and skip_existing_decoded and self._all_remote(plans):
                    summary.skipped += len(plans)
                    if upload_raw:
                        self._upload_or_skip_raw(source, raw_plan, summary, overwrite_raw)
                    return

            self._emit("decode", f"Decoding {source.name}", file=source)
            decoded = raw.extract_bus_logging(
                database_files={"CAN": [(str(path), self.settings.decode_can_bus) for path in dbcs]},
                version="4.10",
                progress=self._decode_progress(source),
            )
            clean_can_source_names(decoded)
            decoded.header.start_time = context.start_time
            summary.decoded += 1
            if plans is None:
                plans = make_artifact_plans(context, rules.build_segments(decoded, context))

            for plan in plans:
                if upload_decoded and skip_existing_decoded:
                    self._remote_names(plan.calendar_date)
                    if plan.filename in self._remote_name_cache.get(plan.calendar_date, set()):
                        summary.skipped += 1
                        self._emit("skip", f"Already uploaded: {plan.filename}", file=source)
                        continue
                output = work_dir / plan.filename
                self._save_decoded_artifact(decoded, plan, output, source)
                if upload_decoded:
                    self._upload_decoded(output, plan, summary, overwrite_decoded, source)
                else:
                    summary.saved += 1
                    self._emit("saved", f"Saved {output}", file=source)

            if upload_raw:
                self._upload_or_skip_raw(source, raw_plan, summary, overwrite_raw)
        finally:
            if decoded is not None:
                decoded.close()
            raw.close()

    def _inspect_context(self, source: Path, source_root: Path) -> RecordingContext:
        try:
            from asammdf import MDF
        except ImportError as exc:
            raise RuntimeError("asammdf is not installed. Run: pip install -e .") from exc

        raw = MDF(str(source))
        try:
            return self._context_from_mdf(raw, source, source_root)
        finally:
            raw.close()

    def _context_from_mdf(self, raw, source: Path, source_root: Path) -> RecordingContext:
        local_start = eastern_time(raw.header.start_time, self.settings.timezone)
        return RecordingContext(
            source_path=source,
            source_root=source_root,
            source_digest="",
            dbc_digest="",
            start_time=local_start,
            device_id=infer_device_id(source, source_root),
            duration_seconds=self._recording_duration(raw),
        )

    def _save_decoded_artifact(self, decoded, plan: ArtifactPlan, output: Path, source: Path) -> None:
        self._check_cancel()
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

    def _upload_decoded(
        self,
        output: Path,
        plan: ArtifactPlan,
        summary: RunSummary,
        overwrite_decoded: bool,
        source: Path,
    ) -> None:
        self._emit("upload", f"Uploading {plan.filename}", file=source)
        result = self.destination.upload_file(
            output,
            plan.calendar_date,
            self._upload_progress(source, plan.filename),
            overwrite=overwrite_decoded,
        )
        summary.uploaded += 1
        summary.uploaded_urls.append(str(result.get("webUrl", "")))
        self._remote_name_cache.setdefault(plan.calendar_date, set()).add(plan.filename)

    def _upload_or_skip_raw(
        self,
        source: Path,
        raw_plan: ArtifactPlan,
        summary: RunSummary,
        overwrite_raw: bool,
    ) -> None:
        if not overwrite_raw and raw_plan.filename in self._raw_remote_names(raw_plan.calendar_date):
            summary.skipped += 1
            self._emit("skip", f"Already uploaded: Raw/{raw_plan.filename}", file=source)
            return
        self._emit("upload", f"Uploading Raw/{raw_plan.filename}", file=source)
        result = self.destination.upload_file(
            source,
            raw_plan.calendar_date,
            self._upload_progress(source, f"Raw/{raw_plan.filename}"),
            overwrite=overwrite_raw,
            subfolder="Raw",
            upload_name=raw_plan.filename,
        )
        summary.uploaded += 1
        summary.uploaded_urls.append(str(result.get("webUrl", "")))
        self._remote_name_cache.setdefault(f"{raw_plan.calendar_date}/Raw", set()).add(raw_plan.filename)

    def _decode_progress(self, source: Path):
        def progress(current, total):
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

        return progress

    def _download_progress(self, path: Path):
        def progress(current, total):
            self._check_cancel()
            self.progress(
                ProgressEvent(
                    "download",
                    f"Downloading {path.name}",
                    current,
                    total,
                    path,
                    file_index=self._active_file_index,
                    file_total=self._file_total,
                )
            )

        return progress

    def _upload_progress(self, source: Path, filename: str):
        def progress(current, total):
            self._check_cancel()
            self.progress(
                ProgressEvent(
                    "upload",
                    f"Uploading {filename}",
                    current,
                    total,
                    source,
                    file_index=self._active_file_index,
                    file_total=self._file_total,
                )
            )

        return progress

    def _all_remote(self, plans: list[ArtifactPlan]) -> bool:
        for plan in plans:
            if plan.filename not in self._remote_names(plan.calendar_date):
                return False
        return True

    def _remote_names(self, calendar_date: str) -> set[str]:
        if calendar_date not in self._remote_name_cache:
            self._remote_name_cache[calendar_date] = self.destination.names_in_date(calendar_date)
        return self._remote_name_cache[calendar_date]

    def _raw_remote_names(self, calendar_date: str) -> set[str]:
        cache_key = f"{calendar_date}/Raw"
        if cache_key not in self._remote_name_cache:
            self._remote_name_cache[cache_key] = self.destination.names_in_date(calendar_date, subfolder="Raw")
        return self._remote_name_cache[cache_key]

    def _recording_duration(self, mdf) -> float:
        last_timestamp = getattr(mdf, "last_timestamp", None)
        if last_timestamp is not None:
            try:
                return max(0.0, float(last_timestamp))
            except (TypeError, ValueError):
                pass

        duration = 0.0
        for group_index, _group in enumerate(getattr(mdf, "groups", [])):
            try:
                master = mdf.get_master(group_index)
            except Exception:
                log.debug("Unable to read master timestamps for MDF group %s", group_index, exc_info=True)
                continue
            if len(master):
                duration = max(duration, float(master[-1]))
        return duration

    def _common_root(self, files: list[Path]) -> Path:
        if not files:
            return Path.cwd()
        common = Path(os.path.commonpath([str(path) for path in files]))
        return common if common.is_dir() else common.parent

    def _complete(self, summary: RunSummary) -> RunSummary:
        completed = f"{summary.uploaded} uploaded" if self.destination else f"{summary.saved} saved locally"
        self._emit(
            "complete",
            f"Done: {completed}, {summary.skipped} skipped, {summary.failed} failed",
            summary.discovered,
            summary.discovered,
        )
        return summary

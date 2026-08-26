from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_settings
from .logging_setup import configure_logging
from .models import ProgressEvent
from .pipeline import CancelledError, Processor
from .sharepoint import GraphClient, SharePointDestination


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="canedge-uploader", description="Decode CANedge MF4 logs and upload them to SharePoint")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose file logging")
    parser.add_argument("--env-file", type=Path, help="Configuration file (defaults to ./env or ./.env)")
    sub = parser.add_subparsers(dest="command")

    upload = sub.add_parser("upload", help="Upload raw SD logs to SharePoint and decode them")
    upload.add_argument("source", type=Path, help="SD card root or folder containing CANedge MF4 logs")
    upload.add_argument("--dbc-dir", type=Path, help="Override the bundled DBC folder")
    upload.add_argument("--preview", action="store_true", help="Show the raw files that would be uploaded, then exit")

    redecode = sub.add_parser("redecode-cloud", help="Re-decode raw logs already uploaded to SharePoint")
    redecode.add_argument("days", nargs="+", help="Cloud date folder names to re-decode, such as 2026-08-03")
    redecode.add_argument("--dbc-dir", type=Path, help="Override the bundled DBC folder")

    process = sub.add_parser("process", help="Decode to a local folder without SharePoint")
    process.add_argument("inputs", nargs="+", type=Path, help="One or more MF4 files or folders")
    process.add_argument("--output", type=Path, default=Path("decoded_output"), help="Local output directory")
    process.add_argument("--dbc-dir", type=Path, help="Override the bundled DBC folder")

    sub.add_parser("gui", help="Open the desktop interface")
    return parser


_PROGRESS_BUCKETS: dict[str, int] = {}


def _print_progress(event: ProgressEvent) -> None:
    if event.total:
        percent = min(100, round(event.current / event.total * 100))
        if event.stage in ("decode", "upload"):
            key = f"{event.stage}:{event.file}"
            bucket = percent // 10
            if _PROGRESS_BUCKETS.get(key) == bucket:
                return
            _PROGRESS_BUCKETS[key] = bucket
        print(f"[{event.stage:10}] {percent:3}% {event.message}", flush=True)
    else:
        print(f"[{event.stage:10}] {event.message}", flush=True)


def main(argv: list[str] | None = None) -> int:
    _PROGRESS_BUCKETS.clear()
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command in (None, "gui"):
        from .gui import main as gui_main

        gui_main()
        return 0

    log_file = configure_logging(args.verbose)
    settings = load_settings(args.env_file, args.dbc_dir)
    destination = None
    if args.command in ("upload", "redecode-cloud"):
        if not settings.configured_for_sharepoint or not settings.output_parent_sp_path:
            parser.error("SharePoint configuration is incomplete in env/.env")
        client = GraphClient(settings, auth_message=lambda message: print(f"\n{message}\n", flush=True))
        destination = SharePointDestination(client, settings)
    processor = Processor(settings, destination=destination, progress=_print_progress)
    try:
        if args.command == "upload":
            preview = processor.preview_upload_from_sd(args.source)
            print(f"{len(preview.items)} MF4 files found.")
            print(f"{preview.upload_count} raw files will be uploaded.")
            for item in preview.items:
                status = "already uploaded" if item.raw_exists else "will upload"
                print(f"{item.calendar_date}  Raw/{item.raw_filename}  {status}")
            if args.preview:
                return 0
            summary = processor.upload_from_sd(args.source, preview)
        elif args.command == "redecode-cloud":
            summary = processor.redecode_cloud_days(args.days)
        else:
            summary = processor.decode_local(args.inputs, args.output)
    except (CancelledError, KeyboardInterrupt):
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}\nDebug log: {log_file}", file=sys.stderr)
        return 1
    if summary.root_url:
        print(f"SharePoint folder: {summary.root_url}")
    print(f"Debug log: {log_file}")
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

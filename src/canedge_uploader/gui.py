from __future__ import annotations

import queue
import re
import threading
import webbrowser
import logging
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

from .config import load_settings
from .logging_setup import configure_logging
from .models import ProgressEvent
from .pipeline import Processor
from .sharepoint import GraphClient, SharePointDestination

log = logging.getLogger(__name__)


def _device_login_details(message: str) -> tuple[str, str]:
    match = re.search(r"open the page (https://\S+) and enter the code ([A-Z0-9]+)", message, re.IGNORECASE)
    return (match.group(1), match.group(2)) if match else ("https://login.microsoft.com/device", "")


def _overall_percent(event: ProgressEvent) -> float:
    """Map a per-stage event onto total batch progress."""
    if event.stage == "complete":
        return 100.0
    if event.file_total <= 0 or event.file_index <= 0:
        return 0.0
    stage_fraction = {
        "file": 0.02,
        "skip": 1.0,
        "error": 1.0,
        "save": 0.78,
        "saved": 0.82,
        "upload": 0.82,
        "file_complete": 1.0,
    }.get(event.stage, 0.0)
    if event.stage == "decode":
        inner = event.current / event.total if event.total else 0.0
        stage_fraction = 0.05 + 0.73 * min(1.0, inner)
    elif event.stage == "upload" and event.total:
        inner = event.current / event.total
        stage_fraction = 0.82 + 0.18 * min(1.0, inner)
    completed_before = event.file_index - 1
    return min(100.0, (completed_before + stage_fraction) / event.file_total * 100.0)


class UploaderWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CANedge SharePoint Uploader")
        self.root.geometry("760x560")
        self.root.minsize(640, 480)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.root_url = ""
        self._progress_buckets: dict[str, int] = {}
        self.log_file = configure_logging()
        self.settings_preview = load_settings()
        self.folder = tk.StringVar()
        self.status = tk.StringVar(value="Drop the CANedge output folder below, or click Browse.")
        self.file_count = tk.StringVar(value="0 / 0 files")
        self.progress_value = tk.DoubleVar(value=0)
        self._build()
        self.root.after(100, self._drain_events)

    def _build(self):
        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="CANedge Log Uploader", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Decode bundled DBC signals and upload only new logs to SharePoint.").pack(anchor="w", pady=(2, 18))

        drop = ttk.LabelFrame(outer, text="CANedge output folder", padding=14)
        drop.pack(fill="x")
        row = ttk.Frame(drop)
        row.pack(fill="x")
        entry = ttk.Entry(row, textvariable=self.folder)
        entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=self._browse).pack(side="left", padx=(8, 0))
        ttk.Label(drop, text="You can drop the CANEDGE_OUTPUT folder here.", foreground="#555").pack(anchor="w", pady=(10, 0))
        try:
            from tkinterdnd2 import DND_FILES

            entry.drop_target_register(DND_FILES)
            entry.dnd_bind("<<Drop>>", self._drop)
            drop.drop_target_register(DND_FILES)
            drop.dnd_bind("<<Drop>>", self._drop)
        except (ImportError, tk.TclError):
            pass

        destination = self.settings_preview.output_parent_sp_path or "NOT CONFIGURED"
        ttk.Label(outer, text=f"SharePoint destination: {destination}", foreground="#555", wraplength=700).pack(
            anchor="w", pady=(10, 0)
        )

        progress_header = ttk.Frame(outer)
        progress_header.pack(fill="x", pady=(18, 6))
        ttk.Label(progress_header, textvariable=self.status, wraplength=570).pack(side="left", anchor="w")
        ttk.Label(progress_header, textvariable=self.file_count, font=("Segoe UI", 10, "bold")).pack(side="right")
        self.progress = ttk.Progressbar(outer, variable=self.progress_value, maximum=100)
        self.progress.pack(fill="x")

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=12)
        self.upload_button = ttk.Button(buttons, text="Upload", command=self._start)
        self.upload_button.pack(side="left")
        self.cancel_button = ttk.Button(buttons, text="Cancel", command=self.cancel_event.set, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        self.open_button = ttk.Button(buttons, text="Open SharePoint Folder", command=self._open_root, state="disabled")
        self.open_button.pack(side="right")

        log_frame = ttk.LabelFrame(outer, text="Activity", padding=8)
        log_frame.pack(fill="both", expand=True)
        self.activity = tk.Text(log_frame, height=12, state="disabled", wrap="word", font=("Consolas", 9))
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.activity.yview)
        self.activity.configure(yscrollcommand=scroll.set)
        self.activity.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        ttk.Label(outer, text=f"Debug log: {self.log_file}", foreground="#666").pack(anchor="w", pady=(8, 0))

    def _browse(self):
        selected = filedialog.askdirectory(title="Select CANedge output folder")
        if selected:
            self.folder.set(selected)

    def _drop(self, event):
        paths = self.root.tk.splitlist(event.data)
        if paths:
            self.folder.set(paths[0])

    def _append(self, text: str):
        self.activity.configure(state="normal")
        self.activity.insert("end", text.rstrip() + "\n")
        self.activity.see("end")
        self.activity.configure(state="disabled")

    def _start(self):
        source = Path(self.folder.get()).expanduser()
        if not source.is_dir():
            messagebox.showerror("Folder required", "Select a valid CANedge output folder first.")
            return
        self.cancel_event.clear()
        self.upload_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.open_button.configure(state="disabled")
        self.progress_value.set(0)
        self.file_count.set("0 / 0 files")
        self._progress_buckets.clear()
        self.status.set("Checking application setup…")
        self._append(f"Starting: {source}")
        self.worker = threading.Thread(target=self._run, args=(source,), daemon=True)
        self.worker.start()

    def _run(self, source: Path):
        try:
            self.events.put(("status", "Loading configuration…"))
            log.info("GUI worker started for %s", source)
            settings = load_settings()
            if not settings.configured_for_sharepoint or not settings.output_parent_sp_path:
                raise RuntimeError("SharePoint settings are incomplete in env/.env")
            self.events.put(("status", "Connecting to Microsoft…"))
            log.info("Creating Microsoft Graph client")
            client = GraphClient(
                settings,
                auth_message=lambda message: self.events.put(("auth", message)),
                cancelled=self.cancel_event.is_set,
            )
            destination = SharePointDestination(client, settings)
            processor = Processor(
                settings,
                destination=destination,
                progress=lambda event: self.events.put(("progress", event)),
                cancel=self.cancel_event,
            )
            summary = processor.run(source)
            self.events.put(("done", summary))
        except Exception as exc:
            self.events.put(("error", exc))

    def _drain_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    event: ProgressEvent = payload  # type: ignore[assignment]
                    self.status.set(event.message)
                    if event.file_total:
                        if event.file_index == 0:
                            self.file_count.set(f"0 / {event.file_total} complete")
                        elif event.stage == "file_complete":
                            self.file_count.set(f"{event.file_index} / {event.file_total} complete")
                        else:
                            self.file_count.set(f"File {event.file_index} / {event.file_total}")
                    self.progress_value.set(_overall_percent(event))
                    if event.total:
                        percent = min(100, event.current / event.total * 100)
                        bucket = int(percent // 10)
                        key = f"{event.stage}:{event.file}"
                        if self._progress_buckets.get(key) != bucket:
                            self._progress_buckets[key] = bucket
                            self._append(f"[{event.stage}] {int(percent)}% {event.message}")
                    else:
                        self._append(f"[{event.stage}] {event.message}")
                elif kind == "auth":
                    url, code = _device_login_details(str(payload))
                    self.status.set("Microsoft sign-in is required. The login page has been opened in your browser.")
                    self._append(f"\nMICROSOFT SIGN-IN\n{payload}\n")
                    if code:
                        self.root.clipboard_clear()
                        self.root.clipboard_append(code)
                    webbrowser.open(url)
                    detail = f"The code {code} has been copied to your clipboard. Paste it into the browser." if code else str(payload)
                    messagebox.showinfo("Microsoft sign-in required", detail)
                elif kind == "status":
                    self.status.set(str(payload))
                    self._append(f"[setup] {payload}")
                elif kind == "done":
                    summary = payload
                    self.root_url = summary.root_url
                    self.status.set(f"Complete: {summary.uploaded} uploaded, {summary.skipped} already present, {summary.failed} failed.")
                    self.progress_value.set(100)
                    self.file_count.set(f"{summary.discovered} / {summary.discovered} complete")
                    self._set_idle()
                    if self.root_url:
                        self.open_button.configure(state="normal")
                    if summary.failed:
                        messagebox.showwarning("Completed with errors", f"Some files failed. See the activity panel and debug log:\n{self.log_file}")
                elif kind == "error":
                    self.status.set(f"Error: {payload}")
                    self._append(f"[error] {payload}")
                    self._set_idle()
                    self.root.bell()
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _set_idle(self):
        self.upload_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")

    def _open_root(self):
        if self.root_url:
            webbrowser.open(self.root_url)


def main():
    try:
        from tkinterdnd2 import TkinterDnD

        root = TkinterDnD.Tk()
    except (ImportError, tk.TclError):
        root = tk.Tk()
    UploaderWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()

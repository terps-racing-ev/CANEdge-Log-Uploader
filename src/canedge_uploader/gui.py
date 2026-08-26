from __future__ import annotations

import logging
import queue
import re
import threading
import webbrowser
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

from .config import load_settings
from .files import detect_removable_source
from .logging_setup import configure_logging
from .models import CloudDay, ProgressEvent, RunSummary, UploadPreview
from .pipeline import Processor
from .sharepoint import GraphClient, SharePointDestination

log = logging.getLogger(__name__)


def _device_login_details(message: str) -> tuple[str, str]:
    match = re.search(r"open the page (https://\S+) and enter the code ([A-Z0-9]+)", message, re.IGNORECASE)
    return (match.group(1), match.group(2)) if match else ("https://login.microsoft.com/device", "")


def _overall_percent(event: ProgressEvent) -> float:
    if event.stage == "complete":
        return 100.0
    if event.file_total <= 0 or event.file_index <= 0:
        return 0.0
    stage_fraction = {
        "file": 0.02,
        "cleanup": 0.02,
        "download": 0.18,
        "skip": 1.0,
        "error": 1.0,
        "save": 0.78,
        "saved": 0.82,
        "upload": 0.82,
        "file_complete": 1.0,
    }.get(event.stage, 0.0)
    if event.stage == "decode":
        inner = event.current / event.total if event.total else 0.0
        stage_fraction = 0.2 + 0.58 * min(1.0, inner)
    elif event.stage == "download" and event.total:
        inner = event.current / event.total
        stage_fraction = 0.02 + 0.18 * min(1.0, inner)
    elif event.stage == "upload" and event.total:
        inner = event.current / event.total
        stage_fraction = 0.82 + 0.18 * min(1.0, inner)
    completed_before = event.file_index - 1
    return min(100.0, (completed_before + stage_fraction) / event.file_total * 100.0)


class UploaderWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CANedge Log Uploader")
        self.root.geometry("860x720")
        self.root.minsize(760, 620)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.open_target = ""
        self._progress_buckets: dict[str, int] = {}
        self.log_file = configure_logging()
        self.settings_preview = load_settings()
        detected = detect_removable_source()
        self.mode = tk.StringVar(value="upload")
        self.sd_source = tk.StringVar(value=str(detected) if detected else "")
        self.sharepoint_path = tk.StringVar(value=self.settings_preview.output_parent_sp_path)
        self.local_output = tk.StringVar(value=str(Path("decoded_output")))
        self.local_inputs: list[Path] = []
        self.cloud_days: list[CloudDay] = []
        self.day_vars: list[tuple[CloudDay, tk.BooleanVar]] = []
        self.upload_preview: UploadPreview | None = None
        self.preview_source = ""
        self.status = tk.StringVar(value=self._initial_status(detected))
        self.file_count = tk.StringVar(value="0 / 0 files")
        self.progress_value = tk.DoubleVar(value=0)
        self._build()
        self._sync_mode()
        self.root.after(100, self._drain_events)

    def _initial_status(self, detected: Path | None) -> str:
        if detected:
            return f"Detected removable drive: {detected}"
        return "No removable drive detected. Choose the SD card root folder."

    def _build(self):
        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="CANedge Log Uploader", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Upload raw SD logs to SharePoint, then decode with bundled DBCs.").pack(anchor="w", pady=(2, 16))

        modes = ttk.Frame(outer)
        modes.pack(fill="x")
        for label, value in (
            ("Upload from SD", "upload"),
            ("Re-decode existing", "redecode"),
            ("Local decode", "local"),
        ):
            ttk.Radiobutton(modes, text=label, value=value, variable=self.mode, command=self._sync_mode).pack(
                side="left", padx=(0, 18)
            )

        self.upload_frame = ttk.LabelFrame(outer, text="Upload from SD", padding=14)
        self.upload_frame.pack(fill="x", pady=(14, 0))
        row = ttk.Frame(self.upload_frame)
        row.pack(fill="x")
        ttk.Label(row, text="SD card", width=14).pack(side="left")
        ttk.Entry(row, textvariable=self.sd_source).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Choose SD Card…", command=self._browse_sd).pack(side="left", padx=(8, 0))
        ttk.Label(
            self.upload_frame,
            text="Select the SD card itself. You do not need to open LOGS, CANEDGE_OUTPUT, or any logger subfolder.",
            foreground="#555",
            wraplength=760,
        ).pack(anchor="w", pady=(8, 0))
        self.preview_button = ttk.Button(self.upload_frame, text="Preview Upload", command=self._preview_upload)
        self.preview_button.pack(anchor="w", pady=(12, 0))

        self.redecode_frame = ttk.LabelFrame(outer, text="Re-decode Uploaded Logs", padding=14)
        row = ttk.Frame(self.redecode_frame)
        row.pack(fill="x")
        ttk.Label(row, text="Days", width=14).pack(side="left", anchor="n")
        self.days_canvas = tk.Canvas(row, height=150, highlightthickness=1, highlightbackground="#d0d0d0")
        self.days_frame = ttk.Frame(self.days_canvas)
        self.days_window = self.days_canvas.create_window((0, 0), window=self.days_frame, anchor="nw")
        self.days_canvas.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(row, orient="vertical", command=self.days_canvas.yview)
        self.days_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="left", fill="y")
        ttk.Button(row, text="Load Days", command=self._load_cloud_days).pack(side="left", padx=(8, 0), anchor="n")
        self.days_frame.bind("<Configure>", self._sync_days_scrollregion)
        self.days_canvas.bind("<Configure>", self._sync_days_width)
        ttk.Label(
            self.redecode_frame,
            text="Select one or more date folders. The app reads each date's Raw folder, clears decoded MF4s for that date, and writes fresh decoded outputs.",
            foreground="#555",
            wraplength=760,
        ).pack(anchor="w", pady=(8, 0))

        self.local_frame = ttk.LabelFrame(outer, text="Local Decode", padding=14)
        input_row = ttk.Frame(self.local_frame)
        input_row.pack(fill="x")
        ttk.Button(input_row, text="Choose MF4 Files…", command=self._choose_local_files).pack(side="left")
        ttk.Button(input_row, text="Choose Folder…", command=self._choose_local_folder).pack(side="left", padx=(8, 0))
        self.local_input_label = ttk.Label(input_row, text="No MF4 files selected", foreground="#555")
        self.local_input_label.pack(side="left", padx=(12, 0))
        output_row = ttk.Frame(self.local_frame)
        output_row.pack(fill="x", pady=(10, 0))
        ttk.Label(output_row, text="Output", width=14).pack(side="left")
        ttk.Entry(output_row, textvariable=self.local_output).pack(side="left", fill="x", expand=True)
        ttk.Button(output_row, text="Choose Output…", command=self._browse_local_output).pack(side="left", padx=(8, 0))

        self.preview_frame = ttk.LabelFrame(outer, text="Preview", padding=8)
        self.preview_frame.pack(fill="both", expand=True, pady=(14, 0))
        self.preview_text = tk.Text(self.preview_frame, height=8, state="disabled", wrap="word", font=("Consolas", 9))
        self.preview_text.pack(fill="both", expand=True)

        self.progress_header = ttk.Frame(outer)
        self.progress_header.pack(fill="x", pady=(14, 6))
        ttk.Label(self.progress_header, textvariable=self.status, wraplength=640).pack(side="left", anchor="w")
        ttk.Label(self.progress_header, textvariable=self.file_count, font=("Segoe UI", 10, "bold")).pack(side="right")
        self.progress = ttk.Progressbar(outer, variable=self.progress_value, maximum=100)
        self.progress.pack(fill="x")

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=12)
        self.start_button = ttk.Button(buttons, text="Start", command=self._start)
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(buttons, text="Cancel", command=self.cancel_event.set, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        self.open_button = ttk.Button(buttons, text="Open Destination", command=self._open_destination, state="disabled")
        self.open_button.pack(side="right")

        log_frame = ttk.LabelFrame(outer, text="Activity", padding=8)
        log_frame.pack(fill="both", expand=True)
        self.activity = tk.Text(log_frame, height=8, state="disabled", wrap="word", font=("Consolas", 9))
        activity_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.activity.yview)
        self.activity.configure(yscrollcommand=activity_scroll.set)
        self.activity.pack(side="left", fill="both", expand=True)
        activity_scroll.pack(side="right", fill="y")
        ttk.Label(outer, text=f"Debug log: {self.log_file}", foreground="#666").pack(anchor="w", pady=(8, 0))

    def _sync_mode(self):
        self.upload_frame.pack_forget()
        self.redecode_frame.pack_forget()
        self.local_frame.pack_forget()
        self.preview_frame.pack_forget()
        if self.mode.get() == "upload":
            self.upload_frame.pack(fill="x", pady=(14, 0), before=self.progress_header)
            self.preview_frame.pack(fill="both", expand=True, pady=(14, 0), before=self.progress_header)
            self.start_button.configure(text="Upload", state="normal" if self.upload_preview else "disabled")
        elif self.mode.get() == "redecode":
            self.redecode_frame.pack(fill="both", expand=True, pady=(14, 0), before=self.progress_header)
            self.start_button.configure(text="Re-decode", state="normal")
        else:
            self.local_frame.pack(fill="x", pady=(14, 0), before=self.progress_header)
            self.start_button.configure(text="Decode Locally", state="normal")

    def _sync_days_scrollregion(self, _event=None):
        self.days_canvas.configure(scrollregion=self.days_canvas.bbox("all"))

    def _sync_days_width(self, event):
        self.days_canvas.itemconfigure(self.days_window, width=event.width)

    def _browse_sd(self):
        selected = filedialog.askdirectory(title="Select the SD card root folder")
        if selected:
            self.sd_source.set(selected)
            self.upload_preview = None
            self.preview_source = ""
            self._set_preview("Preview required. Select the SD card root, then click Preview Upload.")
            self._sync_mode()

    def _browse_local_output(self):
        selected = filedialog.askdirectory(title="Select local output folder")
        if selected:
            self.local_output.set(selected)

    def _choose_local_files(self):
        selected = filedialog.askopenfilenames(title="Select MF4 files", filetypes=[("MF4 files", "*.mf4 *.MF4")])
        if selected:
            self.local_inputs = [Path(path) for path in selected]
            self.local_input_label.configure(text=f"{len(self.local_inputs)} files selected")

    def _choose_local_folder(self):
        selected = filedialog.askdirectory(title="Select folder containing MF4 files")
        if selected:
            self.local_inputs = [Path(selected)]
            self.local_input_label.configure(text=f"Folder: {selected}")

    def _preview_upload(self):
        source = Path(self.sd_source.get()).expanduser()
        if not source.is_dir():
            messagebox.showerror("SD card required", "Select the SD card root folder first.")
            return
        self._start_worker("preview", self._run_preview_upload, source)

    def _load_cloud_days(self):
        self._start_worker("load_days", self._run_load_cloud_days)

    def _start(self):
        mode = self.mode.get()
        if mode == "upload":
            source = Path(self.sd_source.get()).expanduser()
            if not self.upload_preview or self.preview_source != str(source.resolve()):
                messagebox.showerror("Preview required", "Preview the SD card before uploading.")
                return
            self._start_worker("upload", self._run_upload, source, self.upload_preview)
        elif mode == "redecode":
            selected = [day.name for day, selected_var in self.day_vars if selected_var.get()]
            if not selected:
                messagebox.showerror("Days required", "Load and check one or more days.")
                return
            self._start_worker("redecode", self._run_redecode, selected)
        else:
            if not self.local_inputs:
                messagebox.showerror("MF4 input required", "Choose one or more MF4 files, or a folder containing MF4 files.")
                return
            if not self.local_output.get().strip():
                messagebox.showerror("Output required", "Choose a local output folder.")
                return
            self._start_worker("local", self._run_local_decode, list(self.local_inputs), Path(self.local_output.get()))

    def _start_worker(self, label: str, target, *args):
        if self.worker and self.worker.is_alive():
            return
        self.cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.preview_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.open_button.configure(state="disabled")
        self.open_target = ""
        self.progress_value.set(0)
        self.file_count.set("0 / 0 files")
        self._progress_buckets.clear()
        self.status.set("Working...")
        self._append(f"Starting {label}")
        self.worker = threading.Thread(target=target, args=args, daemon=True)
        self.worker.start()

    def _destination(self, settings):
        if not settings.configured_for_sharepoint or not settings.output_parent_sp_path:
            raise RuntimeError("SharePoint settings are incomplete in env/.env")
        client = GraphClient(settings, auth_message=lambda message: self.events.put(("auth", message)), cancelled=self.cancel_event.is_set)
        return SharePointDestination(client, settings)

    def _run_preview_upload(self, source: Path):
        try:
            settings = replace(load_settings(), output_parent_sp_path=self.sharepoint_path.get().strip())
            processor = Processor(settings, destination=self._destination(settings), progress=lambda event: self.events.put(("progress", event)), cancel=self.cancel_event)
            preview = processor.preview_upload_from_sd(source)
            self.events.put(("preview", preview))
        except Exception as exc:
            self.events.put(("error", exc))

    def _run_upload(self, source: Path, preview: UploadPreview):
        try:
            settings = replace(load_settings(), output_parent_sp_path=self.sharepoint_path.get().strip())
            processor = Processor(settings, destination=self._destination(settings), progress=lambda event: self.events.put(("progress", event)), cancel=self.cancel_event)
            summary = processor.upload_from_sd(source, preview)
            self.events.put(("done", summary))
        except Exception as exc:
            self.events.put(("error", exc))

    def _run_load_cloud_days(self):
        try:
            settings = replace(load_settings(), output_parent_sp_path=self.sharepoint_path.get().strip())
            processor = Processor(settings, destination=self._destination(settings), progress=lambda event: self.events.put(("progress", event)), cancel=self.cancel_event)
            self.events.put(("cloud_days", processor.list_cloud_days()))
        except Exception as exc:
            self.events.put(("error", exc))

    def _run_redecode(self, days: list[str]):
        try:
            settings = replace(load_settings(), output_parent_sp_path=self.sharepoint_path.get().strip())
            processor = Processor(settings, destination=self._destination(settings), progress=lambda event: self.events.put(("progress", event)), cancel=self.cancel_event)
            summary = processor.redecode_cloud_days(days)
            self.events.put(("done", summary))
        except Exception as exc:
            self.events.put(("error", exc))

    def _run_local_decode(self, inputs: list[Path], output_dir: Path):
        try:
            settings = load_settings()
            processor = Processor(settings, progress=lambda event: self.events.put(("progress", event)), cancel=self.cancel_event)
            summary = processor.decode_local(inputs, output_dir)
            self.events.put(("local_output", str(output_dir.expanduser().resolve())))
            self.events.put(("done", summary))
        except Exception as exc:
            self.events.put(("error", exc))

    def _drain_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self._handle_progress(payload)  # type: ignore[arg-type]
                elif kind == "preview":
                    self._handle_preview(payload)  # type: ignore[arg-type]
                elif kind == "cloud_days":
                    self._handle_cloud_days(payload)  # type: ignore[arg-type]
                elif kind == "auth":
                    self._handle_auth(str(payload))
                elif kind == "done":
                    self._handle_done(payload)  # type: ignore[arg-type]
                elif kind == "local_output":
                    if payload:
                        self.open_target = Path(str(payload)).as_uri()
                elif kind == "error":
                    self.status.set(f"Error: {payload}")
                    self._append(f"[error] {payload}")
                    self._set_idle()
                    self.root.bell()
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _handle_progress(self, event: ProgressEvent):
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

    def _handle_preview(self, preview: UploadPreview):
        self.upload_preview = preview
        self.preview_source = str(preview.source_root.resolve())
        lines = [
            f"{len(preview.items)} MF4 files found on {preview.source_root}",
            f"{preview.upload_count} raw files will be uploaded; {len(preview.items) - preview.upload_count} raw files are already in SharePoint.",
            "Decoded outputs will be generated during upload from the same raw logs.",
            "",
        ]
        for item in preview.items[:200]:
            status = "raw already uploaded" if item.raw_exists else "raw will upload"
            lines.append(f"{item.calendar_date}  {item.raw_filename}  [{status}]")
        if len(preview.items) > 200:
            lines.append(f"... plus {len(preview.items) - 200} more")
        self._set_preview("\n".join(lines))
        self.status.set("Preview ready. Review the list, then click Upload.")
        self._set_idle()

    def _handle_cloud_days(self, days: list[CloudDay]):
        self.cloud_days = days
        self.day_vars = []
        for child in self.days_frame.winfo_children():
            child.destroy()
        for day in days:
            selected = tk.BooleanVar(value=False)
            self.day_vars.append((day, selected))
            ttk.Checkbutton(self.days_frame, text=f"{day.name} ({day.raw_count} raw)", variable=selected).pack(
                anchor="w", fill="x", padx=6, pady=2
            )
        self.status.set(f"{len(days)} days loaded. Check one or more days to re-decode.")
        self._set_idle()

    def _handle_auth(self, message: str):
        url, code = _device_login_details(message)
        self.status.set("Microsoft sign-in is required. The login page has been opened in your browser.")
        self._append(f"\nMICROSOFT SIGN-IN\n{message}\n")
        if code:
            self.root.clipboard_clear()
            self.root.clipboard_append(code)
        webbrowser.open(url)
        detail = f"The code {code} has been copied to your clipboard. Paste it into the browser." if code else message
        messagebox.showinfo("Microsoft sign-in required", detail)

    def _handle_done(self, summary: RunSummary):
        if summary.root_url:
            self.open_target = summary.root_url
            self.status.set(f"Complete: {summary.uploaded} uploaded, {summary.skipped} skipped, {summary.failed} failed.")
        else:
            self.status.set(f"Complete: {summary.saved} saved locally, {summary.failed} failed.")
        self.progress_value.set(100)
        self.file_count.set(f"{summary.discovered} / {summary.discovered} complete")
        self._set_idle()
        if self.open_target:
            self.open_button.configure(state="normal")
        if summary.failed:
            messagebox.showwarning("Completed with errors", f"Some files failed. See the activity panel and debug log:\n{self.log_file}")

    def _set_idle(self):
        self.preview_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self._sync_mode()

    def _append(self, text: str):
        self.activity.configure(state="normal")
        self.activity.insert("end", text.rstrip() + "\n")
        self.activity.see("end")
        self.activity.configure(state="disabled")

    def _set_preview(self, text: str):
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("end", text.rstrip() + "\n")
        self.preview_text.configure(state="disabled")

    def _open_destination(self):
        if self.open_target:
            webbrowser.open(self.open_target)


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

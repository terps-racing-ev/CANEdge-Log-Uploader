# CANedge SharePoint Uploader

This Windows app is built around the normal track-day flow: put the logger SD card in the laptop, preview the raw logs, then upload raw and decoded MF4 files to SharePoint.

```text
[OUTPUT_PARENT_SP_PATH]/YYYY-MM-DD/[decoded filename].mf4
[OUTPUT_PARENT_SP_PATH]/YYYY-MM-DD/Raw/[raw filename].mf4
```

Raw files are the cloud source of truth. Re-decode mode works from those uploaded raw files, clears decoded MF4s for the selected date folder, and writes fresh decoded outputs so a date does not mix stale and current decodes.

## Install and use the GUI

Python 3.12 is recommended. Python 3.10-3.13 are supported. Python 3.14 is intentionally rejected because asammdf's required `zstd` dependency does not currently publish a Windows 3.14 wheel and otherwise requires Visual C++ build tools.

For the simplest Windows setup, double-click `setup.bat` once, then double-click `start_gui.bat` whenever you want to upload logs.

The equivalent developer commands are:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python run_gui.py
```

The app reads `env` first, then `.env`. DBCs default to the repository's `dbc/` folder.

## Modes

**Upload from SD**

This is the primary path. The app auto-detects a removable drive when it can. If it cannot, click **Choose SD Card...** and select the SD card root. You do not need to open `LOGS`, `CANEDGE_OUTPUT`, or any logger subfolder.

Click **Preview Upload** first. The preview shows the raw MF4 files found on the card and whether each raw file already exists in SharePoint. After review, click **Upload**. Decoded outputs are generated from the same raw logs during processing.

**Re-decode existing**

Use this when DBCs or decode rules changed after raw logs were already uploaded. Click **Load Days**, check one or more date folders, then click **Re-decode**. The app downloads each selected date's `Raw` MF4 files, deletes decoded MF4s directly inside that date folder, decodes again, and uploads the replacements.

**Local decode**

This is the dev path. Choose one or more MF4 files, or a folder containing MF4 files, choose an output folder, then click **Decode Locally**. Nothing is uploaded.

## CLI

Preview an SD card or folder before uploading:

```powershell
canedge-uploader upload E:\ --preview
```

Upload from an SD card or folder:

```powershell
canedge-uploader upload E:\
```

Re-decode uploaded raw logs for one or more days:

```powershell
canedge-uploader redecode-cloud 2026-08-03 2026-08-04
```

Decode local MF4 files or folders without SharePoint:

```powershell
canedge-uploader process CANEDGE_OUTPUT\LOGS\00000489\00000001.MF4 --output decoded_output
canedge-uploader process CANEDGE_OUTPUT\LOGS\00000489 --output decoded_output
```

Decoded output defaults to all CAN buses. Set `DECODE_CAN_BUS` to a bus number such as `1` or `2` if mirrored traffic should be decoded from only one bus.

The rotating debug log lives at `%LOCALAPPDATA%\CANedgeUploader\logs\canedge-uploader.log`. A Microsoft token cache is stored beside it and is excluded from the repository.

## Developer extension point

All project-specific signal naming/splitting logic is isolated in [`src/canedge_uploader/rules.py`](src/canedge_uploader/rules.py). See [`docs/ADDING_RULES.md`](docs/ADDING_RULES.md) for tested patterns.

## SharePoint behavior

Authentication uses the same MSAL device-code and `Sites.Selected` approach as the team's ECE Order Automation project. Files up to 4 MiB use Graph's simple upload; larger MF4s use resumable chunks. Graph throttling and transient server failures are retried with backoff.

The configured SharePoint root folder must already exist. Calendar-day subfolders and their `Raw` subfolders are created automatically using the recording's Eastern date.

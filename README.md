# CANedge SharePoint Uploader

This local Windows app finds every raw CANedge `.MF4` below a selected folder, decodes CAN signals with the DBC files bundled in `dbc/`, converts the recording header from UTC to DST-aware US Eastern time, gives each result a deterministic name, and uploads it to:

```text
[OUTPUT_PARENT_SP_PATH]/YYYY-MM-DD/[deterministic filename].mf4
```

Existing filenames are checked before decoding whenever the active rules do not require signals. Re-running against the CANedge's complete history therefore skips work already in SharePoint.

## Install and use the GUI

Python 3.12 is recommended. Python 3.10–3.13 are supported. Python 3.14 is intentionally rejected because asammdf's required `zstd` dependency does not currently publish a Windows 3.14 wheel and otherwise requires Visual C++ build tools.

For the simplest Windows setup, double-click `setup.bat` once, then double-click `start_gui.bat` whenever you want to upload logs.

The equivalent developer commands are:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python run_gui.py
```

Then:

1. Drop or browse to the `CANEDGE_OUTPUT` folder.
2. Choose **SharePoint** or **Local folder** as the destination.
3. Edit the SharePoint path or local output folder if needed.
4. Click **Start**.
5. On the first SharePoint run, the app opens Microsoft device sign-in and copies the displayed code to the clipboard. Later runs reuse the local token cache. Login can be cancelled without closing the app.
6. Watch the per-file decode/upload progress.
7. Click **Open Destination** when processing completes.

The app reads `env` first (the file already used by this project), then `.env`. DBCs default to the repository's `dbc/` folder, so users do not select them.

On Windows, Microsoft Graph connections default to IPv4 because partially configured IPv6 networks can otherwise stall before login. Set `CANEDGE_FORCE_IPV4=0` only for an IPv6-only network.

Decoded output defaults to CAN bus 1 only so mirrored messages recorded on multiple buses are not duplicated. Set `DECODE_CAN_BUS=0` to decode all buses, or set another bus number such as `2` to keep that bus instead.

## CLI

Upload only new files:

```powershell
canedge-uploader upload CANEDGE_OUTPUT
```

Decode locally without touching SharePoint:

```powershell
canedge-uploader process CANEDGE_OUTPUT --output decoded_output
```

Force regeneration/upload (normally unnecessary):

```powershell
canedge-uploader upload CANEDGE_OUTPUT --force
```

The rotating debug log lives at `%LOCALAPPDATA%\CANedgeUploader\logs\canedge-uploader.log`. A Microsoft token cache is stored beside it and is excluded from the repository.

## Deterministic output and duplicate behavior

Names contain optional rule designators followed by the Eastern recording start. For example:

```text
STATIC_CHARGING_2026-08-03_19-42-10.mf4
```

CANedge recording timestamps are treated as unique, making repeated runs idempotent without hashes or internal logger IDs in the visible name. A split result adds `part-01`, `part-02`, and so on before the timestamp. SharePoint is checked by exact deterministic name, never by the ambiguous source name `00000001.MF4`.

## Developer extension point

All project-specific signal naming/splitting logic is isolated in [`src/canedge_uploader/rules.py`](src/canedge_uploader/rules.py). The upload, GUI, date-folder, hashing, and decoding code should not need edits. See [`docs/ADDING_RULES.md`](docs/ADDING_RULES.md) for tested patterns.

## SharePoint behavior

Authentication uses the same MSAL device-code and `Sites.Selected` approach as the team's ECE Order Automation project. Files up to 4 MiB use Graph's simple upload; larger decoded MF4s use resumable 10 MiB chunks. Graph throttling and transient server failures are retried with backoff.

The configured SharePoint root folder must already exist. Calendar-day subfolders are created automatically using the recording's Eastern date.

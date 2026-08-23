from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import dotenv_values
except ImportError:  # Lets configuration/tests work before the first pip install.
    def dotenv_values(path):
        values: dict[str, str] = {}
        if not path:
            return values
        for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
        return values


@dataclass(frozen=True)
class Settings:
    client_id: str
    tenant_id: str
    site_hostname: str
    site_path: str
    output_parent_sp_path: str
    dbc_dir: Path
    timezone: str = "America/New_York"
    decode_can_bus: int = 1
    upload_chunk_mib: int = 10

    @property
    def configured_for_sharepoint(self) -> bool:
        return bool(self.client_id and self.tenant_id and self.site_hostname and self.site_path)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def find_env_file(explicit: Path | None = None) -> Path | None:
    candidates = [explicit] if explicit else [project_root() / "env", project_root() / ".env"]
    return next((path for path in candidates if path and path.is_file()), None)


def load_settings(env_file: Path | None = None, dbc_dir: Path | None = None) -> Settings:
    resolved_env = find_env_file(env_file)
    file_values = dotenv_values(resolved_env) if resolved_env else {}

    def value(name: str, default: str = "") -> str:
        return os.getenv(name, str(file_values.get(name) or default)).strip()

    resolved_dbc = dbc_dir or Path(value("DBC_DIR", str(project_root() / "dbc")))
    return Settings(
        client_id=value("CLIENT_ID"),
        tenant_id=value("TENANT_ID"),
        site_hostname=value("SITE_HOSTNAME"),
        site_path=value("SITE_PATH"),
        output_parent_sp_path=value("OUTPUT_PARENT_SP_PATH"),
        dbc_dir=resolved_dbc.expanduser().resolve(),
        timezone=value("OUTPUT_TIMEZONE", "America/New_York"),
        decode_can_bus=int(value("DECODE_CAN_BUS", "1")),
        upload_chunk_mib=int(value("UPLOAD_CHUNK_MIB", "10")),
    )

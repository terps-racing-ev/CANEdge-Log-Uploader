from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from .config import Settings

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = ["Sites.Selected"]
SIMPLE_UPLOAD_LIMIT = 4 * 1024 * 1024
CHUNK_GRANULARITY = 320 * 1024

log = logging.getLogger(__name__)


class GraphError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _token_cache_path() -> Path:
    base = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "CANedgeUploader"
    base.mkdir(parents=True, exist_ok=True)
    return base / "msal.token_cache"


def _graph_path(path: str) -> str:
    normalized = "/" + path.strip("/")
    return quote(normalized, safe="/!$&'()*+,;=:@")


class GraphClient:
    """Microsoft Graph client with cached device login and retry handling."""

    def __init__(
        self,
        settings: Settings,
        auth_message: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ):
        try:
            import msal
            import requests
        except ImportError as exc:
            raise RuntimeError("SharePoint dependencies are missing. Run: pip install -e .") from exc

        force_ipv4 = os.getenv("CANEDGE_FORCE_IPV4")
        if force_ipv4 == "1" or (force_ipv4 is None and os.name == "nt"):
            # Default to IPv4 on Windows. Some university/corporate networks publish
            # IPv6 DNS answers without a usable route, which otherwise makes MSAL
            # appear to hang before the GUI receives its first progress event.
            import urllib3.util.connection

            urllib3.util.connection.HAS_IPV6 = False

        self._requests = requests
        self.settings = settings
        self.auth_message = auth_message or (lambda message: print(message, flush=True))
        self.cancelled = cancelled or (lambda: False)
        self.cache_path = _token_cache_path()
        self.cache = msal.SerializableTokenCache()
        if self.cache_path.exists():
            try:
                self.cache.deserialize(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                log.warning("Ignoring unreadable MSAL token cache", exc_info=True)
        self.app = msal.PublicClientApplication(
            client_id=settings.client_id,
            authority=f"https://login.microsoftonline.com/{settings.tenant_id}",
            token_cache=self.cache,
        )
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def login(self) -> None:
        result: dict[str, Any] | None = None
        accounts = self.app.get_accounts()
        if accounts:
            result = self.app.acquire_token_silent(SCOPES, account=accounts[0])
        if not result or "access_token" not in result:
            flow = self.app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                raise GraphError(f"Unable to start Microsoft sign-in: {flow}")
            message = flow.get("message", "Complete Microsoft device sign-in in your browser.")
            log.info("Microsoft sign-in required: %s", message)
            self.auth_message(message)
            watcher_done = threading.Event()

            def cancel_watcher():
                while not watcher_done.wait(0.2):
                    if self.cancelled():
                        flow["expires_at"] = 0
                        return

            watcher = threading.Thread(target=cancel_watcher, daemon=True)
            watcher.start()
            try:
                result = self.app.acquire_token_by_device_flow(flow)
            finally:
                watcher_done.set()
            if self.cancelled():
                raise GraphError("Microsoft sign-in was cancelled")
        if "access_token" not in result:
            raise GraphError(f"Microsoft sign-in failed: {result.get('error_description', result)}")
        self.session.headers["Authorization"] = f"Bearer {result['access_token']}"
        if self.cache.has_state_changed:
            self.cache_path.write_text(self.cache.serialize(), encoding="utf-8")

    def request(self, method: str, url: str, *, timeout: int = 90, **kwargs):
        last = None
        for attempt in range(6):
            try:
                response = self.session.request(method, url, timeout=timeout, **kwargs)
            except self._requests.RequestException as exc:
                if attempt == 5:
                    raise GraphError(f"Graph request failed: {exc}") from exc
                time.sleep(min(0.5 * 2**attempt, 8))
                continue
            last = response
            if response.status_code in (429, 500, 502, 503, 504):
                delay = float(response.headers.get("Retry-After", 0) or min(0.5 * 2**attempt, 8))
                time.sleep(delay)
                continue
            return response
        raise GraphError(f"Graph request failed after retries: {getattr(last, 'text', '')}")

    def json(self, method: str, url: str, **kwargs) -> Any:
        response = self.request(method, url, **kwargs)
        if not response.ok:
            raise GraphError(f"{method} {url} -> {response.status_code}: {response.text}", response.status_code)
        return response.json() if response.content else {}


class SharePointDestination:
    def __init__(self, client: GraphClient, settings: Settings):
        self.client = client
        self.settings = settings
        self.site_id = ""
        self.drive_id = ""
        self.root_item: dict[str, Any] = {}
        self._folder_cache: dict[str, dict[str, Any]] = {}
        chunk = settings.upload_chunk_mib * 1024 * 1024
        self.chunk_size = max(CHUNK_GRANULARITY, chunk - chunk % CHUNK_GRANULARITY)

    @property
    def root_url(self) -> str:
        return str(self.root_item.get("webUrl", ""))

    def prepare(self) -> None:
        self.client.login()
        site_path = self.settings.site_path.strip("/")
        if site_path.lower().startswith("sites/"):
            site_path = site_path[6:]
        site = self.client.json(
            "GET", f"{GRAPH_BASE}/sites/{self.settings.site_hostname}:/sites/{quote(site_path, safe='/')}"
        )
        self.site_id = site["id"]
        drive = self.client.json("GET", f"{GRAPH_BASE}/sites/{self.site_id}/drive")
        self.drive_id = drive["id"]
        self.root_item = self._item_by_path(self.settings.output_parent_sp_path)

    def _item_by_path(self, path: str) -> dict[str, Any]:
        return self.client.json("GET", f"{GRAPH_BASE}/drives/{self.drive_id}/root:{_graph_path(path)}")

    def _try_item_by_path(self, path: str) -> dict[str, Any] | None:
        try:
            return self._item_by_path(path)
        except GraphError as exc:
            if exc.status_code == 404:
                return None
            raise

    def ensure_date_folder(self, calendar_date: str, subfolder: str | None = None) -> dict[str, Any]:
        cache_key = f"{calendar_date}/{subfolder}" if subfolder else calendar_date
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]
        full_path = f"{self.settings.output_parent_sp_path.rstrip('/')}/{calendar_date}"
        existing = self._try_item_by_path(full_path)
        if existing:
            self._folder_cache[calendar_date] = existing
            folder = existing
        else:
            url = f"{GRAPH_BASE}/drives/{self.drive_id}/items/{self.root_item['id']}/children"
            payload = {"name": calendar_date, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}
            response = self.client.request("POST", url, json=payload)
            if response.status_code == 409:
                folder = self._item_by_path(full_path)
            elif response.ok:
                folder = response.json()
            else:
                raise GraphError(f"Create date folder failed: {response.status_code}: {response.text}", response.status_code)
            self._folder_cache[calendar_date] = folder

        if not subfolder:
            return folder

        child_path = f"{full_path.rstrip('/')}/{subfolder}"
        existing_child = self._try_item_by_path(child_path)
        if existing_child:
            self._folder_cache[cache_key] = existing_child
            return existing_child
        url = f"{GRAPH_BASE}/drives/{self.drive_id}/items/{folder['id']}/children"
        payload = {"name": subfolder, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}
        response = self.client.request("POST", url, json=payload)
        if response.status_code == 409:
            child = self._item_by_path(child_path)
        elif response.ok:
            child = response.json()
        else:
            raise GraphError(f"Create {subfolder} folder failed: {response.status_code}: {response.text}", response.status_code)
        self._folder_cache[cache_key] = child
        return child

    def names_in_date(self, calendar_date: str, subfolder: str | None = None) -> set[str]:
        full_path = f"{self.settings.output_parent_sp_path.rstrip('/')}/{calendar_date}"
        if subfolder:
            full_path = f"{full_path.rstrip('/')}/{subfolder}"
        folder = self._try_item_by_path(full_path)
        if folder is None:
            return set()
        return {str(item["name"]) for item in self._children(folder["id"], select="name")}

    def list_date_folders(self) -> list[str]:
        items = self._children(self.root_item["id"], select="name,folder")
        names = [str(item["name"]) for item in items if "folder" in item and self._looks_like_date_folder(str(item["name"]))]
        return sorted(names, reverse=True)

    def raw_items_in_date(self, calendar_date: str) -> list[dict[str, Any]]:
        return [
            item
            for item in self._items_in_date(calendar_date, subfolder="Raw")
            if str(item.get("name", "")).lower().endswith(".mf4")
        ]

    def delete_decoded_files(self, calendar_date: str) -> int:
        deleted = 0
        for item in self._items_in_date(calendar_date):
            if "file" not in item or not str(item.get("name", "")).lower().endswith(".mf4"):
                continue
            response = self.client.request("DELETE", f"{GRAPH_BASE}/drives/{self.drive_id}/items/{item['id']}")
            if not response.ok:
                raise GraphError(f"Delete decoded file failed: {response.status_code}: {response.text}", response.status_code)
            deleted += 1
        return deleted

    def download_item(
        self,
        item_id: str,
        destination: Path,
        progress: Callable[[int, int], None] | None = None,
    ) -> None:
        response = self.client.request(
            "GET",
            f"{GRAPH_BASE}/drives/{self.drive_id}/items/{item_id}/content",
            timeout=120,
            stream=True,
        )
        if not response.ok:
            raise GraphError(f"Download raw file failed: {response.status_code}: {response.text}", response.status_code)
        total = int(response.headers.get("Content-Length", "0") or 0)
        sent = 0
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as stream:
            for chunk in response.iter_content(chunk_size=self.chunk_size):
                if not chunk:
                    continue
                stream.write(chunk)
                sent += len(chunk)
                if progress:
                    progress(sent, total)
        if progress:
            progress(sent, total or sent)

    def _items_in_date(self, calendar_date: str, subfolder: str | None = None) -> list[dict[str, Any]]:
        full_path = f"{self.settings.output_parent_sp_path.rstrip('/')}/{calendar_date}"
        if subfolder:
            full_path = f"{full_path.rstrip('/')}/{subfolder}"
        folder = self._try_item_by_path(full_path)
        if folder is None:
            return []
        return self._children(folder["id"], select="id,name,file,folder,size")

    def _children(self, folder_id: str, select: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        url = f"{GRAPH_BASE}/drives/{self.drive_id}/items/{folder_id}/children?$select={select}&$top=999"
        while url:
            result = self.client.json("GET", url)
            items.extend(result.get("value", []))
            url = result.get("@odata.nextLink", "")
        return items

    def _looks_like_date_folder(self, name: str) -> bool:
        parts = name.split("-")
        return (
            len(parts) == 3
            and len(parts[0]) == 4
            and len(parts[1]) == 2
            and len(parts[2]) == 2
            and all(part.isdigit() for part in parts)
        )

    def upload_file(
        self,
        path: Path,
        calendar_date: str,
        progress: Callable[[int, int], None] | None = None,
        overwrite: bool = False,
        subfolder: str | None = None,
        upload_name: str | None = None,
    ) -> dict:
        folder = self.ensure_date_folder(calendar_date, subfolder)
        size = path.stat().st_size
        name = upload_name or path.name
        escaped = quote(name, safe="")
        if size <= SIMPLE_UPLOAD_LIMIT:
            url = f"{GRAPH_BASE}/drives/{self.drive_id}/items/{folder['id']}:/{escaped}:/content"
            # Bytes can be replayed safely if Graph returns a transient error.
            result = self.client.json(
                "PUT",
                url,
                data=path.read_bytes(),
                headers={"Content-Type": "application/octet-stream"},
            )
            if progress:
                progress(size, size)
            return result
        return self._upload_session(path, folder["id"], progress, overwrite, name)

    def _upload_session(
        self,
        path: Path,
        folder_id: str,
        progress: Callable[[int, int], None] | None,
        overwrite: bool,
        upload_name: str,
    ) -> dict:
        escaped = quote(upload_name, safe="")
        url = f"{GRAPH_BASE}/drives/{self.drive_id}/items/{folder_id}:/{escaped}:/createUploadSession"
        behavior = "replace" if overwrite else "fail"
        body = {"item": {"@microsoft.graph.conflictBehavior": behavior, "name": upload_name}}
        session = self.client.json("POST", url, json=body)
        upload_url = session["uploadUrl"]
        total = path.stat().st_size
        sent = 0
        with path.open("rb") as stream:
            while sent < total:
                chunk = stream.read(min(self.chunk_size, total - sent))
                end = sent + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {sent}-{end}/{total}",
                }
                response = self._requests_put_upload(upload_url, chunk, headers)
                sent = end + 1
                if progress:
                    progress(sent, total)
                if response.status_code in (200, 201):
                    return response.json()
        raise GraphError("Upload session ended without a completed drive item")

    def _requests_put_upload(self, upload_url: str, chunk: bytes, headers: dict[str, str]):
        # Upload URLs are pre-authenticated; Microsoft recommends omitting Authorization.
        last = None
        for attempt in range(6):
            try:
                response = self.client._requests.put(upload_url, data=chunk, headers=headers, timeout=120)
            except self.client._requests.RequestException as exc:
                if attempt == 5:
                    raise GraphError(f"Chunk upload failed: {exc}") from exc
                time.sleep(min(0.5 * 2**attempt, 8))
                continue
            last = response
            if response.status_code in (200, 201, 202):
                return response
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(float(response.headers.get("Retry-After", 0) or min(0.5 * 2**attempt, 8)))
                continue
            raise GraphError(f"Chunk upload failed: {response.status_code}: {response.text}", response.status_code)
        raise GraphError(f"Chunk upload failed after retries: {getattr(last, 'text', '')}")

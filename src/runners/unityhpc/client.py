"""HTTP-only portable campaign client; no controller source imports."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from http.client import RemoteDisconnected
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


class ControllerError(RuntimeError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"controller HTTP {status}: {message}")


class Client:
    def __init__(self, url: str, timeout: int, *, max_download_bytes: int | None):
        self.url = url.rstrip("/")
        self.timeout = timeout
        if max_download_bytes is not None and (
            type(max_download_bytes) is not int or max_download_bytes < 0
        ):
            raise ValueError("artifact download limit must be nonnegative bytes or null")
        self.max_download_bytes = max_download_bytes
        target = urlsplit(self.url)
        if target.scheme not in ("http", "https") or not target.netloc or target.username:
            raise ValueError("controller URL must be an HTTP origin without credentials")

    def json(self, method: str, route: str, body: dict | None = None):
        request = Request(
            self.url + route,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        # Planning is read-only despite using POST. Mutations are retried by the
        # caller only when their identity makes that safe.
        attempts = 8 if method == "GET" or route == "/api/v1/campaigns/plan" else 1
        for attempt in range(attempts):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except HTTPError as exc:
                if (exc.code != 429 and exc.code < 500) or attempt == attempts - 1:
                    raise ControllerError(
                        exc.code, exc.read(2000).decode(errors="replace")
                    ) from exc
            except (URLError, TimeoutError, RemoteDisconnected, ConnectionResetError):
                if attempt == attempts - 1:
                    raise
            time.sleep(min(30, 5 * (attempt + 1)))

    def download(self, receipt: dict, destination: Path) -> dict:
        relative = PurePosixPath(receipt["path"])
        if relative.is_absolute() or ".." in relative.parts or str(relative) == ".":
            raise ValueError("artifact path leaves destination")
        destination = destination.resolve()
        path = destination.joinpath(*relative.parts)
        for parent in (path, *path.parents):
            if parent == destination.parent:
                break
            if parent.is_symlink():
                raise ValueError("artifact destination contains a symlink")
        url = urljoin(self.url + "/", receipt["download_url"])
        target, origin = urlsplit(url), urlsplit(self.url)
        if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
            raise ValueError("artifact download URL leaves controller origin")
        expected_size = receipt["size_bytes"]
        if (
            type(expected_size) is not int
            or expected_size < 0
            or self.max_download_bytes is not None
            and expected_size > self.max_download_bytes
        ):
            raise ValueError("artifact size is outside the download limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(path.parent).free < expected_size:
            raise ValueError("insufficient local space for declared artifact")
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".download-")
        temporary = Path(name)
        try:
            digest, size = hashlib.sha256(), 0
            with os.fdopen(fd, "wb") as output, urlopen(url, timeout=self.timeout) as response:
                if urlsplit(response.url).netloc != origin.netloc:
                    raise ValueError("artifact redirect leaves controller origin")
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if size > expected_size:
                        raise ValueError("artifact exceeds declared size")
                    digest.update(block)
                    output.write(block)
            if size != expected_size or digest.hexdigest() != receipt["sha256"]:
                raise ValueError("artifact digest or size mismatch")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return {**receipt, "verified_path": str(path)}

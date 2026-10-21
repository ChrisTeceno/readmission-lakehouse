"""HTTP helpers for fetch steps that download files: retries, whole-file and ranged reads."""

import io
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx

# www.cms.gov answers 403 to clients without a browser User-Agent.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

RETRY_STATUSES = {429, 500, 502, 503, 504}


def make_client(timeout: float = 60.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": BROWSER_USER_AGENT},
    )


def request_with_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    attempts: int = 5,
    backoff: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """GET with exponential backoff on 429, 5xx and transport errors (timeouts included)."""
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            response = client.get(url, headers=headers)
        except httpx.TransportError:
            if last:
                raise
        else:
            if response.status_code not in RETRY_STATUSES or last:
                return response.raise_for_status()
        sleep(backoff * 2**attempt)
    raise AssertionError("unreachable")


def download_zip(client: httpx.Client, url: str, dest: Path, attempts: int = 3) -> Path:
    """Download a zip unless a valid copy is already there.

    data.cms.gov sometimes ends a 200 response early under parallel load, leaving a truncated
    zip, so the length is checked against Content-Length and the zip structure is verified.
    """
    if dest.exists() and zipfile.is_zipfile(dest):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(attempts):
        response = request_with_retry(client, url)
        expected = response.headers.get("Content-Length")
        complete = expected is None or int(expected) == len(response.content)
        if complete and zipfile.is_zipfile(io.BytesIO(response.content)):
            dest.write_bytes(response.content)
            return dest
        if attempt == attempts - 1:
            raise RuntimeError(f"{url}: incomplete download after {attempts} attempts")
    raise AssertionError("unreachable")


class HttpRangeFile(io.RawIOBase):
    """Seekable read-only view of a remote file through HTTP Range requests.

    zipfile only needs the central directory at the end of a zip and the bytes of one member,
    so peeking inside a 15 MB zip costs well under 1 MB.
    """

    def __init__(self, client: httpx.Client, url: str, size: int):
        self.client, self.url, self.size, self.pos = client, url, size, 0

    @classmethod
    def open(cls, client: httpx.Client, url: str) -> io.BufferedReader:
        response = request_with_retry(client, url, headers={"Range": "bytes=0-0"})
        if response.status_code != 206:
            raise RuntimeError(f"{url} ignored the Range header")
        size = int(response.headers["Content-Range"].rsplit("/", 1)[1])
        return io.BufferedReader(cls(client, url, size), buffer_size=256 * 1024)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        self.pos = max(0, base + offset)
        return self.pos

    def readinto(self, buffer) -> int:
        end = min(self.pos + len(buffer), self.size)
        if end <= self.pos:
            return 0
        response = request_with_retry(
            self.client, self.url, headers={"Range": f"bytes={self.pos}-{end - 1}"}
        )
        if response.status_code != 206:
            raise RuntimeError(f"{self.url} ignored the Range header")
        data = response.content
        buffer[: len(data)] = data
        self.pos += len(data)
        return len(data)

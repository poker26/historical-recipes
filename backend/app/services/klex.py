"""klex.ru herbalism-section crawler + downloader.

Pure I/O helpers used by the Temporal activities that mirror klex.ru/razdel/herb
into a dedicated MinIO bucket. No Temporal imports here so it stays unit-testable.

The site layout:
  listing  https://klex.ru/razdel/herb/   -> ~585 <a href='/<code>'>Title</a> links
  detail   https://klex.ru/<code>         -> download buttons that point at
           direct files on phantastike.com, e.g.
           https://www.phantastike.com/herb/<slug>/pdf/  (Content-Disposition pdf)
           .../zip/ , .../djvu/ , .../fb2/ ...

Everything is windows-1251 encoded, so pages are force-decoded as such.
"""

import os
import re
import tempfile

import httpx

from app.services import minio as minio_svc

BASE = "https://klex.ru"
LISTING = f"{BASE}/razdel/herb/"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# klex detail-page links: <a href='/2if6'>Title</a>  (skip "/" and "/favicon.ico").
RE_BOOK = re.compile(r"<a\s+href='(/[a-z0-9]{2,8})'>(.*?)</a>", re.I | re.S)
# phantastike direct-file links on a detail page; capture the format segment.
RE_FILE = re.compile(
    r"href='(https?://(?:www\.)?phantastike\.com/[^']+?/(pdf|zip|djvu|fb2|doc|docx|txt)/)'",
    re.I,
)
RE_TAGS = re.compile(r"<[^>]+>")

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "zip": "application/zip",
    "djvu": "image/vnd.djvu",
    "fb2": "application/x-fictionbook+xml",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
}
DEFAULT_FORMATS = "pdf,djvu,zip,fb2,docx,doc,txt"

_TIMEOUT = httpx.Timeout(60.0, read=600.0)


def _proxy() -> str | None:
    """Optional forward-proxy for ALL klex/phantastike traffic.

    klex.ru IP-throttles bursts of requests (starts returning 503 to the offending
    egress IP). Routing everything — listing, detail pages AND the file downloads —
    through the fleet trusttunnel proxy (a clean egress IP on server 6) sidesteps
    that block. Set KLEX_PROXY to the proxy URL, e.g.
    https://user:pass@tt.begemot26.ru:4431.
    """
    return os.environ.get("KLEX_PROXY") or None


def _decode(content: bytes) -> str:
    return content.decode("windows-1251", errors="replace")


def parse_listing(html: str) -> list[tuple[str, str]]:
    """Return [(code, title)] for every book link on the listing page."""
    out, seen = [], set()
    for href, raw_title in RE_BOOK.findall(html):
        code = href.strip("/")
        if not code or code == "favicon.ico" or code in seen:
            continue
        title = RE_TAGS.sub("", raw_title).strip()
        if not title:  # logo / chrome anchors carry no title
            continue
        seen.add(code)
        out.append((code, title))
    return out


def parse_detail(html: str) -> dict[str, tuple[str, str]]:
    """Return {fmt: (url, slug)} for the download links on a detail page."""
    found: dict[str, tuple[str, str]] = {}
    for url, fmt in RE_FILE.findall(html):
        fmt = fmt.lower()
        slug = url.rstrip("/").rsplit("/", 2)[-2]
        found.setdefault(fmt, (url, slug))
    return found


async def list_books() -> list[list[str]]:
    """Fetch the listing and return [[code, title], ...] (list, JSON-friendly)."""
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=_TIMEOUT,
                                 follow_redirects=True, proxy=_proxy()) as c:
        r = await c.get(LISTING)
        r.raise_for_status()
        return [[code, title] for code, title in parse_listing(_decode(r.content))]


def _ensure_bucket(bucket: str) -> None:
    client = minio_svc.get_client()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def _object_exists(bucket: str, key: str) -> bool:
    from minio.error import S3Error

    client = minio_svc.get_client()
    try:
        client.stat_object(bucket, key)
        return True
    except S3Error:
        return False


async def download_book(
    code: str,
    bucket: str,
    prefix: str = "",
    formats: str = DEFAULT_FORMATS,
    heartbeat=None,
) -> dict:
    """Mirror one book into ``bucket``. Idempotent: returns 'skip' if the object
    already exists, so re-running the sweep resumes cheaply.

    ``heartbeat`` is an optional callable(str) used to keep a Temporal activity
    alive during the (multi-MB) streamed download.
    """
    def hb(msg: str):
        if heartbeat:
            heartbeat(msg)

    prefs = [f.strip().lower() for f in formats.split(",") if f.strip()]
    _ensure_bucket(bucket)

    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=_TIMEOUT,
                                 follow_redirects=True, proxy=_proxy()) as c:
        r = await c.get(f"{BASE}/{code}")
        r.raise_for_status()
        files = parse_detail(_decode(r.content))
        if not files:
            return {"code": code, "status": "no_file"}

        fmt = next((f for f in prefs if f in files), None) or sorted(files)[0]
        url, slug = files[fmt]
        key = f"{prefix}{slug}.{fmt}"

        if _object_exists(bucket, key):
            return {"code": code, "status": "skip", "key": key, "fmt": fmt}

        # Stream the remote file to a temp file (books are tens of MB), then upload.
        hb(f"download {code}: {key}")
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = tmp.name
                downloaded = 0
                async with c.stream("GET", url, headers={"Referer": BASE}) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 512):
                        tmp.write(chunk)
                        downloaded += len(chunk)
                        if downloaded % (1024 * 1024 * 8) < (1024 * 512):
                            hb(f"download {code}: {downloaded/1e6:.0f}MB -> {key}")
            size = os.path.getsize(tmp_path)
            hb(f"upload {code}: {size/1e6:.1f}MB -> {key}")
            minio_svc.get_client().fput_object(
                bucket, key, tmp_path,
                content_type=CONTENT_TYPES.get(fmt, "application/octet-stream"),
            )
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return {"code": code, "status": "ok", "key": key, "fmt": fmt, "size": size}

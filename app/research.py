"""Turn user material (notes, files, URLs) into plain text context for the model."""
from __future__ import annotations

import ipaddress
import re
import socket
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urlparse

from .errors import ServiceError

MAX_FILE_BYTES = 1_000_000
MAX_FETCH_BYTES = 500_000
MAX_CONTEXT_CHARS = 6000

STOPWORDS = set("""a about above after again all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having he her here
hers him his how i if in into is it its just me more most my no nor not now of off on once only or other our out over
own same she should so some such than that the their them then there these they this those through to too under until
up very was we were what when where which while who whom why will with would you your use using used make like get one
two""".split())


def strip_front_matter(text: str) -> str:
    if text.startswith("---"):
        m = re.match(r"^---\s*\n.*?\n---\s*\n", text, flags=re.S)
        if m:
            return text[m.end():]
    return text


def read_text_file(name: str, data: bytes) -> dict:
    if len(data) > MAX_FILE_BYTES:
        raise ServiceError(f"'{name}' is larger than {MAX_FILE_BYTES // 1000} KB", 413)
    if not name.lower().endswith((".md", ".markdown", ".txt")):
        raise ServiceError("Only .md, .markdown and .txt files are supported", 415)
    if b"\x00" in data[:2048]:
        raise ServiceError(f"'{name}' looks like a binary file", 415)
    text = strip_front_matter(data.decode("utf-8", errors="replace")).strip()
    if not text:
        raise ServiceError(f"'{name}' is empty", 400)
    m = re.search(r"^#\s+(.+)$", text, flags=re.M)
    return {"title": (m.group(1).strip() if m else name), "content": text}


def extract_keywords(text: str, n: int = 8) -> list[str]:
    counts: dict[str, int] = {}
    for w in re.findall(r"[a-zA-Z][a-zA-Z0-9\-]{3,}", text.lower()):
        if w not in STOPWORDS:
            counts[w] = counts.get(w, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in ("p", "div", "br", "li", "h1", "h2", "h3", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip and data.strip():
            self.parts.append(data.strip() + " ")


def _check_public_host(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ServiceError("Only http/https URLs are allowed", 400)
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise ServiceError(f"Cannot resolve host '{parsed.hostname}'", 400) from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ServiceError("URLs that point to local or private addresses are blocked", 400)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_public_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_url(url: str, timeout: int = 15) -> dict:
    """User-triggered page download. Some sites block bots or need JavaScript; failures are expected."""
    _check_public_host(url)
    opener = urllib.request.build_opener(_SafeRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "SocialGrowthAgent/1.0 (local research)"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "html" not in ctype and "text" not in ctype:
                raise ServiceError(f"Unsupported content type: {ctype or 'unknown'}", 415)
            raw = resp.read(MAX_FETCH_BYTES)
            charset = resp.headers.get_content_charset() or "utf-8"
    except ServiceError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ServiceError(f"Could not download {url}: {exc}", 502) from exc
    parser = _TextExtractor()
    parser.feed(raw.decode(charset, errors="replace"))
    text = re.sub(r"\n\s*\n+", "\n", "".join(parser.parts)).strip()
    if len(text) < 50:
        raise ServiceError("The page had almost no readable text (it may need JavaScript or a login)", 422)
    return {"title": parser.title.strip() or url, "content": text, "url": url}


def build_context(notes: str, sources: list[dict], limit: int = MAX_CONTEXT_CHARS) -> str:
    chunks = []
    if notes.strip():
        chunks.append("[User notes]\n" + notes.strip())
    for i, s in enumerate(sources, 1):
        label = s.get("title") or f"Source {i}"
        if s.get("url"):
            label += f" ({s['url']})"
        chunks.append(f"[Source {i}: {label}]\n{(s.get('content') or '').strip()}")
    text = "\n\n".join(chunks)
    return text[:limit] + ("\n[...truncated]" if len(text) > limit else "")

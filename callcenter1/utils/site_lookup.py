"""Şirketin kendi sitesinden, sorulan konuyla ilgili kısa metin çıkarır."""

import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from urllib.parse import quote_plus, urljoin, urlparse

import httpx

from utils.prompt_builder import _fold

_STOP = {
    "bir",
    "bu",
    "icin",
    "ile",
    "veya",
    "icin",
    "hakkinda",
    "urun",
    "bilgi",
    "fiyat",
    "istiyorum",
    "nedir",
    "nasil",
    "var",
    "yok",
    "mi",
}
_JSONLD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_MAX_BYTES = 400_000
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AhmetAsistan/1.0)",
    "Accept": "text/html,application/xhtml+xml",
}


def _tokens(query: str) -> list[str]:
    words = []
    for word in _fold(query).split():
        if len(word) < 3 or word in _STOP:
            continue
        words.append(word)
        if len(words) == 8:
            break
    return words


def _public_host(host: str) -> bool:
    if not host or host.lower() in {"localhost", "localhost.localdomain"}:
        return False
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return False
    if not addresses:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


def _same_site(origin: str, target: str) -> bool:
    base = urlparse(origin)
    other = urlparse(target)
    if other.scheme not in {"http", "https"}:
        return False
    return (other.hostname or "").lower() == (base.hostname or "").lower()


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._skip = 0
        self._anchor: list[str] | None = None
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self._href = href
            self._anchor = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        if tag == "a" and self._anchor is not None:
            self.links.append((self._href, " ".join(self._anchor)))
            self._anchor = None
        if tag in {"p", "div", "li", "h1", "h2", "h3", "br", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = " ".join(data.split())
        if not text:
            return
        self.parts.append(text)
        if self._anchor is not None:
            self._anchor.append(text)


def _walk_products(node, found: list[dict]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_products(item, found)
        return
    if not isinstance(node, dict):
        return
    kind = str(node.get("@type") or "")
    if "Product" in kind and node.get("name"):
        offer = node.get("offers") or {}
        if isinstance(offer, list):
            offer = offer[0] if offer else {}
        if not isinstance(offer, dict):
            offer = {}
        brand = node.get("brand") or ""
        if isinstance(brand, dict):
            brand = brand.get("name") or ""
        found.append(
            {
                "name": str(node.get("name") or ""),
                "description": str(node.get("description") or ""),
                "brand": str(brand or ""),
                "price": str(offer.get("price") or ""),
                "currency": str(offer.get("priceCurrency") or ""),
            }
        )
    for value in node.values():
        if isinstance(value, (dict, list)):
            _walk_products(value, found)


def _products(html: str) -> list[dict]:
    found: list[dict] = []
    for raw in _JSONLD.findall(html):
        try:
            _walk_products(json.loads(raw), found)
        except json.JSONDecodeError:
            continue
    return found


def _product_lines(products: list[dict], tokens: list[str]) -> list[str]:
    lines = []
    for product in products:
        blob = _fold(" ".join(product.values()))
        if tokens and not any(token in blob for token in tokens):
            continue
        bits = [product["name"]]
        if product["brand"]:
            bits.append(product["brand"])
        if product["description"]:
            bits.append(product["description"][:280])
        if product["price"]:
            bits.append(f"fiyat {product['price']} {product['currency']}".strip())
        lines.append(". ".join(bit for bit in bits if bit))
        if len(lines) == 3:
            break
    return lines


def _sentences(text: str, tokens: list[str]) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+|\n+", text)
    picked = []
    for chunk in chunks:
        sentence = " ".join(chunk.split())
        if len(sentence) < 40:
            continue
        folded = _fold(sentence)
        if tokens and not any(token in folded for token in tokens):
            continue
        picked.append(sentence[:320])
        if len(picked) == 4:
            break
    return picked


def read_page(html: str, page_url: str, query: str) -> tuple[str, list[str]]:
    """Sayfadaki ilgili metni ve aynı sitedeki aday linkleri döndürür."""
    parser = _Page()
    parser.feed(html)
    tokens = _tokens(query)
    lines = _product_lines(_products(html), tokens)
    lines.extend(_sentences(" ".join(parser.parts), tokens))
    text = "\n".join(lines)[:1400]
    links = []
    for href, label in parser.links:
        absolute = urljoin(page_url, href)
        if not _same_site(page_url, absolute):
            continue
        folded = _fold(f"{label} {absolute}")
        if tokens and not any(token in folded for token in tokens):
            continue
        links.append(absolute.split("#")[0])
        if len(links) == 4:
            break
    return text, links


def _fetch(client: httpx.Client, url: str) -> str:
    with client.stream("GET", url) as response:
        if response.status_code >= 400:
            return ""
        kind = response.headers.get("content-type", "")
        if "html" not in kind and "json" not in kind and "text" not in kind:
            return ""
        chunks = []
        size = 0
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= _MAX_BYTES:
                break
        return b"".join(chunks).decode("utf-8", "ignore")


def _guard(request: httpx.Request) -> None:
    host = request.url.host
    if not _public_host(host):
        raise ValueError("Bu adres okunamaz.")


def lookup_site(website_url: str, query: str) -> str:
    """Verilen şirket sitesinde konuyu arar ve bulunan metni döndürür."""
    site = (website_url or "").strip()
    konu = " ".join((query or "").split())
    if not site:
        return (
            "Web sitesi tanımlanmamış. Siteden ürün okuma. "
            "Yalnızca şirket tanımında yazan bilgiyi kullan."
        )
    if not konu:
        return "Aranacak ürün veya konu boş. Müşteriden neye baktığını tekrar iste."
    parsed = urlparse(site if "://" in site else f"https://{site}")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "Web sitesi adresi okunamadı. Ürün bilgisi uydurma."
    if not _public_host(parsed.hostname):
        return "Bu web sitesi adresi okunamaz. Ürün, fiyat veya stok uydurma."
    origin = f"{parsed.scheme}://{parsed.netloc}"
    tokens = _tokens(konu)
    encoded = quote_plus(konu)
    targets = [
        site if parsed.path else origin + "/",
        f"{origin}/search?q={encoded}",
        f"{origin}/arama?q={encoded}",
        f"{origin}/?s={encoded}",
    ]
    try:
        with httpx.Client(
            timeout=8,
            follow_redirects=True,
            headers=_HEADERS,
            event_hooks={"request": [_guard]},
        ) as client:
            pages: list[tuple[str, str]] = []
            for url in targets:
                try:
                    html = _fetch(client, url)
                except (httpx.HTTPError, ValueError):
                    continue
                if html:
                    pages.append((str(url), html))
            extra: list[str] = []
            briefs: list[str] = []
            for url, html in pages:
                text, links = read_page(html, url, konu)
                if text:
                    briefs.append(text)
                extra.extend(links)
            seen = {url for url, _ in pages}
            for url in extra:
                if url in seen or len(seen) >= 6:
                    continue
                seen.add(url)
                try:
                    html = _fetch(client, url)
                except (httpx.HTTPError, ValueError):
                    continue
                if not html:
                    continue
                text, _ = read_page(html, url, konu)
                if text:
                    briefs.append(text)
                if len(briefs) >= 3:
                    break
    except (httpx.HTTPError, ValueError):
        return "Siteye şu an ulaşılamadı. Ürün, fiyat veya stok uydurma."
    if not briefs:
        return (
            f"Sitede '{konu}' için okunabilir bir ürün metni bulunamadı. "
            "Özellik, fiyat veya stok uydurma. Müşteriye sitede bu bilgiyi bulamadığını söyle."
        )
    body = "\n".join(briefs)[:1600]
    unused = tokens
    return (
        "Aşağıdaki metin şirketin sitesinden geldi. Bunu bir veya iki cümleyle özetle. "
        "Metinde olmayan özellik, fiyat veya stok ekleme.\n"
        f"{body}\n"
        f"Aranan kelimeler: {' '.join(unused)}"
    )

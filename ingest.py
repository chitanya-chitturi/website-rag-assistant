"""
ingest.py - STAGE 1 of RAG: build the searchable index.

    crawl pages  ->  clean HTML to text  ->  split into chunks
                 ->  embed each chunk    ->  store in the vector DB

Run it once, then again whenever the site changes:
    python ingest.py            # add / update pages
    python ingest.py --reset    # wipe the index and start fresh

It also writes data/chunks.jsonl so you can open it and SEE exactly what the
bot will be searching. Looking at your chunks is the best debugging habit in RAG.
"""
import argparse
import hashlib
import json
import os
import re
import time
from collections import deque
from urllib import robotparser
from urllib.parse import urljoin, urldefrag, urlparse

import requests
from bs4 import BeautifulSoup

import config
from store import get_collection

SKIP_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".zip",
                   ".css", ".js", ".xml", ".json", ".mp4", ".ico")


# ---------------------------------------------------------------------------
# Step 1: CRAWL - discover and download pages
# ---------------------------------------------------------------------------
def make_robot_checker(start_url):
    """Read the site's robots.txt so we only fetch pages we're allowed to."""
    parts = urlparse(start_url)
    rp = robotparser.RobotFileParser()
    try:
        resp = requests.get(f"{parts.scheme}://{parts.netloc}/robots.txt",
                            headers={"User-Agent": config.USER_AGENT}, timeout=15)
        rp.parse(resp.text.splitlines() if resp.ok else [])
    except requests.RequestException:
        rp.parse([])  # no robots.txt reachable -> treat everything as allowed
    return lambda url: rp.can_fetch(config.USER_AGENT, url)


def normalize(url):
    """Drop #fragments, ?queries and trailing index.html so the same page
    isn't fetched (and indexed) twice under different addresses."""
    url, _ = urldefrag(url)
    url = url.split("?")[0]
    for suffix in ("index.html", "index.htm"):
        if url.endswith("/" + suffix):
            url = url[: -len(suffix)]
    return url


def in_scope(url, prefixes):
    return any(url.startswith(p) for p in prefixes) and not url.lower().endswith(SKIP_EXTENSIONS)


def crawl(start_urls, prefixes, max_pages):
    """Breadth-first crawl: visit start pages, then pages they link to, etc."""
    allowed = make_robot_checker(start_urls[0])
    queue = deque(normalize(u) for u in start_urls)
    seen = set(queue)
    session = requests.Session()
    session.headers["User-Agent"] = config.USER_AGENT

    fetched = 0
    while queue and fetched < max_pages:
        url = queue.popleft()
        if not allowed(url):
            print(f"  skip (robots.txt): {url}")
            continue
        try:
            resp = session.get(url, timeout=20)
        except requests.RequestException as exc:
            print(f"  failed: {url} ({exc.__class__.__name__})")
            continue
        if resp.status_code != 200 or "text/html" not in resp.headers.get("Content-Type", ""):
            continue

        fetched += 1
        print(f"  fetched [{fetched}]: {url}")
        yield url, resp.text

        # Find new links on this page and queue the in-scope ones.
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            link = normalize(urljoin(url, a["href"]))
            if link not in seen and in_scope(link, prefixes):
                seen.add(link)
                queue.append(link)
        time.sleep(config.REQUEST_DELAY_SEC)


# ---------------------------------------------------------------------------
# Step 2: CLEAN - turn messy HTML into sections of plain text
# ---------------------------------------------------------------------------
MARK = "␞SECTION␞"   # an unusual character we can split on safely


def html_to_sections(html):
    """Return (page_title, [ {heading, anchor, text}, ... ]).

    Why split at headings? A heading is the author telling you where one
    topic ends and the next begins. Keeping each section whole gives the
    retriever cleaner, on-topic chunks - and each heading's id gives us a
    deep link straight to that spot on the page (url#anchor).
    """
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "").split("|")[0].strip()

    # Remove boilerplate that would pollute every chunk with menu text.
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer",
                     "aside", "form", "svg", "button"]):
        tag.decompose()

    main = soup.find("main") or soup.find("article") or soup.body or soup

    # Flatten each table row to "cell | cell | cell" so tables (holiday
    # lists, benefit tables...) stay readable after conversion to text.
    for tr in main.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        tr.replace_with(soup.new_string("\n" + " | ".join(cells) + "\n"))

    # Put a marker before each h1-h3 carrying the heading text and its id.
    for h in main.find_all(["h1", "h2", "h3"]):
        anchor = h.get("id") or (h.find(id=True) or {}).get("id", "")
        heading = h.get_text(" ", strip=True)
        h.replace_with(soup.new_string(f"\n{MARK}{anchor}{MARK}{heading}{MARK}\n"))

    text = main.get_text("\n")
    sections = []
    parts = text.split(MARK)
    # parts = [intro, anchor1, heading1, body1, anchor2, heading2, body2, ...]
    intro = clean_text(parts[0])
    if intro:
        sections.append({"heading": title, "anchor": "", "text": intro})
    for i in range(1, len(parts) - 2, 3):
        body = clean_text(parts[i + 2])
        if body:
            sections.append({"heading": parts[i + 1].strip(), "anchor": parts[i].strip(), "text": body})
    return title, sections


def clean_text(text):
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return text.strip()


# ---------------------------------------------------------------------------
# Step 3: CHUNK - cut long sections into overlapping pieces
# ---------------------------------------------------------------------------
def split_text(text, size, overlap):
    """Split on paragraph/sentence boundaries, never mid-word if avoidable."""
    if len(text) <= size:
        return [text]
    pieces, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Prefer to cut at a paragraph break, then a sentence end, then a space.
            window = text[start:end]
            cut = max(window.rfind("\n"), window.rfind(". "))
            if cut < size * 0.5:
                cut = window.rfind(" ")
            if cut > 0:
                end = start + cut + 1
        pieces.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return pieces


def make_chunks(url, title, sections):
    chunks = []
    for s_idx, sec in enumerate(sections):
        for p_idx, piece in enumerate(split_text(sec["text"], config.CHUNK_SIZE, config.CHUNK_OVERLAP)):
            if len(piece) < config.MIN_CHUNK_CHARS:
                continue
            link = f"{url}#{sec['anchor']}" if sec["anchor"] else url
            chunks.append({
                "id": hashlib.sha1(f"{url}|{s_idx}|{p_idx}".encode()).hexdigest(),
                # The heading path is prepended to the text that gets embedded.
                # This "contextual header" helps a lot: a chunk that only says
                # "12 days per year" now also says WHAT has 12 days per year.
                "text": f"{title} > {sec['heading']}\n\n{piece}",
                "metadata": {"url": url, "link": link, "title": title, "section": sec["heading"]},
            })
    return chunks


# ---------------------------------------------------------------------------
# Steps 4 + 5: EMBED and STORE (Chroma does the embedding for us on upsert)
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Crawl a site and build the RAG index.")
    parser.add_argument("--reset", action="store_true", help="wipe the index first")
    parser.add_argument("--url", action="append", help="override START_URLS (repeatable)")
    parser.add_argument("--prefix", action="append", help="override ALLOWED_PREFIXES (repeatable)")
    parser.add_argument("--max-pages", type=int, default=config.MAX_PAGES)
    args = parser.parse_args()

    start_urls = args.url or config.START_URLS
    prefixes = args.prefix or config.ALLOWED_PREFIXES

    collection = get_collection(reset=args.reset)
    os.makedirs("data", exist_ok=True)
    total_pages = total_chunks = 0

    print(f"Crawling (up to {args.max_pages} pages)...")
    with open("data/chunks.jsonl", "w", encoding="utf-8") as dump:
        for url, html in crawl(start_urls, prefixes, args.max_pages):
            title, sections = html_to_sections(html)
            chunks = make_chunks(url, title, sections)
            if not chunks:
                continue
            # Remove this page's old chunks first, so an edited page doesn't
            # leave stale pieces behind, then add the fresh ones.
            collection.delete(where={"url": url})
            collection.upsert(
                ids=[c["id"] for c in chunks],
                documents=[c["text"] for c in chunks],
                metadatas=[c["metadata"] for c in chunks],
            )
            for c in chunks:
                dump.write(json.dumps(c, ensure_ascii=False) + "\n")
            total_pages += 1
            total_chunks += len(chunks)

    print(f"\nDone: {total_pages} pages -> {total_chunks} chunks. "
          f"Index now holds {collection.count()} chunks.")
    print("Inspect them in data/chunks.jsonl, then try:  python ask.py \"your question\"")


if __name__ == "__main__":
    main()

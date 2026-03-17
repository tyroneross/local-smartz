"""Web scraping and search tools for Local Smartz."""

import re

import httpx
from bs4 import BeautifulSoup
from langchain_core.tools import tool


def _extract_title(soup: BeautifulSoup) -> str:
    """Extract page title from HTML."""
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    return ""


def _extract_meta(soup: BeautifulSoup, name: str) -> str:
    """Extract a meta tag value by name or og: property."""
    tag = soup.find("meta", attrs={"name": name})
    if tag and tag.get("content"):
        return tag["content"].strip()
    tag = soup.find("meta", property=f"og:{name}")
    if tag and tag.get("content"):
        return tag["content"].strip()
    return ""


def _extract_main_content(soup: BeautifulSoup, extract_tables: bool) -> str:
    """Extract main content using article/main tags, then density heuristic."""
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]):
        tag.decompose()

    for tag_name in ["article", "main", '[role="main"]']:
        if tag_name.startswith("["):
            container = soup.select_one(tag_name)
        else:
            container = soup.find(tag_name)
        if container:
            text = _extract_text(container, extract_tables)
            if len(text) > 200:
                return text

    candidates = []
    for div in soup.find_all(["div", "section"]):
        text = div.get_text(strip=True)
        if len(text) > 200:
            link_text = sum(len(a.get_text()) for a in div.find_all("a"))
            text_len = len(text)
            link_density = link_text / text_len if text_len else 1
            if link_density < 0.5:
                candidates.append((text_len, div))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return _extract_text(candidates[0][1], extract_tables)

    body = soup.find("body")
    if body:
        return _extract_text(body, extract_tables)
    return soup.get_text(separator="\n", strip=True)


def _extract_text(element, extract_tables: bool) -> str:
    """Convert an HTML element to clean markdown-like text."""
    parts: list[str] = []

    for child in element.children:
        if not hasattr(child, "name"):
            text = str(child).strip()
            if text:
                parts.append(text)
            continue

        tag = child.name
        if tag in ("script", "style", "nav", "footer", "aside"):
            continue

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            text = child.get_text(strip=True)
            if text:
                parts.append(f"\n{'#' * level} {text}\n")
        elif tag == "p":
            text = child.get_text(strip=True)
            if text:
                parts.append(f"\n{text}\n")
        elif tag in ("ul", "ol"):
            for li in child.find_all("li", recursive=False):
                text = li.get_text(strip=True)
                if text:
                    parts.append(f"- {text}")
        elif tag == "blockquote":
            text = child.get_text(strip=True)
            if text:
                parts.append(f"\n> {text}\n")
        elif tag == "table" and extract_tables:
            parts.append(_html_table_to_markdown(child))
        elif tag in ("pre", "code"):
            text = child.get_text()
            if text.strip():
                parts.append(f"\n```\n{text.strip()}\n```\n")
        elif tag == "a":
            text = child.get_text(strip=True)
            href = child.get("href", "")
            if text and href:
                parts.append(f"[{text}]({href})")
            elif text:
                parts.append(text)
        elif tag in ("strong", "b"):
            text = child.get_text(strip=True)
            if text:
                parts.append(f"**{text}**")
        elif tag in ("em", "i"):
            text = child.get_text(strip=True)
            if text:
                parts.append(f"*{text}*")
        elif tag == "img":
            alt = child.get("alt", "")
            src = child.get("src", "")
            if alt or src:
                parts.append(f"![{alt}]({src})")
        else:
            inner = _extract_text(child, extract_tables)
            if inner.strip():
                parts.append(inner)

    result = "\n".join(parts)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _html_table_to_markdown(table_element) -> str:
    """Convert an HTML table to markdown."""
    rows = []
    for tr in table_element.find_all("tr"):
        cells = []
        for td in tr.find_all(["td", "th"]):
            cells.append(td.get_text(strip=True).replace("|", "\\|"))
        if cells:
            rows.append(cells)

    if not rows:
        return ""

    col_count = max(len(r) for r in rows)
    lines = []
    for i, row in enumerate(rows):
        padded = row + [""] * (col_count - len(row))
        lines.append("| " + " | ".join(padded) + " |")
        if i == 0:
            lines.append("| " + " | ".join(["---"] * col_count) + " |")

    return "\n" + "\n".join(lines) + "\n"


@tool
def scrape_url(url: str, extract_tables: bool = False, selector: str | None = None) -> str:
    """Fetch a URL and extract its main content as clean markdown.

    Args:
        url: The URL to scrape
        extract_tables: If True, convert HTML tables to markdown tables
        selector: Optional CSS selector to extract specific content
    """
    try:
        try:
            import truststore
            truststore.inject_into_ssl()
        except ImportError:
            pass

        with httpx.Client(
            follow_redirects=True,
            timeout=30.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text

    except httpx.HTTPStatusError as e:
        return f"Error: HTTP {e.response.status_code} for {url}"
    except httpx.RequestError as e:
        return f"Error: Request failed for {url}: {e}"

    soup = BeautifulSoup(html, "lxml")

    title = _extract_title(soup)
    description = _extract_meta(soup, "description")
    author = _extract_meta(soup, "author")

    if selector:
        target = soup.select_one(selector)
        if not target:
            return f"Error: CSS selector '{selector}' matched nothing"
        content_text = _extract_text(target, extract_tables)
    else:
        content_text = _extract_main_content(soup, extract_tables)

    sections: list[str] = []
    sections.append(f"# {title}" if title else f"# {url}")
    sections.append(f"**URL**: {url}")
    if author:
        sections.append(f"**Author**: {author}")
    if description:
        sections.append(f"**Description**: {description}")
    sections.append("")
    sections.append(content_text)

    return "\n".join(sections)


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web using DuckDuckGo. Returns titles, URLs, and snippets.

    Args:
        query: Search query string
        max_results: Maximum number of results to return (default 5)
    """
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            from duckduckgo_search import DDGS
    except ImportError:
        return "Error: duckduckgo-search not installed. Run: pip install ddgs"

    try:
        import warnings as _w
        with _w.catch_warnings():
            _w.simplefilter("ignore", RuntimeWarning)
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))

        if not results:
            return f"No results found for: {query}"

        output = [f"# Search Results: {query}\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "Untitled")
            href = r.get("href", "")
            body = r.get("body", "")
            output.append(f"## {i}. {title}")
            output.append(f"**URL**: {href}")
            if body:
                output.append(body)
            output.append("")

        return "\n".join(output)

    except Exception as e:
        return f"Error searching for '{query}': {e}"

"""Tests for Local Smartz tools — unit tests with mocked I/O."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from localsmartz.tools.compute import python_exec
from localsmartz.tools.documents import read_text_file, parse_pdf, read_spreadsheet
from localsmartz.tools.reports import create_report, create_spreadsheet
from localsmartz.tools.web import web_search, scrape_url


# ── python_exec ──

def test_python_exec_basic():
    result = python_exec.invoke({"code": "print(2 + 2)"})
    assert "4" in result
    assert "Exit code: 0" in result


def test_python_exec_error():
    result = python_exec.invoke({"code": "raise ValueError('test')"})
    assert "ValueError" in result
    assert "Exit code: 1" in result


def test_python_exec_timeout():
    result = python_exec.invoke({"code": "import time; time.sleep(60)", "timeout": 2})
    assert "timed out" in result.lower()


# ── read_text_file ──

def test_read_text_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("line 1\nline 2\nline 3\n")
        f.flush()
        result = read_text_file.invoke({"file_path": f.name})
        assert "line 1" in result
        assert "line 3" in result


def test_read_text_file_not_found():
    result = read_text_file.invoke({"file_path": "/nonexistent/file.txt"})
    assert "error" in result.lower() or "not found" in result.lower()


def test_read_text_file_max_lines():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for i in range(100):
            f.write(f"line {i}\n")
        f.flush()
        result = read_text_file.invoke({"file_path": f.name, "max_lines": 5})
        assert "line 0" in result
        assert "Truncated" in result


# ── create_report ──

def test_create_report_markdown():
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/report.md"
        result = create_report.invoke({
            "title": "Test Report",
            "sections": [{"heading": "Intro", "content": "Hello world"}],
            "output_path": path,
            "format": "markdown",
        })
        assert "saved" in result.lower() or "Report" in result
        assert Path(path).exists()
        content = Path(path).read_text()
        assert "Test Report" in content
        assert "Hello world" in content


def test_create_report_html():
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/report.html"
        result = create_report.invoke({
            "title": "HTML Test",
            "sections": [{"heading": "Section 1", "content": "Content here"}],
            "output_path": path,
            "format": "html",
        })
        assert Path(path).exists()
        content = Path(path).read_text()
        assert "HTML Test" in content
        assert "<html" in content


def test_create_report_json_string_sections():
    """Test resilience — local models sometimes stringify the sections array."""
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/report.md"
        sections_json = json.dumps([{"heading": "Test", "content": "Works"}])
        result = create_report.invoke({
            "title": "JSON Test",
            "sections": sections_json,
            "output_path": path,
        })
        assert Path(path).exists()


# ── create_spreadsheet ──

def test_create_spreadsheet():
    with tempfile.TemporaryDirectory() as tmp:
        path = f"{tmp}/data.xlsx"
        result = create_spreadsheet.invoke({
            "data": [{"Name": "Alice", "Age": 30}, {"Name": "Bob", "Age": 25}],
            "output_path": path,
        })
        assert "saved" in result.lower() or "Spreadsheet" in result
        assert Path(path).exists()


# ── web_search (mocked) ──

def test_web_search_mocked():
    mock_results = [
        {"title": "Test Result", "href": "https://example.com", "body": "A test result"},
    ]
    with patch("duckduckgo_search.DDGS") as MockDDGS:
        mock_instance = MagicMock()
        mock_instance.__enter__ = MagicMock(return_value=mock_instance)
        mock_instance.__exit__ = MagicMock(return_value=False)
        mock_instance.text.return_value = mock_results
        MockDDGS.return_value = mock_instance

        result = web_search.invoke({"query": "test query"})
        assert "Test Result" in result
        assert "example.com" in result


# ── scrape_url (mocked) ──

def test_scrape_url_mocked():
    html = "<html><head><title>Test Page</title></head><body><article><p>Hello from the article.</p></article></body></html>"
    mock_response = MagicMock()
    mock_response.text = html
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    with patch("localsmartz.tools.web.httpx.Client") as MockClient:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        MockClient.return_value = mock_client

        result = scrape_url.invoke({"url": "https://example.com"})
        assert "Test Page" in result
        assert "Hello from the article" in result

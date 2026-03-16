"""Core domain pack — web search, scraping, document processing, computation."""

from localsmartz.tools.web import web_search, scrape_url
from localsmartz.tools.documents import parse_pdf, read_spreadsheet, read_text_file
from localsmartz.tools.reports import create_report, create_spreadsheet
from localsmartz.tools.compute import python_exec


DOMAIN_PACK = {
    "name": "core",
    "description": "Core research tools — web search, document processing, computation, report generation",
    "tools": [
        web_search,
        scrape_url,
        parse_pdf,
        read_spreadsheet,
        read_text_file,
        python_exec,
        create_report,
        create_spreadsheet,
    ],
    "agent_prompts": {},  # Core uses default prompts
}

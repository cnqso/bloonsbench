"""Block NinjaKiwi cloud sync domains via Playwright route handlers."""

from __future__ import annotations

from playwright.sync_api import Page, Route

_BLOCKED_PATTERNS = [
    "*ninjakiwi.com*",
    "*nkstatic.com*",
    "*nkgames.com*",
]


def _abort_handler(route: Route) -> None:
    route.abort("blockedbyclient")


def block_nk_domains(page: Page) -> None:
    """Must be called before page.goto()."""
    for pattern in _BLOCKED_PATTERNS:
        page.route(pattern, _abort_handler)

"""Пакет лёгковесного моста браузера Chrome/Edge DevTools Protocol (CDP)."""

from __future__ import annotations

from core.browser.cdp_bridge import BrowserCDPBridge, DOMElement, PageSummary

__all__ = ["BrowserCDPBridge", "DOMElement", "PageSummary"]

"""Лёгковесный мост Chrome/Edge DevTools Protocol (CDP) без тяжёлых зависимостей.

Использует только стандартную библиотеку (socket, struct, hashlib, base64, json)
и requests для обнаружения отладочного порта браузера (по умолчанию 127.0.0.1:9222).
Реализует компактную DOM-инспекцию в стиле browser-use ([index]<tag>...</tag>),
клик, ввод текста, навигацию и проверку доступности с мягким откатом.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import logging
import os
import socket
import ssl
import struct
from collections.abc import Callable, Sequence
from typing import Any, Protocol
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_ATTR_LEN = 60
_MAX_TEXT_LEN = 80

_INSPECT_DOM_JS = """
(() => {
  const maxEl = %d;
  const selector = [
    'a[href]', 'button', 'input', 'textarea', 'select',
    '[role="button"]', '[role="link"]', '[role="textbox"]',
    '[role="menuitem"]', '[role="tab"]', '[onclick]', '[tabindex]'
  ].join(',');
  const nodes = Array.from(document.querySelectorAll(selector));
  const vw = window.innerWidth || document.documentElement.clientWidth || 1920;
  const vh = window.innerHeight || document.documentElement.clientHeight || 1080;
  const results = [];
  window.__gupkaElements = [];

  for (const el of nodes) {
    if (results.length >= maxEl) break;
    const style = window.getComputedStyle(el);
    const hidden = style.display === 'none' || style.visibility === 'hidden';
    if (!style || hidden || style.opacity === '0') continue;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 1 || rect.height <= 1) continue;
    if (rect.bottom < 0 || rect.right < 0 || rect.top > vh || rect.left > vw) continue;
    if (el.disabled) continue;

    const idx = results.length;
    window.__gupkaElements.push(el);
    el.setAttribute('data-gupka-idx', String(idx));

    const tag = (el.tagName || '').toLowerCase();
    const rawText = (el.innerText || el.value || el.textContent || '').replace(/\\s+/g, ' ').trim();
    results.push({
      index: idx,
      tag: tag,
      text: rawText.slice(0, 80),
      role: (el.getAttribute('role') || '').trim(),
      element_type: (el.getAttribute('type') || '').trim(),
      placeholder: (el.getAttribute('placeholder') || '').trim(),
      aria_label: (el.getAttribute('aria-label') || '').trim(),
      href: (el.getAttribute('href') || '').trim(),
      element_id: (el.id || '').trim(),
      name: (el.getAttribute('name') || '').trim(),
      rect: [
        Math.round(rect.left),
        Math.round(rect.top),
        Math.round(rect.width),
        Math.round(rect.height)
      ],
      center_x: Math.round(rect.left + rect.width / 2),
      center_y: Math.round(rect.top + rect.height / 2)
    });
  }
  return results;
})()
"""


def _truncate_str(value: str, limit: int) -> str:
    """Компактно обрезает строку и убирает лишние переносы."""
    clean = " ".join(value.split())
    if len(clean) <= limit:
        return clean
    return f"{clean[: max(1, limit - 3)].rstrip()}..."


@dataclasses.dataclass(frozen=True)
class DOMElement:
    """Представление видимого интерактивного DOM-элемента браузера."""

    index: int
    tag: str
    text: str = ""
    role: str = ""
    element_type: str = ""
    placeholder: str = ""
    aria_label: str = ""
    href: str = ""
    element_id: str = ""
    name: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    center_x: int = 0
    center_y: int = 0

    def to_manifest_line(self) -> str:
        """Форматирует элемент в компактную строку `[index]<tag ...>text</tag>`."""
        attrs: list[str] = []
        attr_pairs = (
            ("id", self.element_id),
            ("type", self.element_type),
            ("name", self.name),
            ("role", self.role),
            ("placeholder", self.placeholder),
            ("aria-label", self.aria_label),
            ("href", self.href),
        )
        for key, raw_val in attr_pairs:
            if raw_val:
                safe_val = _truncate_str(raw_val, _MAX_ATTR_LEN).replace('"', "'")
                attrs.append(f'{key}="{safe_val}"')

        attr_str = f" {' '.join(attrs)}" if attrs else ""
        tag_name = self.tag or "div"
        if tag_name in ("input", "textarea", "select") and not self.text:
            return f"[{self.index}]<{tag_name}{attr_str} />"

        label = _truncate_str(self.text, _MAX_TEXT_LEN)
        return f"[{self.index}]<{tag_name}{attr_str}>{label}</{tag_name}>"


@dataclasses.dataclass(frozen=True)
class PageSummary:
    """Краткая сводка состояния текущей вкладки браузера."""

    url: str
    title: str
    elements_count: int = 0
    manifest: str = ""
    state_hash: str = ""


class CDPWebSocketProtocol(Protocol):
    """Протокол минимального клиента WebSocket для внедрения зависимостей."""

    def send_command(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Отправка CDP-команды и получение JSON-ответа."""

    def close(self) -> None:
        """Закрытие соединения."""


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    """Считывает ровно size байт из сокета или выбрасывает ConnectionError."""
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("WebSocket соединение закрыто удалённой стороной")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_http_response_header(sock: socket.socket) -> bytes:
    """Считывает HTTP-заголовки ответа рукопожатия до разделителя CRLFCRLF."""
    buf = bytearray()
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(1024)
        if not chunk:
            raise ConnectionError("Пустой ответ при WebSocket-рукопожатии")
        buf.extend(chunk)
        if len(buf) > 16384:
            raise ConnectionError("Превышен размер HTTP-заголовков WebSocket")
    return bytes(buf)


def _build_ws_frame(opcode: int, payload: bytes) -> bytes:
    """Формирует маскированный клиентский фрейм RFC6455."""
    header = bytearray([0x80 | (opcode & 0x0F)])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))

    mask_key = os.urandom(4)
    header.extend(mask_key)
    masked = bytes(b ^ mask_key[idx % 4] for idx, b in enumerate(payload))
    return bytes(header) + masked


class MinimalCDPWebSocket:
    """Минимальный клиент RFC6455 WebSocket поверх стандартного socket."""

    def __init__(self, ws_url: str, timeout: float = 3.0) -> None:
        """Открывает TCP/TLS соединение и выполняет WebSocket Upgrade."""
        self._ws_url = ws_url
        self._timeout = max(0.2, timeout)
        self._cmd_seq = 0
        self._sock = self._connect_and_handshake(ws_url, self._timeout)

    def _connect_and_handshake(self, ws_url: str, timeout: float) -> socket.socket:
        """Устанавливает соединение и проверяет ответ 101 Switching Protocols."""
        parsed = urlparse(ws_url)
        host = parsed.hostname or "127.0.0.1"
        is_tls = parsed.scheme.lower() == "wss"
        port = parsed.port or (443 if is_tls else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        raw_sock = socket.create_connection((host, port), timeout=timeout)
        sock: socket.socket = (
            ssl.create_default_context().wrap_socket(raw_sock, server_hostname=host)
            if is_tls
            else raw_sock
        )
        sock.settimeout(timeout)

        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        raw_resp = _read_http_response_header(sock)
        header_text = raw_resp.split(b"\r\n\r\n", 1)[0].decode("latin-1", errors="replace")
        if " 101 " not in header_text.splitlines()[0]:
            sock.close()
            raise ConnectionError(f"Отклонён WebSocket Upgrade: {header_text.splitlines()[0]}")

        expected_accept = base64.b64encode(
            hashlib.sha1(f"{ws_key}{_WS_GUID}".encode("ascii")).digest()
        ).decode("ascii")
        if expected_accept not in header_text:
            sock.close()
            raise ConnectionError("Некорректный заголовок Sec-WebSocket-Accept")
        return sock

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        """Отправляет одиночный RFC6455 фрейм."""
        self._sock.sendall(_build_ws_frame(opcode, payload))

    def _read_single_frame(self) -> tuple[bool, int, bytes]:
        """Считывает заголовок и тело одного входящего фрейма RFC6455."""
        first_two = _recv_exact(self._sock, 2)
        fin = bool(first_two[0] & 0x80)
        opcode = first_two[0] & 0x0F
        masked = bool(first_two[1] & 0x80)
        length = first_two[1] & 0x7F

        if length == 126:
            length = struct.unpack("!H", _recv_exact(self._sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _recv_exact(self._sock, 8))[0]

        mask_key = _recv_exact(self._sock, 4) if masked else b""
        payload = _recv_exact(self._sock, length) if length > 0 else b""
        if masked and payload:
            payload = bytes(b ^ mask_key[idx % 4] for idx, b in enumerate(payload))
        return fin, opcode, payload

    def recv_text(self) -> str:
        """Читает целое текстовое сообщение с обработкой фрагментации и Ping/Close."""
        fragments: list[bytes] = []
        while True:
            fin, opcode, payload = self._read_single_frame()
            if opcode == 0x8:
                raise ConnectionError("CDP WebSocket закрыт браузером")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x0):
                fragments.append(payload)
                if fin:
                    return b"".join(fragments).decode("utf-8", errors="replace")

    def send_command(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Отправляет CDP-команду и ожидает ответ с совпадающим id."""
        self._cmd_seq += 1
        cmd_id = self._cmd_seq
        payload = json.dumps(
            {"id": cmd_id, "method": method, "params": params or {}},
            ensure_ascii=False,
        ).encode("utf-8")
        self._send_frame(0x1, payload)

        while True:
            raw_msg = self.recv_text()
            data = json.loads(raw_msg)
            if not isinstance(data, dict):
                continue
            if data.get("id") != cmd_id:
                continue
            if "error" in data:
                err_info = data["error"]
                raise RuntimeError(f"CDP error ({method}): {err_info}")
            result = data.get("result", {})
            return result if isinstance(result, dict) else {"value": result}

    def close(self) -> None:
        """Безопасно закрывает сокет WebSocket."""
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


def _default_ws_factory(ws_url: str, timeout: float) -> CDPWebSocketProtocol:
    """Фабрика создания штатного RFC6455 WebSocket-клиента."""
    return MinimalCDPWebSocket(ws_url, timeout=timeout)


class BrowserCDPBridge:
    """Мост управления открытым Chrome/Edge через DevTools Protocol."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9222,
        timeout: float = 3.0,
        session: requests.Session | None = None,
        ws_factory: Callable[[str, float], CDPWebSocketProtocol] | None = None,
    ) -> None:
        """Инициализация моста Chrome/Edge CDP.

        Args:
            host: Хост отладочного порта браузера.
            port: Порт remote-debugging (по умолчанию 9222).
            timeout: Таймаут HTTP и WebSocket операций в секундах.
            session: HTTP-сессия requests (для инъекции в тестах).
            ws_factory: Фабрика WebSocket-клиента (для подмены в тестах).
        """
        self.host = host
        self.port = port
        self.timeout = max(0.2, timeout)
        self._session: requests.Session = session or requests.Session()
        self._ws_factory: Callable[[str, float], CDPWebSocketProtocol] = (
            ws_factory or _default_ws_factory
        )
        self._last_elements: list[DOMElement] = []
        self._last_manifest: str = ""

    @property
    def base_url(self) -> str:
        """Базовый HTTP URL отладочного интерфейса браузера."""
        return f"http://{self.host}:{self.port}"

    @property
    def last_elements(self) -> list[DOMElement]:
        """Список DOM-элементов последнего снимка."""
        return list(self._last_elements)

    @property
    def last_manifest(self) -> str:
        """Компактный текстовый манифест последнего снимка DOM."""
        return self._last_manifest

    def list_pages(self) -> list[dict[str, Any]]:
        """Возвращает список открытых вкладок браузера (type='page')."""
        try:
            resp = self._session.get(
                f"{self.base_url}/json/list",
                timeout=min(1.0, self.timeout),
            )
            if resp.status_code != 200:
                return []
            raw_targets = resp.json()
            if not isinstance(raw_targets, list):
                return []
            return [
                item
                for item in raw_targets
                if isinstance(item, dict) and item.get("type") == "page"
            ]
        except (requests.RequestException, ValueError, OSError):
            return []

    def is_available(self) -> bool:
        """Проверяет, запущен ли браузер с включённым CDP-портом."""
        return self.get_active_ws_url() is not None

    def get_active_ws_url(self) -> str | None:
        """Возвращает webSocketDebuggerUrl активной пользовательской вкладки."""
        pages = self.list_pages()
        if not pages:
            return None

        for page in pages:
            url = str(page.get("url", ""))
            ws_url = str(page.get("webSocketDebuggerUrl", "")).strip()
            if ws_url and not url.startswith(("devtools://", "chrome-extension://")):
                return ws_url

        for page in pages:
            ws_url = str(page.get("webSocketDebuggerUrl", "")).strip()
            if ws_url:
                return ws_url
        return None

    def send_cdp(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Открывает CDP-сессию к активной вкладке и выполняет команду."""
        ws_url = self.get_active_ws_url()
        if not ws_url:
            raise ConnectionError("Активная вкладка Chrome/Edge CDP недоступна")

        ws = self._ws_factory(ws_url, self.timeout)
        try:
            return ws.send_command(method, params)
        finally:
            ws.close()

    def evaluate_js(self, expression: str, return_by_value: bool = True) -> Any:
        """Выполняет JavaScript на активной вкладке через Runtime.evaluate."""
        res = self.send_cdp(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": return_by_value,
                "awaitPromise": True,
            },
        )
        if "exceptionDetails" in res:
            raise RuntimeError(f"Ошибка JS в CDP: {res['exceptionDetails']}")
        result_obj = res.get("result", {})
        if isinstance(result_obj, dict):
            return result_obj.get("value")
        return result_obj

    @staticmethod
    def format_manifest(elements: Sequence[DOMElement]) -> str:
        """Собирает компактный текстовый манифест элементов для VLM/LLM."""
        if not elements:
            return "(интерактивные DOM-элементы не найдены)"
        return "\n".join(el.to_manifest_line() for el in elements)

    @staticmethod
    def _parse_dom_element(raw: dict[str, Any]) -> DOMElement:
        """Преобразует сырой JSON-словарь элемента из JS в DOMElement."""
        raw_rect = raw.get("rect", (0, 0, 0, 0))
        if isinstance(raw_rect, (list, tuple)) and len(raw_rect) == 4:
            rect = (int(raw_rect[0]), int(raw_rect[1]), int(raw_rect[2]), int(raw_rect[3]))
        else:
            rect = (0, 0, 0, 0)
        return DOMElement(
            index=int(raw.get("index", 0)),
            tag=str(raw.get("tag", "div")),
            text=str(raw.get("text", "")),
            role=str(raw.get("role", "")),
            element_type=str(raw.get("element_type", "")),
            placeholder=str(raw.get("placeholder", "")),
            aria_label=str(raw.get("aria_label", "")),
            href=str(raw.get("href", "")),
            element_id=str(raw.get("element_id", "")),
            name=str(raw.get("name", "")),
            rect=rect,
            center_x=int(raw.get("center_x", rect[0] + rect[2] // 2)),
            center_y=int(raw.get("center_y", rect[1] + rect[3] // 2)),
        )

    def inspect_dom(self, max_elements: int = 80) -> list[DOMElement]:
        """Инспектирует видимые интерактивные элементы DOM и обновляет манифест."""
        limit = max(1, min(200, max_elements))
        try:
            raw_list = self.evaluate_js(_INSPECT_DOM_JS % limit)
        except Exception as exc:
            logger.debug("Ошибка inspect_dom: %s", exc)
            return []

        if not isinstance(raw_list, list):
            return []

        elements = [self._parse_dom_element(item) for item in raw_list if isinstance(item, dict)]
        self._last_elements = list(elements)
        self._last_manifest = self.format_manifest(elements)
        return elements

    def _resolve_element(self, index: int) -> DOMElement | None:
        """Ищет элемент по индексу в кэше или переинспектирует DOM."""
        for el in self._last_elements:
            if el.index == index:
                return el
        refreshed = self.inspect_dom()
        for el in refreshed:
            if el.index == index:
                return el
        return None

    def click_element(self, index: int) -> bool:
        """Кликает по интерактивному элементу DOM с указанным индексом."""
        if index < 0:
            return False

        js_prepare = (
            f"(() => {{"
            f"  const el = (window.__gupkaElements && window.__gupkaElements[{index}])"
            f"    || document.querySelector('[data-gupka-idx=\"{index}\"]');"
            f"  if (!el) return null;"
            f"  el.scrollIntoView({{block: 'center', inline: 'center'}});"
            f"  const r = el.getBoundingClientRect();"
            f"  const cx = Math.round(r.left + r.width / 2);"
            f"  const cy = Math.round(r.top + r.height / 2);"
            f"  return {{x: cx, y: cy}};"
            f"}})()"
        )
        try:
            coords = self.evaluate_js(js_prepare)
            if isinstance(coords, dict) and "x" in coords and "y" in coords:
                cx = int(coords["x"])
                cy = int(coords["y"])
                self._dispatch_mouse_click(cx, cy)
                return True

            cached = self._resolve_element(index)
            if cached is not None and (cached.center_x > 0 or cached.center_y > 0):
                self._dispatch_mouse_click(cached.center_x, cached.center_y)
                return True

            fallback_js = (
                f"(() => {{ const el = document.querySelector('[data-gupka-idx=\"{index}\"]');"
                f" if (!el) return false; el.click(); return true; }})()"
            )
            return bool(self.evaluate_js(fallback_js))
        except Exception as exc:
            logger.debug("Ошибка click_element(%d): %s", index, exc)
            return False

    def _dispatch_mouse_click(self, x: int, y: int) -> None:
        """Отправляет доверенный клик мыши через CDP Input.dispatchMouseEvent."""
        self.send_cdp("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        self.send_cdp(
            "Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
        )
        self.send_cdp(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
        )

    def _dispatch_enter_key(self) -> None:
        """Нажимает клавишу Enter через CDP Input.dispatchKeyEvent."""
        enter_params: dict[str, Any] = {
            "key": "Enter",
            "code": "Enter",
            "windowsVirtualKeyCode": 13,
            "nativeVirtualKeyCode": 13,
        }
        self.send_cdp("Input.dispatchKeyEvent", {"type": "keyDown", **enter_params})
        self.send_cdp("Input.dispatchKeyEvent", {"type": "keyUp", **enter_params})

    def type_text(self, index: int, text: str, press_enter: bool = False) -> bool:
        """Вводит текст в DOM-элемент по индексу и опционально нажимает Enter."""
        if index < 0:
            return False

        safe_text_json = json.dumps(text, ensure_ascii=False)
        js_focus_and_set = (
            f"(() => {{"
            f"  const el = (window.__gupkaElements && window.__gupkaElements[{index}])"
            f"    || document.querySelector('[data-gupka-idx=\"{index}\"]');"
            f"  if (!el) return false;"
            f"  el.scrollIntoView({{block: 'center', inline: 'center'}});"
            f"  el.focus();"
            f"  if ('value' in el) {{"
            f"    el.value = {safe_text_json};"
            f"    el.dispatchEvent(new Event('input', {{bubbles: true}}));"
            f"    el.dispatchEvent(new Event('change', {{bubbles: true}}));"
            f"  }}"
            f"  return true;"
            f"}})()"
        )
        try:
            focused = bool(self.evaluate_js(js_focus_and_set))
            if not focused:
                return False
            if press_enter:
                self._dispatch_enter_key()
            return True
        except Exception as exc:
            logger.debug("Ошибка type_text(%d): %s", index, exc)
            return False

    def navigate(self, url: str) -> bool:
        """Переходит по URL-адресу в активной вкладке через CDP Page.navigate."""
        target = url.strip()
        if not target:
            return False
        if "://" not in target:
            target = f"https://{target}"
        try:
            res = self.send_cdp("Page.navigate", {"url": target})
            return "errorText" not in res
        except Exception as exc:
            logger.debug("Ошибка navigate(%s): %s", target, exc)
            return False

    def scroll(self, direction: str = "down", amount: int = 500) -> bool:
        """Прокручивает страницу вверх или вниз."""
        delta = abs(amount) if direction.lower() == "down" else -abs(amount)
        try:
            self.evaluate_js(f"window.scrollBy(0, {delta}); true")
            return True
        except Exception as exc:
            logger.debug("Ошибка scroll: %s", exc)
            return False

    def get_page_summary(self, max_elements: int = 80) -> PageSummary:
        """Возвращает URL, заголовок, число элементов, компактный манифест и state_hash."""
        try:
            meta = self.evaluate_js(
                "({url: window.location.href || '', title: document.title || ''})"
            )
            url = str(meta.get("url", "")) if isinstance(meta, dict) else ""
            title = str(meta.get("title", "")) if isinstance(meta, dict) else ""
        except Exception:
            url, title = "", ""

        elements = self.inspect_dom(max_elements=max_elements)
        manifest = self.format_manifest(elements)
        digest = hashlib.sha1(f"{url}|{title}|{manifest}".encode()).hexdigest()[:16]
        return PageSummary(
            url=url,
            title=title,
            elements_count=len(elements),
            manifest=manifest,
            state_hash=digest,
        )

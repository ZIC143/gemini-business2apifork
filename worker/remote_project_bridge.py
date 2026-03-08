"""
Remote project bridge for refresh-worker.

Allows local refresh-worker to read/write account data from a remote
gemini-business2api admin service using:
  - POST /login (admin_key form)
  - GET  /admin/settings
  - GET  /admin/accounts-config
  - PUT  /admin/accounts-config
"""

import json
import os
import threading
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import requests


def _parse_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "y", "on"):
            return True
        if lowered in ("0", "false", "no", "n", "off"):
            return False
    return default


def _normalize_base_url(value: str) -> str:
    url = (value or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url.rstrip("/")


class RemoteProjectBridge:
    def __init__(self) -> None:
        self._session: Optional[requests.Session] = None
        self._signature: Optional[tuple[str, bool, int]] = None
        self._authenticated = False
        self._lock = threading.RLock()

    @staticmethod
    def is_enabled() -> bool:
        base_url = _normalize_base_url(os.getenv("REMOTE_PROJECT_BASE_URL", ""))
        return bool(base_url)

    @staticmethod
    def _load_config() -> Dict[str, Any]:
        base_url = _normalize_base_url(os.getenv("REMOTE_PROJECT_BASE_URL", ""))
        password = (os.getenv("REMOTE_PROJECT_PASSWORD", "") or "").strip()
        verify_ssl = _parse_bool(os.getenv("REMOTE_PROJECT_VERIFY_SSL"), True)

        timeout_raw = (os.getenv("REMOTE_PROJECT_TIMEOUT_SECONDS", "") or "").strip()
        try:
            timeout = int(timeout_raw) if timeout_raw else 30
        except ValueError:
            timeout = 30
        timeout = max(5, min(120, timeout))

        if not base_url:
            raise RuntimeError("REMOTE_PROJECT_BASE_URL is not configured")
        if not password:
            raise RuntimeError("REMOTE_PROJECT_PASSWORD is not configured")

        return {
            "base_url": base_url,
            "password": password,
            "verify_ssl": verify_ssl,
            "timeout": timeout,
        }

    def _ensure_session_locked(self, cfg: Dict[str, Any]) -> None:
        signature = (cfg["base_url"], cfg["verify_ssl"], cfg["timeout"])
        if self._session is not None and self._signature == signature:
            return

        if self._session is not None:
            self._session.close()

        session = requests.Session()
        session.verify = cfg["verify_ssl"]
        self._session = session
        self._signature = signature
        self._authenticated = False

    def _request_locked(self, method: str, path: str, json_body: Any = None) -> requests.Response:
        if self._session is None:
            raise RuntimeError("Remote session is not initialized")
        cfg = self._load_config()
        url = urljoin(cfg["base_url"] + "/", path.lstrip("/"))
        try:
            response = self._session.request(
                method=method.upper(),
                url=url,
                json=json_body,
                timeout=cfg["timeout"],
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Remote request failed: {exc}") from exc
        return response

    def _ensure_authenticated_locked(self, force: bool = False) -> None:
        if self._authenticated and not force:
            return
        cfg = self._load_config()
        if self._session is None:
            raise RuntimeError("Remote session is not initialized")

        login_url = urljoin(cfg["base_url"] + "/", "login")
        try:
            response = self._session.post(
                login_url,
                data={"admin_key": cfg["password"]},
                timeout=cfg["timeout"],
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Remote login failed: {exc}") from exc

        if response.status_code != 200:
            detail = self._extract_error_detail(response)
            raise RuntimeError(f"Remote login failed: {detail}")

        self._authenticated = True

    @staticmethod
    def _extract_error_detail(response: requests.Response) -> str:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = payload.get("detail") or payload.get("message")
                if detail:
                    return str(detail)
        except Exception:
            pass
        text = (response.text or "").strip()
        if text:
            return text[:300]
        return f"HTTP {response.status_code}"

    def request_json(self, method: str, path: str, json_body: Any = None) -> dict:
        with self._lock:
            cfg = self._load_config()
            self._ensure_session_locked(cfg)
            self._ensure_authenticated_locked()

            response = self._request_locked(method, path, json_body=json_body)
            if response.status_code == 401:
                self._authenticated = False
                self._ensure_authenticated_locked(force=True)
                response = self._request_locked(method, path, json_body=json_body)

            if response.status_code >= 400:
                detail = self._extract_error_detail(response)
                raise RuntimeError(f"Remote request failed: {detail}")

            if not response.content:
                return {}

            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise RuntimeError(f"Remote returned non-JSON response: {response.text[:200]}") from exc

            if not isinstance(payload, dict):
                raise RuntimeError("Remote returned invalid payload type")

            return payload

    def get_settings(self) -> dict:
        return self.request_json("GET", "admin/settings")

    def get_accounts_config(self) -> dict:
        return self.request_json("GET", "admin/accounts-config")

    def update_accounts_config(self, accounts_data: list[dict]) -> dict:
        return self.request_json("PUT", "admin/accounts-config", json_body=accounts_data)

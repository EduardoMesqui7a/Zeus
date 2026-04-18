from __future__ import annotations

import json
import threading
from http.cookies import SimpleCookie
from dataclasses import dataclass
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.query_parser import absolute_to_period_minute, period_minute_to_absolute


APP_BASE_URL = "https://app.fulltradersports.com"
GAMES_API_BASE_URL = "https://gamesapi.fulltraderapps.com"
AUTH_API_BASE_URL = "https://authapi.fulltraderapps.com"
DEFAULT_TIMEOUT = 25


class ZeusClientError(RuntimeError):
    pass


class ZeusAuthError(ZeusClientError):
    pass


class ZeusContractError(ZeusClientError):
    pass


@dataclass(frozen=True)
class ZeusClientConfig:
    auth_token: str = ""
    timeout: int = DEFAULT_TIMEOUT


def _retry_adapter() -> HTTPAdapter:
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        status=2,
        backoff_factor=0.5,
        status_forcelist=(408, 425, 429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
        raise_on_status=False,
    )
    return HTTPAdapter(max_retries=retry)


class ZeusClient:
    def __init__(self, auth_token: str, timeout: int = DEFAULT_TIMEOUT) -> None:
        token = (auth_token or "").strip()
        if token and not token.lower().startswith("bearer "):
            token = f"Bearer {token}"
        self.config = ZeusClientConfig(auth_token=token, timeout=timeout)
        self._local = threading.local()

    def set_auth_token(self, auth_token: str) -> None:
        token = (auth_token or "").strip()
        if token and not token.lower().startswith("bearer "):
            token = f"Bearer {token}"
        object.__setattr__(self.config, "auth_token", token)
        if hasattr(self._local, "session"):
            self._local.session.headers.update(self.headers)

    @property
    def headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": APP_BASE_URL,
            "Referer": f"{APP_BASE_URL}/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/147.0.0.0 Safari/537.36"
            ),
        }
        if self.config.auth_token:
            headers["Authorization"] = self.config.auth_token
        return headers

    def _session(self) -> requests.Session:
        if not hasattr(self._local, "session"):
            session = requests.Session()
            session.headers.update(self.headers)
            adapter = _retry_adapter()
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            self._local.session = session
        else:
            self._local.session.headers.update(self.headers)
        return self._local.session

    def _extract_token(self, response: requests.Response, data: Any | None = None) -> str | None:
        candidates: list[str] = []

        if isinstance(data, dict):
            for key in ("accessToken", "access_token", "token", "jwt", "bearer"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    candidates.append(value.strip())
            for nested_key in ("data", "result", "user"):
                nested = data.get(nested_key)
                if isinstance(nested, dict):
                    for key in ("accessToken", "access_token", "token", "jwt", "bearer"):
                        value = nested.get(key)
                        if isinstance(value, str) and value.strip():
                            candidates.append(value.strip())

        for cookie_key in ("tradingtool.token", "token", "accessToken", "access_token"):
            value = response.cookies.get(cookie_key) or response.headers.get(cookie_key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())

        set_cookie = response.headers.get("Set-Cookie") or response.headers.get("set-cookie")
        if set_cookie:
            parsed = SimpleCookie()
            parsed.load(set_cookie)
            for cookie_key in ("tradingtool.token", "token", "accessToken", "access_token"):
                morsel = parsed.get(cookie_key)
                if morsel and morsel.value.strip():
                    candidates.append(morsel.value.strip())

        for candidate in candidates:
            if candidate.lower().startswith("bearer "):
                return candidate
            if len(candidate) > 20:
                return f"Bearer {candidate}"
        return None

    def login(self, username: str, password: str, recaptcha: str = "risos") -> str:
        payload = {
            "username": username,
            "password": password,
            "recaptcha": recaptcha,
        }
        session = requests.Session()
        session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Origin": APP_BASE_URL,
                "Referer": f"{APP_BASE_URL}/login",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/147.0.0.0 Safari/537.36"
                ),
            }
        )
        response = session.post(
            f"{AUTH_API_BASE_URL}/auth/login",
            json=payload,
            timeout=self.config.timeout,
        )
        if response.status_code in (401, 403):
            raise ZeusAuthError("Login recusado. Verifique email e senha.")
        if response.status_code >= 400:
            raise ZeusClientError(f"Falha ao autenticar: HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError:
            data = None

        token = self._extract_token(response, data)
        if not token:
            raise ZeusContractError(
                "Nao foi possivel extrair o token da resposta de login."
            )
        self.set_auth_token(token)
        return token

    def _request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        response = self._session().request(method, url, timeout=self.config.timeout, **kwargs)
        if response.status_code in (401, 403):
            raise ZeusAuthError(
                f"Acesso negado ao endpoint {url}. Verifique se o token de acesso ainda esta valido."
            )
        if response.status_code >= 400:
            raise ZeusClientError(
                f"Falha ao consultar {url}: HTTP {response.status_code}"
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ZeusContractError(f"Resposta invalida ao consultar {url}.") from exc

    def validate(self) -> dict[str, Any]:
        return self._request_json("GET", f"{AUTH_API_BASE_URL}/users/profile")

    def count(self, query: str) -> dict[str, Any]:
        payload = {"query": query}
        return self._request_json("POST", f"{GAMES_API_BASE_URL}/legacy/zeus", json=payload)

    def search_page(self, query: str, page: int = 1) -> dict[str, Any]:
        payload = {"query": query, "page": int(page)}
        return self._request_json("POST", f"{GAMES_API_BASE_URL}/legacy/lucy", json=payload)

    def search_all(
        self,
        query: str,
        *,
        max_pages: int | None = None,
        max_games: int | None = None,
    ) -> list[dict[str, Any]]:
        first_page = self.search_page(query, page=1)
        total_pages = int(first_page.get("numberPages") or 1)
        per_page = int(first_page.get("perPage") or 10)
        current_page = int(first_page.get("currentPage") or 1)
        rows = list(first_page.get("result") or [])

        page_limit = total_pages if max_pages is None else min(total_pages, int(max_pages))
        if max_games is not None and len(rows) >= max_games:
            return rows[:max_games]

        for page in range(current_page + 1, page_limit + 1):
            page_data = self.search_page(query, page=page)
            rows.extend(page_data.get("result") or [])
            if max_games is not None and len(rows) >= max_games:
                rows = rows[:max_games]
                break
            if int(page_data.get("currentPage") or page) >= int(page_data.get("numberPages") or page):
                break

        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            key = str(row.get("sport_event_id") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(row)
        return deduped

    def fetch_snapshot(self, game_id: str, minute: int, period: int) -> dict[str, Any]:
        url = f"{GAMES_API_BASE_URL}/legacy/lucy/{game_id}"
        params = {"period": int(period), "minute": int(minute)}
        payload = self._request_json("GET", url, params=params)
        if not isinstance(payload, dict):
            raise ZeusContractError("Snapshot retornou um formato inesperado.")
        data = payload.get("data") or payload
        if not isinstance(data, dict):
            raise ZeusContractError("Snapshot sem campo 'data' valido.")
        return data

    def fetch_final_snapshot(self, game_id: str, final_minute: int = 500) -> dict[str, Any]:
        attempts = []
        if final_minute == 500:
            attempts.extend([(2, 45), (2, 44), (1, 45)])
        else:
            period, minute = absolute_to_period_minute(final_minute)
            attempts.extend([(period, minute), (2, 45), (1, 45)])

        last_error: Exception | None = None
        for period, minute in attempts:
            try:
                snapshot = self.fetch_snapshot(game_id, minute=minute, period=period)
                if snapshot:
                    return snapshot
            except Exception as exc:
                last_error = exc
        if last_error:
            raise ZeusClientError(f"Nao foi possivel obter o snapshot final de {game_id}.") from last_error
        raise ZeusClientError(f"Nao foi possivel obter o snapshot final de {game_id}.")

    def fetch_timeline(self, game_id: str, market_field: str = "BackUnder25FT") -> list[dict[str, Any]]:
        timeline: list[dict[str, Any]] = []
        for period in (1, 2):
            for minute in range(1, 46):
                try:
                    snap = self.fetch_snapshot(game_id, minute=minute, period=period)
                except Exception:
                    continue
                if not snap:
                    continue
                odd_value = snap.get(market_field)
                if odd_value is None:
                    continue
                absolute_minute = period_minute_to_absolute(period, int(snap.get("Minuto") or minute))
                gols_total = None
                if snap.get("GolsCasa") is not None and snap.get("GolsVisitante") is not None:
                    gols_total = int(snap.get("GolsCasa") or 0) + int(snap.get("GolsVisitante") or 0)
                timeline.append(
                    {
                        "absolute_minute": absolute_minute,
                        "period": period,
                        "minute": int(snap.get("Minuto") or minute),
                        "odd_value": float(odd_value),
                        "gols_total": gols_total,
                        "home_goals": int(snap.get("GolsCasa") or 0),
                        "away_goals": int(snap.get("GolsVisitante") or 0),
                        "pressao1_casa": snap.get("Pressao1Casa"),
                        "pressao1_visitante": snap.get("Pressao1Visitante"),
                        "pressao2_casa": snap.get("Pressao2Casa"),
                        "pressao2_visitante": snap.get("Pressao2Visitante"),
                    }
                )
        timeline.sort(key=lambda row: row["absolute_minute"])
        return timeline

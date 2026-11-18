"""Appels HTTP de l'agent vers le serveur Maillon."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from maillon_agent import __version__
from maillon_agent.hardware import Hardware

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
USER_AGENT = f"maillon-agent/{__version__}"


class AgentError(Exception):
    """Erreur à afficher telle quelle à l'utilisateur."""


def normalize_server_url(url: str) -> str:
    """Vérifie l'adresse du serveur. HTTPS obligatoire, sauf en local, pour que
    les jetons ne circulent jamais en clair sur le réseau."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AgentError(f"Adresse de serveur invalide : {url}")
    if parsed.scheme == "http" and parsed.hostname not in _LOCAL_HOSTS:
        raise AgentError("Le serveur doit être en HTTPS (http:// n'est accepté qu'en local).")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def _error_message(response: httpx.Response) -> str:
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return f"Réponse inattendue du serveur (HTTP {response.status_code})."


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    try:
        return httpx.request(method, url, headers=headers, timeout=20, **kwargs)
    except httpx.HTTPError as exc:
        raise AgentError(f"Serveur injoignable ({url}) : {exc.__class__.__name__}.") from exc


def enroll(server_url: str, enrollment_token: str, hardware: Hardware) -> dict:
    response = _request(
        "POST",
        f"{server_url}/agent/enroll",
        json={
            "enrollment_token": enrollment_token,
            "agent_version": __version__,
            "hardware": hardware.as_payload(),
        },
    )
    if response.status_code != 201:
        raise AgentError(_error_message(response))
    return response.json()


def me(server_url: str, agent_token: str) -> dict:
    response = _request(
        "GET", f"{server_url}/agent/me", headers={"Authorization": f"Bearer {agent_token}"}
    )
    if response.status_code == 401:
        raise AgentError(
            "Le serveur refuse le jeton de cette machine : elle a sans doute été retirée "
            "depuis l'espace fournisseur. Réinscrivez-la avec un nouveau jeton d'inscription."
        )
    if response.status_code != 200:
        raise AgentError(_error_message(response))
    return response.json()

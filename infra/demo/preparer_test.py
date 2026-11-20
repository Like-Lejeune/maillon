#!/usr/bin/env python3
"""Prépare un test de bout en bout : comptes, machine et clé, en une commande.

    python infra/demo/preparer_test.py --server http://localhost:8000

Crée un compte fournisseur et y déclare une machine (affiche son jeton
d'inscription), puis un compte client avec une clé API (affiche la clé).
Python 3.10+, sans dépendance : fonctionne aussi sous Windows.
"""

import argparse
import json
import secrets
import sys
import urllib.error
import urllib.request


def call(server: str, method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    request = urllib.request.Request(f"{server}{path}", method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(request, data, timeout=20) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        detail = json.loads(error.read() or b"{}").get("error", {}).get("message", "")
        sys.exit(f"Erreur {error.code} sur {path} : {detail}")
    except urllib.error.URLError as error:
        sys.exit(f"Serveur injoignable ({server}) : {error.reason}")


def account(server: str, role: str, email: str, name: str) -> tuple[str, str]:
    password = secrets.token_urlsafe(12)
    session = call(server, "POST", "/api/auth/register", {"email": email, "password": password, "name": name, "role": role})
    return session["token"], password


def main() -> None:
    parser = argparse.ArgumentParser(description="Prépare un test de bout en bout Maillon")
    parser.add_argument("--server", required=True, help="Adresse du serveur, ex. http://localhost:8000")
    parser.add_argument("--machine", default="Machine de test GPU", help="Nom de la machine")
    args = parser.parse_args()
    server = args.server.rstrip("/")
    tag = secrets.token_hex(3)

    health = call(server, "GET", "/health")
    if health.get("status") != "ok":
        sys.exit(f"Le serveur ne va pas bien : {health}")

    provider_email = f"fournisseur-{tag}@test.maillon"
    provider, provider_password = account(server, "provider", provider_email, "Fournisseur de test")
    machine = call(server, "POST", "/api/machines", {"name": args.machine}, provider)

    client_email = f"client-{tag}@test.maillon"
    client, client_password = account(server, "client", client_email, "Client de test")
    key = call(server, "POST", "/api/api-keys", {"name": "Test de bout en bout"}, client)

    print(f"""
Comptes créés sur {server}

  Fournisseur : {provider_email}   mot de passe : {provider_password}
  Client      : {client_email}   mot de passe : {client_password}

1. Sur la machine GPU, inscrivez l'agent avec ce jeton (valable 24 h, usage unique) :

     {machine['enrollment_token']}

     maillon-agent enroll --server {server}

2. Clé API du client, pour les appels :

     {key['key']}
""")


if __name__ == "__main__":
    main()

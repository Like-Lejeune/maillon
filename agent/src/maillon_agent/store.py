"""Cache local des modèles : téléchargement, reprise, vérification d'empreinte.

Chaque modèle vérifié occupe deux fichiers dans le dossier des modèles :
- <slug>.gguf    le fichier du modèle ;
- <slug>.sha256  son empreinte, écrite seulement après vérification.

Un téléchargement interrompu laisse <slug>.gguf.part, repris à la tentative
suivante. Un modèle n'est annoncé au serveur qu'avec son fichier .sha256.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Callable

import httpx

logger = logging.getLogger("maillon_agent")

CHUNK = 1024 * 1024
DISK_MARGIN_BYTES = 2 * 1024**3  # garder 2 Go libres après le téléchargement
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{1,119}$")


class StoreError(Exception):
    pass


class ModelStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path(self, slug: str) -> Path:
        if not _SLUG.match(slug):
            raise StoreError(f"Identifiant de modèle invalide : {slug!r}")
        return self.directory / f"{slug}.gguf"

    def digest_path(self, slug: str) -> Path:
        return self.directory / f"{slug}.sha256"

    def has(self, slug: str) -> bool:
        try:
            return self.path(slug).is_file() and self.digest_path(slug).is_file()
        except StoreError:
            return False

    def digest(self, slug: str) -> str | None:
        try:
            return self.digest_path(slug).read_text(encoding="ascii").strip()
        except (OSError, StoreError):
            return None

    def cached(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(p.stem for p in self.directory.glob("*.sha256") if self.has(p.stem))[:100]

    def download(
        self,
        slug: str,
        url: str,
        sha256: str,
        size_bytes: int,
        progress: Callable[[int, int], None] | None = None,
        client: httpx.Client | None = None,
    ) -> Path:
        """Télécharge le modèle (avec reprise), vérifie l'empreinte, puis le rend visible."""
        target = self.path(slug)
        expected = sha256.lower()
        if self.has(slug) and self.digest(slug) == expected:
            return target
        if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise StoreError("Adresse de téléchargement refusée : HTTPS obligatoire.")

        self.directory.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".gguf.part")
        already = partial.stat().st_size if partial.exists() else 0
        if already > size_bytes:
            partial.unlink()
            already = 0
        free = shutil.disk_usage(self.directory).free
        if size_bytes - already + DISK_MARGIN_BYTES > free:
            raise StoreError(
                f"Espace disque insuffisant : {size_bytes / 1024**3:.1f} Go nécessaires, "
                f"{free / 1024**3:.1f} Go libres."
            )

        hasher = hashlib.sha256()
        if already:
            with partial.open("rb") as existing:
                for block in iter(lambda: existing.read(CHUNK), b""):
                    hasher.update(block)

        own_client = client is None
        http = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, read=120))
        try:
            headers = {"Range": f"bytes={already}-"} if already else {}
            with http.stream("GET", url, headers=headers) as response:
                if already and response.status_code == 200:
                    # Le serveur ignore la reprise : on repart de zéro.
                    already = 0
                    hasher = hashlib.sha256()
                    mode = "wb"
                elif response.status_code in (200, 206):
                    mode = "ab" if already else "wb"
                else:
                    raise StoreError(f"Téléchargement refusé (HTTP {response.status_code}).")
                done = already
                with partial.open(mode) as out:
                    # Sans taille de bloc : chaque morceau reçu est écrit aussitôt, rien n'est
                    # perdu dans un tampon si la connexion coupe.
                    for block in response.iter_bytes():
                        out.write(block)
                        hasher.update(block)
                        done += len(block)
                        if done > size_bytes:
                            raise StoreError("Le fichier reçu dépasse la taille annoncée.")
                        if progress:
                            progress(done, size_bytes)
        except httpx.HTTPError as exc:
            raise StoreError(f"Téléchargement interrompu ({exc.__class__.__name__}) ; il reprendra.") from exc
        finally:
            if own_client:
                http.close()

        if done != size_bytes or hasher.hexdigest() != expected:
            partial.unlink(missing_ok=True)
            raise StoreError("Empreinte ou taille différente de celle annoncée : fichier supprimé.")
        os.replace(partial, target)
        self.digest_path(slug).write_text(expected + "\n", encoding="ascii")
        logger.info("Modèle %s téléchargé et vérifié (%.1f Go).", slug, size_bytes / 1024**3)
        return target

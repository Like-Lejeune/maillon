"""Commandes d'administration.

Créer un compte admin (impossible par l'API, par conception) :

    docker compose -p maillon-staging exec server python -m app.cli create-admin admin@exemple.com "Nom"

Le mot de passe est demandé au clavier et n'apparaît ni à l'écran ni dans
l'historique du terminal.

Ajouter ou mettre à jour un modèle du catalogue :

    python -m app.cli add-model qwen2.5-7b-instruct \
        --name "Qwen2.5 7B Instruct" \
        --url https://huggingface.co/.../Qwen2.5-7B-Instruct-Q4_K_M.gguf \
        --sha256 <empreinte SHA-256 du fichier> --size <taille en octets> \
        --min-vram 7000 --context 8192 --price-input 100000 --price-output 300000

Les prix sont en micro-crédits par million de tokens.
"""

import argparse
import asyncio
import getpass
import sys

import re

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Balance, User
from app.security import hash_password


async def _create_admin(email: str, name: str, password: str) -> int:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if existing is not None:
                print(f"Un compte existe déjà avec l'adresse {email}.", file=sys.stderr)
                return 1
            user = User(email=email, name=name, role="admin", password_hash=hash_password(password))
            db.add(user)
            await db.flush()
            db.add(Balance(user_id=user.id))
            await db.commit()
            print(f"Compte admin créé : {email}")
            return 0
    finally:
        await engine.dispose()


async def _add_model(args: argparse.Namespace) -> int:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,119}", args.slug):
        print("Identifiant invalide : minuscules, chiffres, « . », « - » et « _ ».", file=sys.stderr)
        return 1
    if not re.fullmatch(r"[0-9a-f]{64}", args.sha256.lower()):
        print("Empreinte SHA-256 invalide (64 caractères hexadécimaux).", file=sys.stderr)
        return 1
    if not args.url.startswith("https://"):
        print("L'adresse du fichier doit être en HTTPS.", file=sys.stderr)
        return 1
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.begin() as connection:
            result = await connection.execute(
                text(
                    """
                    INSERT INTO models (slug, display_name, min_vram_mb, context_length, file_url,
                                        file_sha256, file_size_bytes, price_input_per_mtok,
                                        price_output_per_mtok, is_active)
                    VALUES (:slug, :name, :vram, :ctx, :url, :sha, :size, :pin, :pout, true)
                    ON CONFLICT (slug) DO UPDATE SET
                        display_name = EXCLUDED.display_name, min_vram_mb = EXCLUDED.min_vram_mb,
                        context_length = EXCLUDED.context_length, file_url = EXCLUDED.file_url,
                        file_sha256 = EXCLUDED.file_sha256, file_size_bytes = EXCLUDED.file_size_bytes,
                        price_input_per_mtok = EXCLUDED.price_input_per_mtok,
                        price_output_per_mtok = EXCLUDED.price_output_per_mtok, is_active = true
                    RETURNING (xmax = 0) AS created
                    """
                ),
                {
                    "slug": args.slug, "name": args.name, "vram": args.min_vram, "ctx": args.context,
                    "url": args.url, "sha": args.sha256.lower(), "size": args.size,
                    "pin": args.price_input, "pout": args.price_output,
                },
            )
            created = result.scalar_one()
        print(f"Modèle {args.slug} {'ajouté' if created else 'mis à jour'}.")
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin", help="Créer un compte admin")
    create.add_argument("email")
    create.add_argument("name")
    model = commands.add_parser("add-model", help="Ajouter ou mettre à jour un modèle du catalogue")
    model.add_argument("slug")
    model.add_argument("--name", required=True)
    model.add_argument("--url", required=True, help="Adresse HTTPS du fichier GGUF")
    model.add_argument("--sha256", required=True)
    model.add_argument("--size", type=int, required=True, help="Taille du fichier en octets")
    model.add_argument("--min-vram", type=int, required=True, help="Mémoire GPU minimale, en Mio")
    model.add_argument("--context", type=int, default=8192)
    model.add_argument("--price-input", type=int, required=True)
    model.add_argument("--price-output", type=int, required=True)
    args = parser.parse_args()

    if args.command == "add-model":
        return asyncio.run(_add_model(args))

    password = getpass.getpass("Mot de passe (10 caractères minimum) : ")
    if len(password) < 10:
        print("Mot de passe trop court.", file=sys.stderr)
        return 1
    if password != getpass.getpass("Confirmation : "):
        print("Les deux saisies diffèrent.", file=sys.stderr)
        return 1
    return asyncio.run(_create_admin(args.email.strip().lower(), args.name.strip(), password))


if __name__ == "__main__":
    sys.exit(main())

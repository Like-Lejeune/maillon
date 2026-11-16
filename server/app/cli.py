"""Commandes d'administration.

Créer un compte admin (impossible par l'API, par conception) :

    docker compose -p maillon-staging exec server python -m app.cli create-admin admin@exemple.com "Nom"

Le mot de passe est demandé au clavier et n'apparaît ni à l'écran ni dans
l'historique du terminal.
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select
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


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin", help="Créer un compte admin")
    create.add_argument("email")
    create.add_argument("name")
    args = parser.parse_args()

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

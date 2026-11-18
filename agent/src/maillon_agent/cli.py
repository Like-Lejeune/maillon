"""Commande `maillon-agent`.

    maillon-agent info                                  Matériel détecté
    maillon-agent enroll --server https://api.exemple   Inscrire la machine
    maillon-agent status                                État vu par le serveur
    maillon-agent run                                   Rester connecté au réseau
    maillon-agent forget                                Effacer l'inscription locale

Le jeton d'inscription est demandé au clavier (il n'apparaît ni à l'écran ni
dans l'historique), ou lu dans la variable MAILLON_ENROLLMENT_TOKEN.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import sys

from maillon_agent import __version__, client, config, connection, hardware

# Mêmes seuils que le serveur (server/app/tiers.py). Le serveur fait foi :
# l'affichage local n'est qu'une estimation.
_TIERS = (("A", 22_000), ("B", 11_000), ("C", 7_500))


def _estimate_tier(hw: hardware.Hardware) -> str:
    if not hw.vram_mb:
        return "non éligible : aucun GPU NVIDIA détecté"
    if hw.ram_mb < 15_000:
        return "non éligible : moins de 16 Go de mémoire vive"
    if hw.disk_free_gb < 50:
        return "non éligible : moins de 50 Go libres"
    for tier, threshold in _TIERS:
        if hw.vram_mb >= threshold:
            return f"niveau {tier} (estimation)"
    return "non éligible : GPU de moins de 8 Go"


def _print_hardware(hw: hardware.Hardware) -> None:
    gpu = f"{hw.gpu_model}, {hw.vram_mb / 1024:.1f} Go" if hw.gpu_model else "aucun GPU NVIDIA détecté"
    print(f"  Système      : {hw.os}")
    print(f"  GPU          : {gpu}")
    print(f"  Processeur   : {hw.cpu_cores} cœurs logiques")
    print(f"  Mémoire vive : {hw.ram_mb / 1024:.1f} Go")
    print(f"  Disque libre : {hw.disk_free_gb} Go ({config.models_dir()})")


def cmd_info(_args: argparse.Namespace) -> int:
    hw = hardware.detect(config.models_dir())
    print("Matériel détecté :")
    _print_hardware(hw)
    print(f"  Classement   : {_estimate_tier(hw)}")
    return 0


def cmd_enroll(args: argparse.Namespace) -> int:
    existing = config.load()
    if existing and not args.force:
        print(
            f"Cette machine est déjà inscrite auprès de {existing.server_url}.\n"
            "Ajoutez --force pour la réinscrire avec un nouveau jeton.",
            file=sys.stderr,
        )
        return 1
    server_url = client.normalize_server_url(args.server)
    token = os.environ.get("MAILLON_ENROLLMENT_TOKEN") or getpass.getpass(
        "Jeton d'inscription (copié depuis l'espace fournisseur) : "
    )
    token = token.strip()
    if not token:
        print("Aucun jeton saisi.", file=sys.stderr)
        return 1

    hw = hardware.detect(config.models_dir())
    print("Matériel détecté :")
    _print_hardware(hw)
    result = client.enroll(server_url, token, hw)
    path = config.save(
        config.AgentConfig(
            server_url=server_url,
            machine_id=result["machine_id"],
            agent_token=result["agent_token"],
            websocket_url=result["websocket_url"],
        )
    )
    print(f"\nMachine inscrite. Identifiant : {result['machine_id']}")
    if result["eligible"]:
        print(f"Niveau attribué par le serveur : {result['tier']}")
    else:
        print(f"Machine inscrite mais non éligible : {result['ineligibility_reason']}")
    print(f"Configuration enregistrée dans {path}")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    cfg = config.load()
    if cfg is None:
        print("Cette machine n'est pas inscrite. Lancez d'abord : maillon-agent enroll", file=sys.stderr)
        return 1
    machine = client.me(cfg.server_url, cfg.agent_token)
    print(f"Serveur      : {cfg.server_url}")
    print(f"Machine      : {machine['name']} ({machine['id']})")
    print(f"Statut       : {machine['status']}")
    print(f"Niveau       : {machine['tier'] or 'aucun (non éligible)'}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = config.load()
    if cfg is None:
        print("Cette machine n'est pas inscrite. Lancez d'abord : maillon-agent enroll", file=sys.stderr)
        return 1
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    runner = connection.AgentRunner(
        websocket_url=cfg.websocket_url,
        agent_token=cfg.agent_token,
        models_dir=config.models_dir(),
        detect_hardware=lambda: hardware.detect(config.models_dir()),
    )
    logging.getLogger("maillon_agent").info(
        "maillon-agent %s démarre, serveur %s", __version__, cfg.server_url
    )
    try:
        asyncio.run(runner.run_forever())
    except connection.StopAgent as stop:
        logging.getLogger("maillon_agent").error("%s", stop)
        return stop.exit_code
    return 0


def cmd_forget(_args: argparse.Namespace) -> int:
    if config.forget():
        print("Inscription locale effacée. Pensez à retirer aussi la machine de l'espace fournisseur.")
    else:
        print("Aucune inscription locale à effacer.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="maillon-agent", description="Agent du réseau Maillon")
    parser.add_argument("--version", action="version", version=f"maillon-agent {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("info", help="Afficher le matériel détecté").set_defaults(func=cmd_info)

    enroll = commands.add_parser("enroll", help="Inscrire cette machine sur le réseau")
    enroll.add_argument("--server", required=True, help="Adresse du serveur, ex. https://api.exemple.com")
    enroll.add_argument("--force", action="store_true", help="Réinscrire une machine déjà inscrite")
    enroll.set_defaults(func=cmd_enroll)

    commands.add_parser("status", help="Afficher l'état de la machine").set_defaults(func=cmd_status)

    run = commands.add_parser("run", help="Rester connecté au réseau (à lancer comme service)")
    run.add_argument("-v", "--verbose", action="store_true", help="Journal détaillé")
    run.set_defaults(func=cmd_run)
    commands.add_parser("forget", help="Effacer l'inscription locale").set_defaults(func=cmd_forget)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except client.AgentError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Arrêt demandé.", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())

"""Configuration locale de l'agent : adresse du serveur et jeton d'agent.

Emplacement :
- Linux   : $XDG_CONFIG_HOME/maillon-agent/config.json (par défaut ~/.config/...)
- Windows : %APPDATA%\\Maillon\\agent\\config.json
Le dossier peut être imposé avec la variable MAILLON_AGENT_HOME.

Le fichier contient le jeton d'agent : sous Linux il n'est lisible que par
son propriétaire (droits 600).
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import asdict, dataclass
from pathlib import Path


def agent_home() -> Path:
    override = os.environ.get("MAILLON_AGENT_HOME")
    if override:
        return Path(override)
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "Maillon" / "agent"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "maillon-agent"


def config_path() -> Path:
    return agent_home() / "config.json"


def models_dir() -> Path:
    return agent_home() / "models"


@dataclass
class AgentConfig:
    server_url: str
    machine_id: str
    agent_token: str
    websocket_url: str


def load() -> AgentConfig | None:
    path = config_path()
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return AgentConfig(**{key: data[key] for key in AgentConfig.__dataclass_fields__})


def save(config: AgentConfig) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(asdict(config), indent=2)
    if platform.system() == "Windows":
        path.write_text(content, encoding="utf-8")
    else:
        # Créé directement en 600 : le jeton n'est jamais lisible par d'autres,
        # même un instant.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(path, 0o600)
    return path


def forget() -> bool:
    path = config_path()
    if path.is_file():
        path.unlink()
        return True
    return False

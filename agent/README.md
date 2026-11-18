# Agent Maillon

Programme installé sur chaque machine de calcul. Version 0.2 : il mesure le
matériel, inscrit la machine et la garde connectée au réseau. L'exécution des
tâches arrive dans la version suivante.

## Prérequis

- Linux ou Windows 10/11, 64 bits.
- Python 3.10 ou plus récent.
- Pilote NVIDIA installé (il fournit `nvidia-smi`). Sans lui, la machine est
  inscrite sans GPU et ne reçoit aucune tâche.

## Installation

Copiez le dossier `agent/` du dépôt sur la machine, puis :

| Système | Commande, depuis le dossier `agent/` |
|---|---|
| Linux | `bash install.sh` |
| Windows | `powershell -ExecutionPolicy Bypass -File .\install.ps1` |

Aucun droit administrateur n'est nécessaire.

## Inscrire la machine

1. Dans l'espace fournisseur, déclarez la machine : un **jeton d'inscription**
   s'affiche. Il ne sert qu'une fois et expire après 24 heures.
2. Sur la machine, vérifiez ce que l'agent détecte :

   ```bash
   maillon-agent info
   ```

3. Inscrivez-la ; le jeton est demandé au clavier :

   ```bash
   maillon-agent enroll --server https://api.<votre-domaine>
   ```

Le serveur répond avec le niveau attribué (A, B ou C) ou la raison pour
laquelle la machine n'est pas éligible.

## Garder la machine connectée

```bash
maillon-agent run
```

L'agent ouvre une connexion sortante vers le serveur, envoie un signal toutes
les 10 secondes et se reconnecte seul après une coupure (attente de 1 s, 2 s,
4 s… jusqu'à 60 s). Il s'arrête de lui-même dans trois cas, avec un code de sortie :

| Code | Cause | Que faire |
|---|---|---|
| 3 | Jeton refusé : machine retirée de l'espace fournisseur | `maillon-agent enroll --force` avec un nouveau jeton |
| 4 | Machine suspendue par l'administrateur | Contacter l'équipe Maillon |
| 5 | Un autre agent utilise le même jeton | N'en lancer qu'un par machine |

### Lancer l'agent au démarrage (Linux)

Créez `~/.config/systemd/user/maillon-agent.service` :

```ini
[Unit]
Description=Agent Maillon
After=network-online.target

[Service]
ExecStart=%h/.local/bin/maillon-agent run
Restart=on-failure
RestartPreventExitStatus=3 4 5
RestartSec=10

[Install]
WantedBy=default.target
```

Puis :

```bash
systemctl --user daemon-reload
systemctl --user enable --now maillon-agent
loginctl enable-linger "$USER"      # garde l'agent actif sans session ouverte
journalctl --user -u maillon-agent -f
```

### Lancer l'agent au démarrage (Windows)

Dans PowerShell :

```powershell
$Agent = "$env:LOCALAPPDATA\Maillon\agent\venv\Scripts\maillon-agent.exe"
$Action = New-ScheduledTaskAction -Execute $Agent -Argument "run"
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit 0
Register-ScheduledTask -TaskName "Maillon agent" -Action $Action -Trigger $Trigger -Settings $Settings
```

## Autres commandes

| Commande | Effet |
|---|---|
| `maillon-agent run -v` | Comme `run`, avec un journal détaillé |
| `maillon-agent status` | État de la machine vu par le serveur ; signale si elle a été retirée |
| `maillon-agent forget` | Efface l'inscription locale (retirez aussi la machine dans l'espace fournisseur) |
| `maillon-agent enroll --force ...` | Réinscrit une machine déjà inscrite, avec un nouveau jeton |

## Conditions d'éligibilité

| Niveau | Mémoire GPU |
|---|---|
| A | 24 Go et plus |
| B | 12 à 23 Go |
| C | 8 à 11 Go |

Dans tous les cas : 16 Go de mémoire vive et 50 Go libres sur le disque qui
accueille les modèles. Le serveur fait foi ; `maillon-agent info` donne une estimation.

## Où sont les fichiers

| Système | Configuration (contient le jeton d'agent) |
|---|---|
| Linux | `~/.config/maillon-agent/config.json`, lisible par vous seul |
| Windows | `%APPDATA%\Maillon\agent\config.json` |

La variable `MAILLON_AGENT_HOME` change cet emplacement.

## Sécurité

- HTTPS obligatoire vers le serveur ; `http://` n'est accepté que vers `localhost`.
- Le jeton d'agent n'est jamais affiché. Si la machine est retirée dans
  l'espace fournisseur, il cesse de fonctionner immédiatement.

## Développement

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

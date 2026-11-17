# Agent Maillon

Programme installé sur chaque machine de calcul. Version 0.1 : il mesure le
matériel et inscrit la machine sur le réseau. La connexion permanente au
serveur et l'exécution des tâches arrivent dans les versions suivantes.

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

## Autres commandes

| Commande | Effet |
|---|---|
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

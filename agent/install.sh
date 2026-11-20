#!/usr/bin/env bash
# Installe l'agent Maillon pour l'utilisateur courant (Linux), sans droits root.
# À lancer depuis le dossier agent/ du dépôt :   bash install.sh
#
# L'agent est installé dans ~/.local/share/maillon-agent/venv et la commande
# maillon-agent est placée dans ~/.local/bin.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/maillon-agent"
BIN="$HOME/.local/bin"

PYTHON="$(command -v python3 || true)"
if [[ -z "$PYTHON" ]]; then
  echo "Python 3 est introuvable. Sous Ubuntu : sudo apt install python3 python3-venv" >&2
  exit 1
fi
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10 ou plus récent est requis (trouvé : $("$PYTHON" --version))." >&2
  exit 1
fi
if ! "$PYTHON" -c 'import venv, ensurepip' 2>/dev/null; then
  echo "Le module venv manque. Sous Ubuntu : sudo apt install python3-venv" >&2
  exit 1
fi

echo "Installation dans $PREFIX"
"$PYTHON" -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet "$HERE"

mkdir -p "$BIN"
ln -sf "$PREFIX/venv/bin/maillon-agent" "$BIN/maillon-agent"

echo
"$BIN/maillon-agent" --version
if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "Attention : nvidia-smi est introuvable. Installez le pilote NVIDIA, sinon la machine sera déclarée sans GPU."
fi
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "Ajoutez $BIN à votre PATH, ou lancez directement $BIN/maillon-agent" ;;
esac
echo
echo "Étape suivante : maillon-agent info, puis maillon-agent enroll --server https://<adresse du serveur>"

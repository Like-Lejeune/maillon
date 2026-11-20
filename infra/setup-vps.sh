#!/usr/bin/env bash
# Préparation initiale du VPS Maillon (Ubuntu 22.04 ou 24.04).
# À lancer UNE fois, en root :   sudo bash setup-vps.sh
#
# Ce script :
#   1. met le système à jour et active les mises à jour de sécurité automatiques ;
#   2. installe Docker depuis le dépôt officiel ;
#   3. ouvre uniquement les ports 22, 80 et 443 ;
#   4. crée l'utilisateur « deploy », sans mot de passe, membre du groupe docker ;
#   5. crée l'arborescence /srv/maillon et le réseau Docker partagé.
#
# Il ne désactive PAS la connexion SSH par mot de passe : faites-le vous-même
# une fois votre clé testée (voir infra/README.md), pour ne pas vous enfermer dehors.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Lancez ce script en root : sudo bash $0" >&2
  exit 1
fi

. /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "Ce script est prévu pour Ubuntu (système détecté : ${ID:-inconnu})." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "== 1/5 Mise à jour du système"
apt-get update -y
apt-get upgrade -y
apt-get install -y ca-certificates curl git rsync ufw fail2ban unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "== 2/5 Installation de Docker"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker

# Limite la taille des journaux des conteneurs.
if [[ ! -f /etc/docker/daemon.json ]]; then
  cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "5" }
}
JSON
  systemctl restart docker
fi

echo "== 3/5 Pare-feu"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
systemctl enable --now fail2ban

echo "== 4/5 Utilisateur deploy"
if ! id deploy >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" deploy
fi
usermod -aG docker deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
touch /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys

echo "== 5/5 Arborescence et réseau"
for env in staging production; do
  install -d -o deploy -g deploy "/srv/maillon/$env" "/srv/maillon/$env/web"
done
install -d -o deploy -g deploy /srv/maillon/edge /srv/maillon/models /srv/maillon/backups
docker network inspect maillon_edge >/dev/null 2>&1 || docker network create maillon_edge

echo
echo "Terminé. Étapes suivantes : voir infra/README.md, section « Première mise en ligne »."
docker --version
docker compose version

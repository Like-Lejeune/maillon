#!/usr/bin/env bash
# Déploie une version de Maillon sur le VPS.
# À lancer en tant qu'utilisateur « deploy », à la main ou par GitHub Actions :
#
#   bash deploy.sh staging develop
#   bash deploy.sh production v1.0
#
# Arguments : <environnement> <branche ou tag Git>
set -euo pipefail

main() {
ENVIRONMENT="${1:?Usage : deploy.sh <staging|production> <branche ou tag>}"
REF="${2:?Usage : deploy.sh <staging|production> <branche ou tag>}"

case "$ENVIRONMENT" in
  staging|production) ;;
  *) echo "Environnement inconnu : $ENVIRONMENT" >&2; exit 1 ;;
esac

# La production ne reçoit qu'un tag vX.Y, ou un commit exact pour un retour arrière.
if [[ "$ENVIRONMENT" == "production" && ! "$REF" =~ ^v[0-9]+\.[0-9]+ && ! "$REF" =~ ^[0-9a-f]{40}$ ]]; then
  echo "La production ne reçoit que des tags vX.Y (reçu : $REF)." >&2
  exit 1
fi

BASE="/srv/maillon/$ENVIRONMENT"
REPO="$BASE/repo"
ENV_FILE="$BASE/.env"
PROJECT="maillon-$ENVIRONMENT"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Fichier $ENV_FILE introuvable : copiez .env.example et remplissez-le." >&2
  exit 1
fi
if [[ ! -d "$REPO/.git" ]]; then
  echo "Dépôt absent dans $REPO : clonez-le d'abord (voir infra/README.md)." >&2
  exit 1
fi

cd "$REPO"
PREVIOUS="$(git rev-parse HEAD)"
git fetch --tags --prune origin
if git show-ref --verify --quiet "refs/remotes/origin/$REF"; then
  git checkout --quiet --detach "origin/$REF"
else
  git checkout --quiet --detach "$REF"
fi
CURRENT="$(git rev-parse HEAD)"
echo "== $ENVIRONMENT : ${PREVIOUS:0:7} -> ${CURRENT:0:7} ($REF)"

export ENV_FILE
export STACK_NAME="$PROJECT"
export MODELS_PATH="/srv/maillon/models"
compose() { docker compose -p "$PROJECT" --env-file "$ENV_FILE" -f "$REPO/infra/docker-compose.yml" "$@"; }

compose up -d --build --remove-orphans

# Interface web : construite dans un conteneur Node (pas de Node sur le VPS),
# puis copiée dans le dossier servi par Caddy.
if [[ -f "$REPO/web/package.json" ]]; then
  echo "== Construction de l'interface web"
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$REPO/web:/app" -w /app node:22-alpine \
    sh -c "npm ci --no-audit --no-fund && npm run build"
  mkdir -p "$BASE/web"
  rsync -a --delete "$REPO/web/dist/" "$BASE/web/"
fi

# Migrations de base, dès que le serveur en contient.
if [[ -f "$REPO/server/alembic.ini" ]]; then
  compose exec -T server alembic upgrade head
fi

echo "== Contrôle de santé"
for attempt in $(seq 1 30); do
  if compose exec -T server python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)" 2>/dev/null; then
    echo "$CURRENT" > "$BASE/DEPLOYED"
    echo "$PREVIOUS" > "$BASE/PREVIOUS"
    echo "OK : $ENVIRONMENT sert la version ${CURRENT:0:7}."
    docker image prune -f >/dev/null
    exit 0
  fi
  sleep 2
done

echo "ÉCHEC : le serveur ne répond pas. Derniers journaux :" >&2
compose logs --tail 40 server >&2
echo "Pour revenir en arrière : bash deploy.sh $ENVIRONMENT $PREVIOUS" >&2
exit 1
}

# Le script est lu en entier avant exécution : le git checkout peut le remplacer sans risque.
main "$@"

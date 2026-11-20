# Infrastructure Maillon

Un seul VPS héberge la préproduction et la production. Chaque environnement a
ses propres conteneurs, sa base de données et son fichier `.env`.

```text
Internet ──► Caddy (ports 80 et 443, HTTPS automatique)        infra/edge/
                │  réseau Docker « maillon_edge »
                ├──► maillon-staging-server ──► PostgreSQL, Redis (préproduction)
                └──► maillon-production-server ──► PostgreSQL, Redis (production)
```

Seul Caddy est exposé sur Internet. PostgreSQL et Redis ne sont joignables que
par le serveur de leur environnement.

## Arborescence sur le VPS

```text
/srv/maillon/
├── edge/.env                 domaines et e-mail des certificats
├── staging/
│   ├── .env                  secrets de la préproduction
│   ├── repo/                 clone du dépôt
│   └── web/                  interface web construite
├── production/               même contenu, pour la production
├── models/                   fichiers des modèles servis aux agents
└── backups/                  sauvegardes de la base
```

Les modèles sont stockés dans un dossier du disque et distribués aux agents par
le serveur. Il n'y a pas de service de stockage S3 dans cette version : MinIO,
envisagé au départ, n'est plus maintenu en édition communautaire.

## Première mise en ligne

Prérequis : Ubuntu 22.04 ou 24.04, un accès root, et trois enregistrements DNS
de type A pointant vers l'adresse IP du VPS (préproduction, API, interface).

1. **Préparer le VPS** (en root, une seule fois) :

   ```bash
   curl -fsSLO https://raw.githubusercontent.com/Like-Lejeune/maillon/develop/infra/setup-vps.sh
   sudo bash setup-vps.sh
   ```

   Le dépôt étant privé, ce lien demande un jeton : le plus simple est de
   copier le fichier avec `scp infra/setup-vps.sh root@<IP>:` depuis votre poste.

2. **Autoriser les connexions de déploiement.** Sur votre poste, créez une clé
   réservée au déploiement, puis ajoutez sa partie publique sur le VPS :

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/maillon_deploy -C "maillon-deploy"
   ssh root@<IP> "cat >> /home/deploy/.ssh/authorized_keys" < ~/.ssh/maillon_deploy.pub
   ssh -i ~/.ssh/maillon_deploy deploy@<IP> "docker ps"     # doit répondre sans mot de passe
   ```

3. **Donner au VPS un accès en lecture au dépôt.** Sur le VPS, en tant que
   `deploy`, créez une clé et déclarez-la dans GitHub : dépôt › Settings ›
   Deploy keys › Add deploy key (lecture seule).

   ```bash
   sudo -iu deploy
   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
   cat ~/.ssh/id_ed25519.pub            # à coller dans GitHub
   git clone git@github.com:Like-Lejeune/maillon.git /srv/maillon/staging/repo
   ```

4. **Remplir les secrets de la préproduction** :

   ```bash
   cp /srv/maillon/staging/repo/.env.example /srv/maillon/staging/.env
   chmod 600 /srv/maillon/staging/.env
   nano /srv/maillon/staging/.env       # un secret par ligne : openssl rand -hex 32
   ```

5. **Démarrer le proxy HTTPS** :

   ```bash
   cp /srv/maillon/staging/repo/infra/edge/.env.example /srv/maillon/edge/.env
   nano /srv/maillon/edge/.env          # e-mail et trois domaines
   cd /srv/maillon/staging/repo/infra/edge
   docker compose -p maillon-edge --env-file /srv/maillon/edge/.env up -d
   ```

6. **Déployer la préproduction** :

   ```bash
   bash /srv/maillon/staging/repo/infra/deploy.sh staging develop
   curl https://<domaine de préproduction>/health
   ```

   Réponse attendue : `{"status":"ok","env":"staging","checks":{"database":true,"redis":true}}`.

7. **Fermer la connexion par mot de passe**, une fois la clé testée. Dans
   `/etc/ssh/sshd_config` : `PasswordAuthentication no`, puis
   `sudo systemctl restart ssh`. Gardez une session ouverte pendant le test.

## Déploiement automatique

Chaque fusion dans `develop` déclenche `.github/workflows/deploy-staging.yml`,
qui se connecte au VPS et lance `deploy.sh staging develop`.

À déclarer dans GitHub : dépôt › Settings › Secrets and variables › Actions.

| Secret | Valeur |
|---|---|
| `VPS_HOST` | Adresse IP ou nom du VPS |
| `VPS_SSH_KEY` | Contenu de `~/.ssh/maillon_deploy` (la clé privée de l'étape 2) |

## Production

Même procédure avec le dossier `production` : cloner le dépôt dans
`/srv/maillon/production/repo`, remplir `/srv/maillon/production/.env` avec
`APP_ENV=production` et des secrets différents de la préproduction, puis :

```bash
bash /srv/maillon/production/repo/infra/deploy.sh production v1.0
```

La production n'accepte qu'un tag `vX.Y`.

## Retour arrière

`deploy.sh` note la version précédente dans `/srv/maillon/<env>/PREVIOUS` :

```bash
bash deploy.sh production "$(cat /srv/maillon/production/PREVIOUS)"
```

## Commandes utiles

```bash
docker compose -p maillon-staging ps                    # état des conteneurs
docker compose -p maillon-staging logs -f server        # journaux du serveur
docker compose -p maillon-edge logs -f caddy            # journaux HTTPS
```

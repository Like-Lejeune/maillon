# Interface web Maillon

React + Vite + TypeScript. Espaces client, fournisseur et administrateur.

## Développer en local

Le serveur doit tourner sur `http://localhost:8000` (voir `infra/docker-compose.dev.yml`).

```bash
npm install
npm run dev          # http://localhost:5173
```

Vite relaie `/api`, `/v1`, `/agent` et `/health` vers le serveur, comme Caddy
en préproduction. Autre adresse de serveur : `MAILLON_API=http://... npm run dev`.

## Construire

```bash
npm run build        # vérifie les types puis produit dist/
```

Le déploiement (`infra/deploy.sh`) construit l'interface dans un conteneur Node
et copie `dist/` dans `/srv/maillon/<environnement>/web`, servi par Caddy.

## Pages

| Adresse | Page |
|---|---|
| `/connexion` | Connexion |
| `/inscription` | Création d'un compte client ou fournisseur |
| `/espace` | Espace connecté, selon le rôle (provisoire) |

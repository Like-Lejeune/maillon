# Maillon

Plateforme de calcul IA décentralisé : un réseau de machines équipées de GPU
exécute des requêtes d'inférence pour des clients, via une API compatible OpenAI.

Lancement de la version 1.0 : 1er décembre 2026.

## Structure

| Dossier   | Contenu                                              | Rôle |
|-----------|------------------------------------------------------|------|
| `server/` | API, ordonnanceur, facturation                       | BE   |
| `agent/`  | Programme installé sur les machines de calcul        | AG   |
| `web/`    | Espaces client, fournisseur, admin, site public      | FE   |
| `infra/`  | docker-compose, proxy HTTPS, scripts de déploiement  | AG   |
| `docs/`   | Périmètre, contrat OpenAPI, schéma de base           | CP   |

## Branches

- `main` : production. Seul un tag `vX.Y` y est déployé.
- `develop` : intégration, déployée automatiquement en préproduction.
- `feat/<volet>-<sujet>` : une branche par sous-tâche du planning.

## Règles de travail

1. Aucun commit direct sur `main` ni sur `develop` : tout passe par une demande
   de fusion relue par une autre personne.
2. Une demande de fusion correspond à une ligne du planning et cite son critère
   de validation.
3. Aucun secret dans le dépôt : copier `.env.example` vers `.env` et y mettre
   les vraies valeurs. `.env` est ignoré par Git.
4. Les tests passent avant toute fusion vers `develop`.

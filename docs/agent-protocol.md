# Protocole agent ↔ serveur

Complète `openapi.yaml`, qui ne peut pas décrire un canal WebSocket.
À relire par `AG` (côté agent) et `BE` (côté serveur).

## Connexion

1. L'agent appelle `POST /agent/enroll` une seule fois, avec son jeton
   d'inscription, et conserve le `agent_token` reçu.
2. Il ouvre ensuite une connexion **sortante** vers `wss://<hôte>/agent/ws`
   avec l'en-tête `Authorization: Bearer <agent_token>`.
3. En cas de coupure, il se reconnecte seul : 1 s, 2 s, 4 s… jusqu'à 60 s
   entre deux essais.

Une machine n'a qu'une connexion active. Une nouvelle connexion avec le même
jeton ferme la précédente.

## Format des messages

Chaque message est un objet JSON sur une trame texte :

```json
{ "type": "<nom>", "id": "<uuid du message>", "ts": "2026-11-16T10:00:00Z", "data": { } }
```

## Messages de l'agent vers le serveur

| Type | Quand | Contenu de `data` |
|---|---|---|
| `hello` | Juste après la connexion | `agent_version`, `hardware` (schéma `Hardware`), `cached_models` (liste de slugs) |
| `heartbeat` | Toutes les 10 s | `busy` (booléen), `job_id` en cours ou `null`, `gpu_util`, `vram_free_mb` |
| `job.accepted` | À réception d'une tâche | `job_id` |
| `job.chunk` | Pendant la génération, si `stream` est vrai | `job_id`, `index`, `content` |
| `job.result` | Fin de tâche réussie | `job_id`, `content`, `finish_reason`, `prompt_tokens`, `completion_tokens` |
| `job.error` | Échec de la tâche | `job_id`, `code`, `message`, `retryable` (booléen) |
| `benchmark.result` | Fin d'un benchmark | `tokens_per_sec`, `model` |
| `model.cached` | Modèle téléchargé et vérifié | `model`, `sha256` |

## Messages du serveur vers l'agent

| Type | Quand | Contenu de `data` |
|---|---|---|
| `welcome` | Réponse à `hello` | `machine_id`, `tier`, `heartbeat_interval_seconds` |
| `job.assign` | L'ordonnanceur choisit cette machine | `job_id`, `model`, `request` (corps de `/v1/chat/completions`), `timeout_seconds` |
| `job.cancel` | Client déconnecté ou délai dépassé | `job_id`, `reason` |
| `benchmark.run` | À l'inscription, puis à la demande | `model` |
| `model.prepare` | Demande de mise en cache d'un modèle | `model` |
| `suspended` | Machine suspendue | `reason` |

## Règles

- **Une tâche à la fois** par machine dans la version 1.0. Un `job.assign`
  reçu alors que l'agent est occupé est refusé par `job.error` avec le code
  `busy` et `retryable: true`.
- **Accusé de réception** : sans `job.accepted` dans les 5 s, le serveur
  réattribue la tâche à une autre machine.
- **Machine perdue** : sans `heartbeat` pendant 30 s, le serveur passe la
  machine hors ligne et réattribue sa tâche en cours.
- **La durée facturée est mesurée par le serveur**, entre `job.accepted` et
  `job.result`. L'agent ne déclare jamais sa propre durée.
- **Le nombre de tokens est recompté par le serveur** à partir du texte reçu.
  Les valeurs envoyées par l'agent servent seulement de contrôle.
- **Résultat en double** : un `job.result` pour une tâche déjà terminée ou
  réattribuée est ignoré et n'est pas rémunéré.
- **Trois essais au plus** par tâche. Au-delà, le client reçoit une erreur
  503 et n'est pas débité.

## Codes d'erreur de `job.error`

| Code | Signification | `retryable` |
|---|---|---|
| `busy` | L'agent exécute déjà une tâche | oui |
| `model_missing` | Modèle absent du cache local | oui |
| `out_of_memory` | VRAM insuffisante | oui, sur une autre machine |
| `runtime_error` | Erreur du moteur d'inférence | oui |
| `invalid_request` | Requête rejetée par le moteur | non |

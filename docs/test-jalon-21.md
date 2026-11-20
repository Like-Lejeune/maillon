# Test de bout en bout du 21 novembre

**But** : une vraie requête, envoyée par un client avec le SDK OpenAI, est traitée
par un vrai modèle sur une vraie carte NVIDIA, à travers le serveur et l'agent Maillon.

**Ce qui est déjà vérifié sans GPU** : tout le chemin, avec un faux moteur et un
matériel simulé. Ce test vérifie la seule partie jamais testée : le modèle qui tourne
sur la carte.

## Deux façons de le mener

| | Option A : tout sur la machine GPU | Option B : préproduction |
|---|---|---|
| Serveur | Sur la machine GPU, avec Docker | Sur le VPS, en HTTPS |
| Prérequis | Une machine GPU | VPS préparé, domaine, une machine GPU |
| Ce que ça prouve | Modèle, moteur, agent, serveur, client | Idem, plus le réseau entre machines distantes |

L'agent n'accepte `http://` que vers `localhost` : sans préproduction en HTTPS, le
serveur doit tourner sur la machine GPU elle-même (option A). L'option B se fait
dès que le VPS et le domaine sont prêts, avec `https://` à la place de `http://localhost:8000`.

## Prérequis de la machine GPU

- [ ] Carte NVIDIA avec **8 Go de mémoire ou plus** ; `nvidia-smi` répond.
- [ ] 16 Go de mémoire vive et 50 Go libres sur le disque.
- [ ] Python 3.10 ou plus récent, Git.
- [ ] Docker (pour le serveur en option A).
- [ ] Le moteur llama.cpp, l'un des deux :
  - exécutable `llama-server` d'une version **CUDA** de llama.cpp, dans le PATH ;
  - ou Docker avec accès au GPU : `docker run --rm --gpus all ubuntu nvidia-smi` doit afficher la carte.

## Modèle proposé : Qwen3 8B

Licence Apache 2.0, usage commercial permis. Fichier `Qwen3-8B-Q4_K_M.gguf`
(environ 5 Go) du dépôt officiel `Qwen/Qwen3-8B-GGUF` sur Hugging Face. **Choix à
confirmer par `CP`** ; Ministral 3 8B (Apache 2.0 aussi) est l'alternative.

Qwen3 « réfléchit » à voix haute par défaut. Pour des réponses directes, lancer
l'agent avec `MAILLON_LLAMA_ARGS="--reasoning-budget 0"`. Vérifier d'abord que
l'option existe dans votre version : `llama-server --help | grep reasoning`.

## Déroulé (option A)

### 1. Serveur

```bash
git clone git@github.com:Like-Lejeune/maillon.git && cd maillon
docker compose -f infra/docker-compose.dev.yml up -d --build
curl http://localhost:8000/health          # attendu : "status":"ok"
```

### 2. Empreinte du modèle

Le catalogue a besoin de l'empreinte SHA-256 et de la taille exactes du fichier.
Téléchargez-le une fois :

```bash
curl -L -o Qwen3-8B-Q4_K_M.gguf \
  https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf
sha256sum Qwen3-8B-Q4_K_M.gguf             # Windows : Get-FileHash Qwen3-8B-Q4_K_M.gguf
stat -c %s Qwen3-8B-Q4_K_M.gguf            # Windows : (Get-Item Qwen3-8B-Q4_K_M.gguf).Length
```

Vérifiez sur la page du dépôt que le nom du fichier est bien celui-là.

### 3. Ajouter le modèle au catalogue

```bash
docker compose -f infra/docker-compose.dev.yml exec server python -m app.cli add-model qwen3-8b \
  --name "Qwen3 8B" \
  --url https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf \
  --sha256 <empreinte> --size <taille en octets> \
  --min-vram 7000 --context 8192 --price-input 100000 --price-output 300000
```

Les prix sont provisoires (micro-crédits par million de tokens) : à fixer par `CP`.

### 4. Comptes, machine et clé

```bash
python infra/demo/preparer_test.py --server http://localhost:8000
```

Le script affiche le jeton d'inscription de la machine et la clé API du client.

### 5. Agent

```bash
cd agent && bash install.sh                 # Windows : powershell -ExecutionPolicy Bypass -File .\install.ps1
maillon-agent info                          # la carte et le niveau doivent apparaître
maillon-agent enroll --server http://localhost:8000
MAILLON_LLAMA_ARGS="--reasoning-budget 0" maillon-agent run -v
```

Pour éviter de télécharger une seconde fois les 5 Go : avant `run`, copiez le
fichier de l'étape 2 dans le dossier des modèles de l'agent
(`~/.config/maillon-agent/models/` sous Linux, `%APPDATA%\Maillon\agent\models\`
sous Windows), sous le nom `qwen3-8b.gguf`. Écrivez son empreinte, sur une seule
ligne, dans `qwen3-8b.sha256`.

Attendu dans le journal : « Connecté », puis « Téléchargement du modèle »
(sauf si copié), « téléchargé et vérifié », « Chargement du modèle », « Modèle qwen3-8b prêt ».

### 6. La requête

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer <clé API>" -H "Content-Type: application/json" \
  -d '{"model": "qwen3-8b", "messages": [{"role": "user", "content": "Explique en deux phrases ce qu est un GPU."}], "max_tokens": 200}'
```

Puis avec le SDK OpenAI (`pip install openai`) :

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8000/v1", api_key="<clé API>")
r = client.chat.completions.create(
    model="qwen3-8b",
    messages=[{"role": "user", "content": "Bonjour, qui es-tu ?"}],
    max_tokens=200,
)
print(r.choices[0].message.content, r.usage)
```

### 7. Mesures

```bash
docker compose -f infra/docker-compose.dev.yml exec postgres psql -U maillon -d maillon -c \
  "SELECT status, attempts, prompt_tokens, completion_tokens, compute_ms,
          round(completion_tokens * 1000.0 / nullif(compute_ms, 0), 1) AS tokens_par_s
   FROM jobs ORDER BY created_at DESC LIMIT 5"
```

## Critères de réussite

- [ ] `maillon-agent info` détecte la carte avec le bon niveau (A, B ou C).
- [ ] Le modèle est téléchargé et vérifié, ou copié et accepté.
- [ ] La requête `curl` renvoie une réponse cohérente, en français.
- [ ] Le SDK OpenAI fonctionne en changeant seulement l'adresse et la clé.
- [ ] La tâche est `succeeded`, avec des tokens et une durée mesurée.
- [ ] Débit noté (tokens par seconde) pour la carte testée.
- [ ] Bonus : couper l'agent pendant une longue génération, le relancer ; la
      requête suivante passe (reprise testée hors GPU, à confirmer ici).

## Si ça bloque

| Symptôme | Piste |
|---|---|
| `info` : aucun GPU | `nvidia-smi` absent ou hors du PATH ; pilote NVIDIA à installer |
| Machine « non éligible » | Moins de 16 Go de mémoire vive, de 50 Go libres ou de 8 Go de mémoire GPU |
| « Aucun moteur llama.cpp » | Ni `llama-server` dans le PATH ni Docker : voir les prérequis |
| Le moteur s'arrête au chargement | Lire `engine.log` à côté de la configuration de l'agent |
| 503 `no_capacity` | Modèle pas encore prêt sur la machine, ou machine hors ligne : voir le journal de l'agent |
| Réponse qui commence par `<think>` | Ajouter `MAILLON_LLAMA_ARGS="--reasoning-budget 0"` |
| `docker compose up` échoue | La construction de l'image du serveur n'a jamais été testée sous Docker : envoyer le message d'erreur |

Notez les résultats et le débit mesuré dans le planning, ligne « Test de bout en bout ».

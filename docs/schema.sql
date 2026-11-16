-- Maillon — schéma de base de données (PostgreSQL 15+)
-- Indépendant du framework : sert de référence aux migrations Laravel ou FastAPI.
--
-- Conventions
--   * Identifiants : UUID générés par la base.
--   * Montants : entiers en micro-crédits (1 crédit = 1 000 000 micro-crédits),
--     jamais de nombres à virgule.
--   * Dates : timestamptz, en UTC.
--   * Secrets (mots de passe, clés API, jetons d'agent) : seule l'empreinte est
--     stockée, jamais la valeur en clair.

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS citext;     -- e-mails insensibles à la casse

-- ---------------------------------------------------------------------------
-- Types
-- ---------------------------------------------------------------------------
CREATE TYPE user_role      AS ENUM ('client', 'provider', 'admin');
CREATE TYPE user_status    AS ENUM ('active', 'suspended');
CREATE TYPE machine_tier   AS ENUM ('A', 'B', 'C');
CREATE TYPE machine_status AS ENUM ('pending', 'online', 'offline', 'suspended');
CREATE TYPE job_status     AS ENUM ('queued', 'assigned', 'running',
                                    'succeeded', 'failed', 'expired');
CREATE TYPE attempt_status AS ENUM ('assigned', 'running', 'succeeded',
                                    'failed', 'timed_out', 'machine_lost');
CREATE TYPE ledger_account AS ENUM ('user', 'platform');
CREATE TYPE ledger_type    AS ENUM ('topup', 'job_debit', 'provider_earning',
                                    'commission', 'payout', 'adjustment');
CREATE TYPE payout_status  AS ENUM ('pending', 'paid', 'cancelled');

-- ---------------------------------------------------------------------------
-- Comptes et accès
-- ---------------------------------------------------------------------------
CREATE TABLE users (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email          citext      NOT NULL UNIQUE,
    password_hash  text        NOT NULL,
    name           text        NOT NULL,
    role           user_role   NOT NULL,
    status         user_status NOT NULL DEFAULT 'active',
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- Clés API des clients. key_prefix (ex. "mk_live_ab12") sert à l'affichage et
-- à retrouver la ligne ; key_hash est le SHA-256 de la clé complète.
CREATE TABLE api_keys (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          text        NOT NULL,
    key_prefix    text        NOT NULL UNIQUE,
    key_hash      text        NOT NULL UNIQUE,
    rate_limit_per_min integer NOT NULL DEFAULT 60 CHECK (rate_limit_per_min > 0),
    last_used_at  timestamptz,
    revoked_at    timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX api_keys_user_idx ON api_keys (user_id);

-- ---------------------------------------------------------------------------
-- Modèles servis
-- ---------------------------------------------------------------------------
CREATE TABLE models (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug               text    NOT NULL UNIQUE,          -- nom exposé dans l'API
    display_name       text    NOT NULL,
    min_vram_mb        integer NOT NULL CHECK (min_vram_mb > 0),
    context_length     integer NOT NULL CHECK (context_length > 0),
    file_url           text    NOT NULL,                 -- emplacement dans le stockage S3
    file_sha256        text    NOT NULL,
    file_size_bytes    bigint  NOT NULL CHECK (file_size_bytes > 0),
    -- Prix par million de tokens, en micro-crédits.
    price_input_per_mtok   bigint NOT NULL CHECK (price_input_per_mtok  >= 0),
    price_output_per_mtok  bigint NOT NULL CHECK (price_output_per_mtok >= 0),
    is_active          boolean NOT NULL DEFAULT true,
    created_at         timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Machines de calcul
-- ---------------------------------------------------------------------------
CREATE TABLE machines (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id        uuid           NOT NULL REFERENCES users(id),
    name               text           NOT NULL,
    -- Jeton d'inscription à usage unique, puis jeton d'agent permanent.
    enrollment_token_hash text UNIQUE,
    agent_token_hash   text UNIQUE,
    status             machine_status NOT NULL DEFAULT 'pending',
    tier               machine_tier,                      -- NULL tant que non mesurée
    os                 text,
    agent_version      text,
    gpu_model          text,
    vram_mb            integer CHECK (vram_mb   >= 0),
    cpu_cores          integer CHECK (cpu_cores  > 0),
    ram_mb             integer CHECK (ram_mb     > 0),
    disk_free_gb       integer CHECK (disk_free_gb >= 0),
    benchmark_tokens_per_sec numeric(10,2),
    benchmarked_at     timestamptz,
    -- Réputation : part des tâches terminées avec succès, entre 0 et 1.
    reliability_score  numeric(4,3) NOT NULL DEFAULT 1.000
                       CHECK (reliability_score BETWEEN 0 AND 1),
    jobs_succeeded     integer NOT NULL DEFAULT 0,
    jobs_failed        integer NOT NULL DEFAULT 0,
    current_job_id     uuid,                              -- une tâche à la fois (MVP)
    last_heartbeat_at  timestamptz,
    suspended_reason   text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX machines_provider_idx ON machines (provider_id);
-- Recherche de l'ordonnanceur : machines en ligne et libres, par niveau.
CREATE INDEX machines_available_idx ON machines (tier, reliability_score DESC)
    WHERE status = 'online' AND current_job_id IS NULL;

-- Modèles déjà téléchargés sur une machine (évite de renvoyer plusieurs Go).
CREATE TABLE machine_models (
    machine_id  uuid NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
    model_id    uuid NOT NULL REFERENCES models(id)   ON DELETE CASCADE,
    cached_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (machine_id, model_id)
);

-- ---------------------------------------------------------------------------
-- Tâches
-- ---------------------------------------------------------------------------
-- Tâches de contrôle : requêtes dont le résultat attendu est connu, glissées
-- parmi les vraies pour détecter les machines qui trichent.
CREATE TABLE control_tasks (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id         uuid  NOT NULL REFERENCES models(id),
    request          jsonb NOT NULL,       -- corps de requête (température 0, seed fixe)
    expected_output  text  NOT NULL,
    is_active        boolean NOT NULL DEFAULT true,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE jobs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- NULL pour une tâche de contrôle lancée par la plateforme.
    client_id         uuid REFERENCES users(id),
    api_key_id        uuid REFERENCES api_keys(id),
    model_id          uuid NOT NULL REFERENCES models(id),
    control_task_id   uuid REFERENCES control_tasks(id),
    status            job_status NOT NULL DEFAULT 'queued',
    request           jsonb NOT NULL,      -- corps de /v1/chat/completions
    stream            boolean NOT NULL DEFAULT false,
    machine_id        uuid REFERENCES machines(id),   -- machine de la tentative en cours
    attempts          smallint NOT NULL DEFAULT 0,
    max_attempts      smallint NOT NULL DEFAULT 3,
    prompt_tokens     integer,
    completion_tokens integer,
    compute_ms        integer,             -- durée mesurée par le serveur
    cost              bigint,              -- micro-crédits débités au client
    control_passed    boolean,             -- renseigné pour les tâches de contrôle
    error_code        text,
    error_message     text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    assigned_at       timestamptz,
    started_at        timestamptz,
    finished_at       timestamptz,
    CHECK ((client_id IS NOT NULL) <> (control_task_id IS NOT NULL))
);
CREATE INDEX jobs_queue_idx   ON jobs (created_at) WHERE status = 'queued';
CREATE INDEX jobs_client_idx  ON jobs (client_id, created_at DESC);
CREATE INDEX jobs_machine_idx ON jobs (machine_id, created_at DESC);
CREATE INDEX jobs_active_idx  ON jobs (assigned_at) WHERE status IN ('assigned', 'running');

ALTER TABLE machines
    ADD CONSTRAINT machines_current_job_fk
    FOREIGN KEY (current_job_id) REFERENCES jobs(id) ON DELETE SET NULL;

-- Une ligne par essai : garde la trace des reprises après panne.
CREATE TABLE job_attempts (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id       uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    machine_id   uuid NOT NULL REFERENCES machines(id),
    attempt_no   smallint NOT NULL,
    status       attempt_status NOT NULL DEFAULT 'assigned',
    error_message text,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    UNIQUE (job_id, attempt_no)
);
CREATE INDEX job_attempts_machine_idx ON job_attempts (machine_id, started_at DESC);

-- ---------------------------------------------------------------------------
-- Facturation
-- ---------------------------------------------------------------------------
-- Registre en ajout seul : on n'y modifie ni ne supprime jamais une ligne.
-- Pour chaque tâche réussie, trois lignes sont écrites dans la même
-- transaction, et leur somme est nulle :
--     job_debit (client, négatif) + provider_earning (fournisseur, positif)
--     + commission (plateforme, positif) = 0
CREATE TABLE ledger_entries (
    id            bigserial PRIMARY KEY,
    account       ledger_account NOT NULL,
    user_id       uuid REFERENCES users(id),     -- NULL pour le compte plateforme
    type          ledger_type NOT NULL,
    amount        bigint NOT NULL CHECK (amount <> 0),   -- signé, micro-crédits
    balance_after bigint NOT NULL,
    job_id        uuid REFERENCES jobs(id),
    payout_id     uuid,
    note          text,
    created_by    uuid REFERENCES users(id),     -- admin, pour topup et adjustment
    created_at    timestamptz NOT NULL DEFAULT now(),
    CHECK ((account = 'user') = (user_id IS NOT NULL))
);
CREATE INDEX ledger_user_idx ON ledger_entries (user_id, id DESC);
CREATE INDEX ledger_job_idx  ON ledger_entries (job_id);
-- Empêche de facturer deux fois la même tâche au même compte.
CREATE UNIQUE INDEX ledger_job_once_idx
    ON ledger_entries (job_id, type, COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid))
    WHERE job_id IS NOT NULL;

-- Solde courant par utilisateur, mis à jour dans la même transaction que le
-- registre (verrou de ligne : SELECT ... FOR UPDATE).
-- Client : crédits disponibles. Fournisseur : available + pending.
CREATE TABLE balances (
    user_id    uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    available  bigint NOT NULL DEFAULT 0 CHECK (available >= 0),
    -- Gains en attente de vérification avant de devenir retirables.
    pending    bigint NOT NULL DEFAULT 0 CHECK (pending >= 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Paiements aux fournisseurs, effectués à la main par l'admin au lancement.
CREATE TABLE payouts (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id  uuid NOT NULL REFERENCES users(id),
    amount       bigint NOT NULL CHECK (amount > 0),
    status       payout_status NOT NULL DEFAULT 'pending',
    method       text,                    -- ex. mobile money, virement
    reference    text,                    -- référence de la transaction externe
    paid_by      uuid REFERENCES users(id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    paid_at      timestamptz
);
CREATE INDEX payouts_provider_idx ON payouts (provider_id, created_at DESC);

ALTER TABLE ledger_entries
    ADD CONSTRAINT ledger_payout_fk
    FOREIGN KEY (payout_id) REFERENCES payouts(id);

-- ---------------------------------------------------------------------------
-- Paramètres de la plateforme (taux de commission, délais)
-- ---------------------------------------------------------------------------
CREATE TABLE settings (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO settings (key, value) VALUES
    ('commission_rate',            '0.20'),   -- part de la plateforme, à valider
    ('heartbeat_interval_seconds', '10'),
    ('heartbeat_timeout_seconds',  '30'),     -- au-delà : machine hors ligne
    ('job_timeout_seconds',        '120'),
    ('earning_hold_hours',         '72'),     -- attente avant gain retirable
    ('control_task_ratio',         '0.05');   -- part de tâches de contrôle

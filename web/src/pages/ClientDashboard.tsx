import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { api, ApiError, type ApiKey, type Balance, type CreatedApiKey, type LedgerEntry, type UsageRow } from "../api";
import {
  formatCompact,
  formatCredits,
  formatDateTime,
  formatInteger,
  formatSignedCredits,
  isoDay,
} from "../format";

const LEDGER_LABELS: Record<LedgerEntry["type"], string> = {
  topup: "Recharge",
  job_debit: "Consommation",
  provider_earning: "Gain",
  commission: "Commission",
  payout: "Paiement",
  adjustment: "Ajustement",
};

// Adresse publique de l'API pour l'exemple d'appel : en production l'API a son
// propre domaine (VITE_API_PUBLIC_URL), en préproduction c'est le même que l'interface.
const API_PUBLIC_URL = (import.meta.env.VITE_API_PUBLIC_URL as string | undefined) ?? window.location.origin;

interface Overview {
  balance: Balance;
  usage: UsageRow[];
  ledger: LedgerEntry[];
}

function lastSevenDays(): { from: string; to: string } {
  const to = new Date();
  const from = new Date(to.getTime() - 6 * 24 * 3600 * 1000);
  return { from: isoDay(from), to: isoDay(to) };
}

export function ClientDashboard() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadKeys = useCallback(async () => {
    setKeys(await api.apiKeys.list());
  }, []);

  useEffect(() => {
    const { from, to } = lastSevenDays();
    Promise.all([api.balance(), api.usage(from, to), api.ledger(10), api.apiKeys.list()])
      .then(([balance, usage, ledger, apiKeys]) => {
        setOverview({ balance, usage, ledger: ledger.data });
        setKeys(apiKeys);
      })
      .catch((error) => setLoadError(error instanceof ApiError ? error.message : "Chargement impossible."));
  }, []);

  if (loadError) {
    return (
      <div className="form-error" role="alert">
        {loadError}
      </div>
    );
  }
  if (!overview || !keys) {
    return (
      <p className="muted" role="status">
        Chargement du tableau de bord…
      </p>
    );
  }

  const requests = overview.usage.reduce((total, row) => total + row.requests, 0);
  const tokens = overview.usage.reduce((total, row) => total + row.prompt_tokens + row.completion_tokens, 0);

  return (
    <>
      <div className="stats">
        <Stat label="Solde disponible" value={`${formatCredits(overview.balance.available)} crédits`} sub="Recharge sur demande auprès de l'équipe" />
        <Stat label="Requêtes, 7 derniers jours" value={formatInteger(requests)} sub="Toutes clés confondues" />
        <Stat label="Tokens, 7 derniers jours" value={formatCompact(tokens)} sub="Entrée et sortie" />
      </div>
      <ApiKeysPanel keys={keys} onChange={loadKeys} />
      <FirstCallPanel />
      <LedgerPanel entries={overview.ledger} />
    </>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      <div className="stat-sub">{sub}</div>
    </div>
  );
}

// ------------------------------------------------------------------ clés API
function ApiKeysPanel({ keys, onChange }: { keys: ApiKey[]; onChange: () => Promise<void> }) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<CreatedApiKey | null>(null);
  const nameInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (creating) nameInput.current?.focus();
  }, [creating]);

  async function onCreate(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) {
      setError("Donnez un nom à la clé, par exemple « Production ».");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const key = await api.apiKeys.create(name.trim());
      setCreated(key);
      setCreating(false);
      setName("");
      await onChange();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Création impossible, réessayez.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel flush" aria-labelledby="keys-title">
      <div className="panel-head">
        <h2 id="keys-title">Clés API</h2>
        {!creating && (
          <button type="button" className="button primary small" onClick={() => setCreating(true)}>
            Créer une clé
          </button>
        )}
      </div>

      {created && <NewKeyNotice created={created} onDone={() => setCreated(null)} />}

      {creating && (
        <form className="inline-form" onSubmit={onCreate} noValidate>
          <div className="field grow">
            <label htmlFor="key-name">Nom de la clé</label>
            <input
              id="key-name"
              ref={nameInput}
              value={name}
              maxLength={80}
              placeholder="Production"
              onChange={(e) => setName(e.target.value)}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? "key-name-error" : undefined}
            />
            {error && (
              <p id="key-name-error" className="field-error">
                {error}
              </p>
            )}
          </div>
          <div className="inline-actions">
            <button type="submit" className="button primary" disabled={busy}>
              {busy ? "Création…" : "Créer"}
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                setCreating(false);
                setError(null);
              }}
            >
              Annuler
            </button>
          </div>
        </form>
      )}

      {keys.length === 0 ? (
        <p className="empty">Aucune clé pour l'instant. Créez-en une pour appeler l'API.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Nom</th>
                <th scope="col">Préfixe</th>
                <th scope="col">Dernière utilisation</th>
                <th scope="col">Limite par minute</th>
                <th scope="col">
                  <span className="sr-only">Action</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => (
                <KeyRow key={key.id} apiKey={key} onRevoked={onChange} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function NewKeyNotice({ created, onDone }: { created: CreatedApiKey; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  async function copy() {
    try {
      await navigator.clipboard.writeText(created.key);
      setCopied(true);
    } catch {
      // Presse-papiers refusé (page non sécurisée) : on sélectionne pour une copie manuelle.
      input.current?.select();
    }
  }

  return (
    <div className="notice" role="status">
      <p>
        <strong>Clé « {created.name} » créée.</strong> Copiez-la maintenant : elle ne sera plus jamais affichée.
      </p>
      <div className="copy-row">
        <label htmlFor="new-key" className="sr-only">
          Clé API
        </label>
        <input id="new-key" ref={input} className="mono" readOnly value={created.key} onFocus={(e) => e.target.select()} />
        <button type="button" className="button" onClick={copy}>
          {copied ? "Copiée" : "Copier"}
        </button>
      </div>
      <button type="button" className="link-button" onClick={onDone}>
        J'ai copié la clé
      </button>
    </div>
  );
}

function KeyRow({ apiKey, onRevoked }: { apiKey: ApiKey; onRevoked: () => Promise<void> }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const revoked = apiKey.revoked_at !== null;

  async function revoke() {
    setBusy(true);
    setError(null);
    try {
      await api.apiKeys.revoke(apiKey.id);
      await onRevoked();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Révocation impossible.");
      setBusy(false);
    }
  }

  return (
    <tr className={revoked ? "revoked" : undefined}>
      <td>{apiKey.name}</td>
      <td className="mono">{apiKey.key_prefix}…</td>
      <td>
        {revoked
          ? `Révoquée le ${formatDateTime(apiKey.revoked_at!)}`
          : apiKey.last_used_at
            ? formatDateTime(apiKey.last_used_at)
            : "Jamais utilisée"}
      </td>
      <td>{revoked ? "—" : apiKey.rate_limit_per_min}</td>
      <td className="actions">
        {revoked ? (
          <span className="pill off">Révoquée</span>
        ) : confirming ? (
          <span className="confirm">
            <span className="confirm-text">Les appels avec cette clé seront refusés immédiatement.</span>
            <span className="confirm-buttons">
              <button type="button" className="button danger small" onClick={revoke} disabled={busy}>
                {busy ? "Révocation…" : "Confirmer"}
              </button>
              <button type="button" className="button small" onClick={() => setConfirming(false)} disabled={busy}>
                Annuler
              </button>
            </span>
          </span>
        ) : (
          <button
            type="button"
            className="button small"
            onClick={() => setConfirming(true)}
            aria-label={`Révoquer la clé ${apiKey.name}`}
          >
            Révoquer
          </button>
        )}
        {error && <p className="field-error">{error}</p>}
      </td>
    </tr>
  );
}

// --------------------------------------------------------------- premier appel
function FirstCallPanel() {
  const snippet = `curl ${API_PUBLIC_URL}/v1/chat/completions \\
  -H "Authorization: Bearer mk_live_..." \\
  -H "Content-Type: application/json" \\
  -d '{"model": "<nom du modèle>", "messages": [{"role": "user", "content": "Bonjour"}]}'`;
  return (
    <section className="panel" aria-labelledby="first-call-title">
      <h2 id="first-call-title">Premier appel</h2>
      <p className="muted">
        L'API suit le format OpenAI : un code existant fonctionne en changeant l'adresse et la clé. La génération de
        réponses ouvre le 1er décembre.
      </p>
      <pre className="code">{snippet}</pre>
    </section>
  );
}

// ---------------------------------------------------------------- mouvements
function LedgerPanel({ entries }: { entries: LedgerEntry[] }) {
  return (
    <section className="panel flush" aria-labelledby="ledger-title">
      <div className="panel-head">
        <h2 id="ledger-title">Derniers mouvements</h2>
      </div>
      {entries.length === 0 ? (
        <p className="empty">Aucun mouvement pour l'instant. Votre première recharge apparaîtra ici.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Date</th>
                <th scope="col">Type</th>
                <th scope="col">Détail</th>
                <th scope="col" className="num">
                  Montant
                </th>
                <th scope="col" className="num">
                  Solde après
                </th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <td>{formatDateTime(entry.created_at)}</td>
                  <td>{LEDGER_LABELS[entry.type] ?? entry.type}</td>
                  <td>{entry.note ?? "—"}</td>
                  <td className="num">{formatSignedCredits(entry.amount)}</td>
                  <td className="num">{formatCredits(entry.balance_after)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

import { useState, type FormEvent } from "react";

import { ApiError } from "../api";
import { useAuth } from "../auth";
import { Link, useRouter } from "../router";
import { AuthLayout, Field } from "./AuthLayout";

type AccountType = "client" | "provider";

const MIN_PASSWORD = 10;

const ACCOUNT_TYPES: { value: AccountType; title: string; text: string }[] = [
  { value: "client", title: "Client", text: "J'utilise l'API pour faire tourner des modèles d'IA." },
  { value: "provider", title: "Fournisseur", text: "Je mets des machines avec GPU à disposition du réseau." },
];

export function Register() {
  const { register } = useAuth();
  const { navigate } = useRouter();
  const [role, setRole] = useState<AccountType>("client");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function validate(): Record<string, string> {
    const errors: Record<string, string> = {};
    if (!name.trim()) errors.name = "Indiquez votre nom ou celui de votre structure.";
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) errors.email = "Adresse e-mail invalide.";
    if (password.length < MIN_PASSWORD) errors.password = `Au moins ${MIN_PASSWORD} caractères.`;
    return errors;
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const errors = validate();
    setFieldErrors(errors);
    setError(null);
    if (Object.keys(errors).length) return;

    setSubmitting(true);
    try {
      await register({ email: email.trim(), password, name: name.trim(), role });
      navigate("/espace", { replace: true });
    } catch (caught) {
      if (caught instanceof ApiError && caught.param && ["email", "password", "name"].includes(caught.param)) {
        setFieldErrors({ [caught.param]: caught.message });
      } else {
        setError(caught instanceof ApiError ? caught.message : "Inscription impossible, réessayez.");
      }
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout title="Créer un compte" lead="Choisissez votre usage : vous ne pourrez pas le changer ensuite.">
      <form onSubmit={onSubmit} noValidate className="form">
        <fieldset className="choice">
          <legend>Type de compte</legend>
          {ACCOUNT_TYPES.map((type) => (
            <label key={type.value} className={role === type.value ? "choice-item selected" : "choice-item"}>
              <input
                type="radio"
                name="role"
                value={type.value}
                checked={role === type.value}
                onChange={() => setRole(type.value)}
              />
              <span>
                <strong>{type.title}</strong>
                <span className="muted">{type.text}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <Field
          id="name"
          label="Nom ou structure"
          autoComplete="organization"
          value={name}
          onChange={(e) => setName(e.target.value)}
          error={fieldErrors.name}
          required
        />
        <Field
          id="email"
          label="Adresse e-mail"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          error={fieldErrors.email}
          required
        />
        <Field
          id="password"
          label="Mot de passe"
          type="password"
          autoComplete="new-password"
          hint={`${MIN_PASSWORD} caractères minimum.`}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          error={fieldErrors.password}
          required
        />
        <div role="alert" aria-live="assertive" className={error ? "form-error" : "sr-only"}>
          {error}
        </div>
        <button type="submit" className="button primary" disabled={submitting}>
          {submitting ? "Création…" : "Créer mon compte"}
        </button>
      </form>
      <p className="switch">
        Déjà un compte ? <Link to="/connexion">Se connecter</Link>
      </p>
    </AuthLayout>
  );
}

import { useState, type FormEvent } from "react";

import { ApiError } from "../api";
import { useAuth } from "../auth";
import { Link, useRouter } from "../router";
import { AuthLayout, Field } from "./AuthLayout";

export function Login() {
  const { login } = useAuth();
  const { navigate } = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!email.trim() || !password) {
      setError("Saisissez votre adresse e-mail et votre mot de passe.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await login(email.trim(), password);
      navigate("/espace", { replace: true });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Connexion impossible, réessayez.");
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout title="Connexion" lead="Accédez à votre espace client, fournisseur ou administrateur.">
      <form onSubmit={onSubmit} noValidate className="form">
        <Field
          id="email"
          label="Adresse e-mail"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <Field
          id="password"
          label="Mot de passe"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <div role="alert" aria-live="assertive" className={error ? "form-error" : "sr-only"}>
          {error}
        </div>
        <button type="submit" className="button primary" disabled={submitting}>
          {submitting ? "Connexion…" : "Se connecter"}
        </button>
      </form>
      <p className="switch">
        Pas encore de compte ? <Link to="/inscription">Créer un compte</Link>
      </p>
    </AuthLayout>
  );
}

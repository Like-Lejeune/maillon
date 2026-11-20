import type { ReactNode } from "react";

import { Link } from "../router";

export function AuthLayout({ title, lead, children }: { title: string; lead: string; children: ReactNode }) {
  return (
    <div className="auth-page">
      <main className="auth-card">
        <Link to="/" className="brand">
          Maillon
        </Link>
        <h1>{title}</h1>
        <p className="muted">{lead}</p>
        {children}
      </main>
    </div>
  );
}

export function Field({
  id,
  label,
  error,
  hint,
  ...input
}: {
  id: string;
  label: string;
  error?: string | null;
  hint?: string;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  const describedBy = [hint && !error ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean).join(" ");
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} aria-invalid={error ? true : undefined} aria-describedby={describedBy || undefined} {...input} />
      {hint && !error && (
        <p id={`${id}-hint`} className="hint">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="field-error">
          {error}
        </p>
      )}
    </div>
  );
}

import { useState } from "react";

import type { Role } from "../api";
import { useAuth } from "../auth";
import { useRouter } from "../router";

// Espace provisoire : les tableaux de bord des maquettes arrivent à partir du 18 novembre.
const SPACES: Record<Role, { label: string; next: string }> = {
  client: { label: "Espace client", next: "Tableau de bord, clés API et consommation." },
  provider: { label: "Espace fournisseur", next: "Vos machines, leurs gains et l'installation de l'agent." },
  admin: { label: "Administration", next: "État du réseau, machines, tâches, recharges et paiements." },
};

export function Space() {
  const { user, logout } = useAuth();
  const { navigate } = useRouter();
  const [leaving, setLeaving] = useState(false);
  if (!user) return null;
  const space = SPACES[user.role];

  async function onLogout() {
    setLeaving(true);
    await logout();
    navigate("/connexion", { replace: true });
  }

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">Maillon</span>
        <span className="muted">{space.label}</span>
        <span className="spacer" />
        <span className="who">{user.email}</span>
        <button type="button" className="button" onClick={onLogout} disabled={leaving}>
          Se déconnecter
        </button>
      </header>
      <main className="content">
        <h1>Bonjour {user.name}</h1>
        <p className="muted">Votre compte est actif.</p>
        <section className="panel">
          <h2>Bientôt ici</h2>
          <p>{space.next}</p>
        </section>
      </main>
    </div>
  );
}

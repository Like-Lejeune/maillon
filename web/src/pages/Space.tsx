import { useState } from "react";

import type { Role } from "../api";
import { useAuth } from "../auth";
import { useRouter } from "../router";
import { ClientDashboard } from "./ClientDashboard";

const SPACES: Record<Role, { label: string; lead: string }> = {
  client: { label: "Espace client", lead: "Solde, clés et consommation de votre compte." },
  provider: { label: "Espace fournisseur", lead: "Vos machines, leurs gains et l'installation de l'agent." },
  admin: { label: "Administration", lead: "État du réseau, machines, tâches, recharges et paiements." },
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
        <div className="page-head">
          <h1>{user.role === "client" ? "Tableau de bord" : `Bonjour ${user.name}`}</h1>
          <p className="muted">{space.lead}</p>
        </div>
        {user.role === "client" ? (
          <ClientDashboard />
        ) : (
          // Espaces fournisseur et admin : planning des 23 et 25 novembre.
          <section className="panel">
            <h2>Bientôt ici</h2>
            <p>Cet espace ouvre avant le lancement du 1er décembre.</p>
          </section>
        )}
      </main>
    </div>
  );
}

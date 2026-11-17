import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { api, ApiError, sessionStore, type User } from "./api";

interface AuthValue {
  user: User | null;
  /** Vrai tant que la session enregistrée n'a pas été vérifiée auprès du serveur. */
  loading: boolean;
  login: (email: string, password: string) => Promise<User>;
  register: (input: { email: string; password: string; name: string; role: "client" | "provider" }) => Promise<User>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(() => sessionStore.get() !== null);

  useEffect(() => {
    if (!sessionStore.get()) return;
    let cancelled = false;
    api
      .me()
      .then((me) => {
        if (!cancelled) setUser(me);
      })
      .catch((error) => {
        // Session expirée ou révoquée : on l'oublie. Serveur injoignable : on la garde.
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          sessionStore.clear();
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const session = await api.login({ email, password });
    sessionStore.set(session.token);
    setUser(session.user);
    return session.user;
  }, []);

  const register = useCallback<AuthValue["register"]>(async (input) => {
    const session = await api.register(input);
    sessionStore.set(session.token);
    setUser(session.user);
    return session.user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // Même si le serveur ne répond pas, on ferme la session côté navigateur.
    }
    sessionStore.clear();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth doit être utilisé dans AuthProvider");
  return value;
}

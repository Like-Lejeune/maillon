// Appels à l'API de la plateforme (/api/...), conformes à docs/openapi.yaml.

export type Role = "client" | "provider" | "admin";

export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
  status: "active" | "suspended";
  created_at: string;
}

export interface Session {
  token: string;
  expires_at: string;
  user: User;
}

/** Erreur renvoyée par l'API, au format { error: { message, type, code, param } }. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code: string | null = null,
    readonly param: string | null = null,
  ) {
    super(message);
  }
}

const TOKEN_KEY = "maillon.session";

export const sessionStore = {
  get(): string | null {
    try {
      return window.localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set(token: string): void {
    try {
      window.localStorage.setItem(TOKEN_KEY, token);
    } catch {
      // Stockage indisponible (navigation privée stricte) : la session dure le temps de la page.
    }
  },
  clear(): void {
    try {
      window.localStorage.removeItem(TOKEN_KEY);
    } catch {
      // idem
    }
  },
};

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = sessionStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Le serveur est injoignable. Vérifiez votre connexion et réessayez.");
  }

  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = data?.error;
    throw new ApiError(
      response.status,
      error?.message ?? `Erreur inattendue (HTTP ${response.status}).`,
      error?.code ?? null,
      error?.param ?? null,
    );
  }
  return data as T;
}

export const api = {
  register: (input: { email: string; password: string; name: string; role: "client" | "provider" }) =>
    request<Session>("POST", "/api/auth/register", input),
  login: (input: { email: string; password: string }) =>
    request<Session>("POST", "/api/auth/login", input),
  logout: () => request<void>("POST", "/api/auth/logout"),
  me: () => request<User>("GET", "/api/me"),
};

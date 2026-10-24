import axios, { type AxiosError, type AxiosRequestConfig } from 'axios';
import type { Customer, Problem, TokenResponse } from '../types';

export type Backend = 'python' | 'dotnet';

const configuredDefault = import.meta.env.VITE_API_URL || 'http://localhost:8000';
export const defaultBackend: Backend = configuredDefault.includes(':5000') ? 'dotnet' : 'python';

export const API_URLS: Record<Backend, string> = {
  python: import.meta.env.VITE_PYTHON_API_URL || (defaultBackend === 'python' ? configuredDefault : 'http://localhost:8000'),
  dotnet: import.meta.env.VITE_DOTNET_API_URL || (defaultBackend === 'dotnet' ? configuredDefault : 'http://localhost:5000'),
};

// ---------------------------------------------------------------------------
// Auth store (persisted in localStorage 'fincore-auth')

export interface StoredAuth {
  accessToken: string;
  refreshToken: string;
  customer: Customer;
}

const AUTH_KEY = 'fincore-auth';
const BACKEND_KEY = 'fincore-backend';

function loadAuth(): StoredAuth | null {
  try {
    const raw = localStorage.getItem(AUTH_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredAuth;
    return parsed?.accessToken && parsed?.refreshToken ? parsed : null;
  } catch {
    return null;
  }
}

let auth: StoredAuth | null = loadAuth();
let currentBackend: Backend = (() => {
  const stored = localStorage.getItem(BACKEND_KEY);
  return stored === 'dotnet' || stored === 'python' ? stored : defaultBackend;
})();

let authFailureHandler: (() => void) | null = null;

export function getAuth(): StoredAuth | null {
  return auth;
}

export function setAuth(next: StoredAuth | null): void {
  auth = next;
  if (next) localStorage.setItem(AUTH_KEY, JSON.stringify(next));
  else localStorage.removeItem(AUTH_KEY);
}

export function getBackend(): Backend {
  return currentBackend;
}

export function setBackend(next: Backend): void {
  currentBackend = next;
  localStorage.setItem(BACKEND_KEY, next);
}

const ROLE_CLAIMS = ['roles', 'role', 'http://schemas.microsoft.com/ws/2008/06/identity/claims/role'];

/**
 * Roles from the access token, for showing or hiding navigation only. The API enforces every
 * permission itself. Python issues a "roles" array; .NET issues "role" as a string or array.
 */
export function rolesFromToken(token: string | null | undefined): string[] {
  if (!token) return [];
  try {
    const segment = token.split('.')[1] ?? '';
    const json = atob(segment.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(segment.length / 4) * 4, '='));
    const claims = JSON.parse(json) as Record<string, unknown>;
    return ROLE_CLAIMS.flatMap((name) => {
      const value = claims[name];
      if (typeof value === 'string') return [value];
      return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
    });
  } catch {
    return [];
  }
}

export function setAuthFailureHandler(handler: (() => void) | null): void {
  authFailureHandler = handler;
}

// ---------------------------------------------------------------------------
// Axios instance with dynamic base URL + bearer token

export const http = axios.create({
  timeout: 20_000,
  headers: { Accept: 'application/json' },
});

const randomHex = (bytes: number) =>
  Array.from(crypto.getRandomValues(new Uint8Array(bytes)), (b) => b.toString(16).padStart(2, '0')).join('');

/**
 * Each request starts a distributed trace in the browser: a W3C traceparent the APIs continue
 * through the database, the outbox and Service Bus. The trace id doubles as the correlation id,
 * so the traceId in an error response is the id to search for in the tracing dashboard.
 */
function traceHeaders(): Record<string, string> {
  const traceId = randomHex(16);
  return { traceparent: `00-${traceId}-${randomHex(8)}-01`, 'X-Correlation-ID': traceId };
}

http.interceptors.request.use((config) => {
  config.baseURL = API_URLS[getBackend()];
  for (const [name, value] of Object.entries(traceHeaders())) config.headers.set(name, value);
  if (auth?.accessToken) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${auth.accessToken}`;
  }
  return config;
});

// Plain axios for token refresh (must not recurse through the interceptors).
const refreshClient = axios.create({ timeout: 20_000, headers: { Accept: 'application/json' } });

interface RetriableConfig extends AxiosRequestConfig {
  _retried?: boolean;
}

let refreshPromise: Promise<string> | null = null;

async function refreshTokens(refreshToken: string): Promise<string> {
  const { data } = await refreshClient.post<TokenResponse>(
    `${API_URLS[getBackend()]}/api/v1/auth/refresh`,
    { refreshToken },
  );
  if (auth) {
    setAuth({ ...auth, accessToken: data.accessToken, refreshToken: data.refreshToken });
  }
  return data.accessToken;
}

function forceLogout(): void {
  setAuth(null);
  authFailureHandler?.();
}

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<Problem>) => {
    const original = error.config as RetriableConfig | undefined;
    const status = error.response?.status;
    const code = error.response?.data?.code;
    const url = original?.url ?? '';

    const isAuthCall = url.includes('/auth/login') || url.includes('/auth/register') || url.includes('/auth/refresh');
    // Both backends answer an expired access token with code "unauthorized", so that code must
    // trigger a refresh. Auth calls are excluded, each request retries once, and a failed
    // refresh signs the user out.
    const excluded = code === 'invalid_credentials';

    if (status === 401 && original && !original._retried && !isAuthCall && !excluded) {
      original._retried = true;
      if (auth?.refreshToken) {
        try {
          refreshPromise ??= refreshTokens(auth.refreshToken).finally(() => {
            refreshPromise = null;
          });
          const token = await refreshPromise;
          original.headers = original.headers ?? {};
          original.headers.Authorization = `Bearer ${token}`;
          return http.request(original);
        } catch {
          forceLogout();
        }
      } else {
        forceLogout();
      }
    }

    return Promise.reject(error);
  },
);

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { getAuth, rolesFromToken, setAuth, setAuthFailureHandler, type StoredAuth } from '../api/client';
import { fetchMe, login as loginApi, logout as logoutApi, register as registerApi } from '../api/endpoints';

interface AuthContextValue {
  auth: StoredAuth | null;
  customer: StoredAuth['customer'] | null;
  roles: string[];
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authState, setAuthState] = useState<StoredAuth | null>(() => getAuth());
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const update = useCallback((next: StoredAuth | null) => {
    setAuth(next);
    setAuthState(next);
  }, []);

  // Keep the module-level store and React state in sync on forced logout (refresh failure).
  useEffect(() => {
    setAuthFailureHandler(() => {
      setAuth(null);
      setAuthState(null);
      queryClient.clear();
      navigate('/login', { replace: true });
    });
    return () => setAuthFailureHandler(null);
  }, [navigate, queryClient]);

  const login = useCallback(
    async (email: string, password: string) => {
      const tokens = await loginApi({ email, password });
      let customer = { id: '', email, status: 'active' };
      try {
        setAuth({ accessToken: tokens.accessToken, refreshToken: tokens.refreshToken, customer });
        customer = await fetchMe();
      } finally {
        update({ accessToken: tokens.accessToken, refreshToken: tokens.refreshToken, customer });
      }
      queryClient.clear();
    },
    [queryClient, update],
  );

  const register = useCallback(
    async (email: string, password: string) => {
      await registerApi({ email, password });
    },
    [],
  );

  const logout = useCallback(async () => {
    const current = getAuth();
    if (current) {
      try {
        await logoutApi(current.refreshToken);
      } catch {
        // Backend may already be unreachable; clear local state regardless.
      }
    }
    update(null);
    queryClient.clear();
  }, [queryClient, update]);

  const value = useMemo<AuthContextValue>(
    () => ({
      auth: authState,
      customer: authState?.customer ?? null,
      roles: rolesFromToken(authState?.accessToken),
      isAuthenticated: Boolean(authState?.accessToken),
      login,
      register,
      logout,
    }),
    [authState, login, logout, register],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

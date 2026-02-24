/**
 * Authentication provider components.
 *
 * Provides the appropriate auth provider based on configuration.
 */

import { type ReactNode, useCallback, useEffect, useState } from "react";
import { useAuth as useOidcAuth } from "react-oidc-context";
import { clearAuthTokenProvider, setAuthTokenProvider } from "./api";
import { isOidcConfigured } from "./auth-config";
import {
  AuthContext,
  type AuthContextValue,
  type AuthUser,
  LOCAL_USER,
} from "./auth-context";

const LOCAL_AUTH_KEY = "hivegent-local-auth";

/**
 * Provider component for local (non-OIDC) authentication.
 * Does NOT auto-login - user must click sign in.
 */
function LocalAuthProvider({ children }: { children: ReactNode }) {
  const [isLoading, setIsLoading] = useState(true);
  const [user, setUser] = useState<AuthUser | null>(null);

  // Check for existing session on mount
  useEffect(() => {
    const stored = localStorage.getItem(LOCAL_AUTH_KEY);
    if (stored) {
      try {
        const data = JSON.parse(stored);
        setUser(data);
      } catch {
        localStorage.removeItem(LOCAL_AUTH_KEY);
      }
    }
    setIsLoading(false);
  }, []);

  const signIn = useCallback(async () => {
    localStorage.setItem(LOCAL_AUTH_KEY, JSON.stringify(LOCAL_USER));
    setUser(LOCAL_USER);
  }, []);

  const signOut = useCallback(async () => {
    localStorage.removeItem(LOCAL_AUTH_KEY);
    setUser(null);
    clearAuthTokenProvider();
  }, []);

  const value: AuthContextValue = {
    isLoading,
    isAuthenticated: !!user,
    user,
    signIn,
    signOut,
    error: null,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/**
 * Provider component for OIDC authentication.
 */
function OidcAuthProviderWrapper({ children }: { children: ReactNode }) {
  const oidcAuth = useOidcAuth();

  // Set up token provider when authenticated
  useEffect(() => {
    if (oidcAuth.isAuthenticated && oidcAuth.user?.access_token) {
      setAuthTokenProvider(async () => {
        if (oidcAuth.user?.access_token) {
          return oidcAuth.user.access_token;
        }
        throw new Error("No access token available");
      });
    }
  }, [oidcAuth.isAuthenticated, oidcAuth.user?.access_token]);

  const signIn = useCallback(async () => {
    await oidcAuth.signinRedirect();
  }, [oidcAuth]);

  const signOut = useCallback(async () => {
    clearAuthTokenProvider();
    await oidcAuth.signoutRedirect();
  }, [oidcAuth]);

  const user: AuthUser | null = oidcAuth.user
    ? {
        id: oidcAuth.user.profile.sub,
        name:
          oidcAuth.user.profile.name ||
          oidcAuth.user.profile.preferred_username ||
          "User",
        email: oidcAuth.user.profile.email,
      }
    : null;

  const value: AuthContextValue = {
    isLoading: oidcAuth.isLoading,
    isAuthenticated: oidcAuth.isAuthenticated,
    user,
    signIn,
    signOut,
    error: oidcAuth.error || null,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/**
 * Unified auth provider that selects the appropriate implementation.
 */
export function UnifiedAuthProvider({ children }: { children: ReactNode }) {
  if (isOidcConfigured()) {
    return <OidcAuthProviderWrapper>{children}</OidcAuthProviderWrapper>;
  }
  return <LocalAuthProvider>{children}</LocalAuthProvider>;
}

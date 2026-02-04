/**
 * Authentication context and hook.
 *
 * This file contains only the context and hook - no components.
 * Provider components are in auth-providers.tsx.
 */

import { createContext, useContext } from 'react';

export interface AuthUser {
  id: string;
  name: string;
  email?: string;
}

export interface AuthContextValue {
  isLoading: boolean;
  isAuthenticated: boolean;
  user: AuthUser | null;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
  error: Error | null;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Hook to access the auth context.
 */
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}

// Hardcoded local development user
export const LOCAL_USER: AuthUser = {
  id: 'localhost',
  email: 'dev@localhost',
  name: 'Localhost User',
};

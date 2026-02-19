import type { ReactNode } from "react";
import { AuthProvider as OidcAuthProvider } from "react-oidc-context";

import { isOidcConfigured, oidcConfig } from "../lib/auth-config";
import { UnifiedAuthProvider } from "../lib/auth-providers";

interface AuthProviderProps {
  children: ReactNode;
}

/**
 * Authentication provider that wraps the app with the appropriate auth context.
 *
 * When OIDC is configured, wraps with the OIDC provider first.
 * Otherwise, uses local development authentication.
 */
export function AuthProvider({ children }: AuthProviderProps) {
  if (isOidcConfigured()) {
    return (
      <OidcAuthProvider {...oidcConfig}>
        <UnifiedAuthProvider>{children}</UnifiedAuthProvider>
      </OidcAuthProvider>
    );
  }

  return <UnifiedAuthProvider>{children}</UnifiedAuthProvider>;
}

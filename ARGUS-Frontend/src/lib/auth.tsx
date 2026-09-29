import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { clearSession, getStoredUser, getToken, login as apiLogin, storeSession } from "./api";
import type { LoginResult } from "./api";
import type { AuthUser, Role } from "../types";

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  /** Resolves with `null` when signed in, or the pending second-factor step. */
  login: (employeeId: string, password: string) => Promise<Extract<LoginResult, { kind: "mfa" }> | null>;
  /** Store a session obtained through the MFA flow. */
  completeSession: (token: string, user: AuthUser) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(() => (getToken() ? getStoredUser() : null));
  const [loading] = useState(false);

  const login = useCallback(async (employeeId: string, password: string) => {
    const result = await apiLogin(employeeId, password);
    if (result.kind === "mfa") return result;
    storeSession(result.token, result.user);
    setUser(result.user);
    return null;
  }, []);

  const completeSession = useCallback((token: string, loggedInUser: AuthUser) => {
    storeSession(token, loggedInUser);
    setUser(loggedInUser);
  }, []);

  const logout = useCallback(() => {
    clearSession();
    setUser(null);
  }, []);

  useEffect(() => {
    // Keep tabs in sync if a session is cleared elsewhere.
    function onStorage() {
      setUser(getToken() ? getStoredUser() : null);
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const value = useMemo(() => ({ user, loading, login, completeSession, logout }), [user, loading, login, completeSession, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function ProtectedRoute({ children, roles }: { children: ReactNode; roles?: Role[] }) {
  const { user } = useAuth();
  const location = useLocation();
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />;
  if (roles && !roles.includes(user.role)) return <Navigate to="/" replace />;
  return <>{children}</>;
}

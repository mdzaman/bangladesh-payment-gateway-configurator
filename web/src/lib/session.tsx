// Who is signed in, and what this deployment allows. Loaded once, kept in the
// react-query cache, and re-read after anything that changes it.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from "react";
import { ApiError, setCsrfToken, setUnauthenticatedHandler } from "../api/client";
import * as api from "../api/endpoints";
import type { Me, Platform } from "../api/types";

interface Session {
  me: Me | null;
  platform: Platform | undefined;
  loading: boolean;
  /** Call with the payload of a successful login or sign-up. */
  signedIn: (me: Me) => void;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
}

const SessionContext = createContext<Session | null>(null);

async function loadMe(): Promise<Me | null> {
  try {
    const me = await api.auth.me();
    setCsrfToken(me.csrf_token);
    return me;
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return null;
    throw e;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const meQuery = useQuery({ queryKey: ["me"], queryFn: loadMe, staleTime: 60_000 });
  const platformQuery = useQuery({ queryKey: ["platform"], queryFn: api.platform, staleTime: Infinity });

  useEffect(() => {
    // Any request that comes back 401 means the session ended: forget it.
    setUnauthenticatedHandler(() => queryClient.setQueryData(["me"], null));
    return () => setUnauthenticatedHandler(null);
  }, [queryClient]);

  const signedIn = useCallback((me: Me) => {
    setCsrfToken(me.csrf_token);
    queryClient.setQueryData(["me"], me);
  }, [queryClient]);

  const signOut = useCallback(async () => {
    try {
      await api.auth.logout();
    } finally {
      setCsrfToken("");
      queryClient.clear();
      queryClient.setQueryData(["me"], null);
    }
  }, [queryClient]);

  const refresh = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: ["me"] });
  }, [queryClient]);

  const value = useMemo<Session>(() => ({
    me: meQuery.data ?? null,
    platform: platformQuery.data,
    loading: meQuery.isPending,
    signedIn, signOut, refresh,
  }), [meQuery.data, meQuery.isPending, platformQuery.data, signedIn, signOut, refresh]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside SessionProvider");
  return session;
}

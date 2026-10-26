import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { ApiError, auth } from "./api";

// Who is signed in, and the gate in front of every page but /login.

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: auth.me, retry: false, staleTime: 60_000 });
}

export function RequireAuth() {
  const me = useMe();
  const loc = useLocation();
  if (me.isLoading) return <div className="auth-wait" aria-busy="true"><span className="status-dot live" /> Loading…</div>;
  if (me.error instanceof ApiError && me.error.code === "unauthenticated") {
    return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  }
  if (me.isError) return <div className="page"><div className="notice warn">{(me.error as Error).message}</div></div>;
  return <Outlet />;
}

export function useLogout() {
  const qc = useQueryClient();
  const nav = useNavigate();
  return useMutation({
    mutationFn: auth.logout,
    onSettled: () => {
      qc.clear();
      nav("/login", { replace: true });
    },
  });
}

export const initials = (name: string) =>
  name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("") || "?";

export function Avatar({ name, color, size = 32 }: { name: string; color: string; size?: number }) {
  return (
    <span className="avatar" style={{ background: color, width: size, height: size, fontSize: size * 0.4 }} aria-hidden>
      {initials(name)}
    </span>
  );
}

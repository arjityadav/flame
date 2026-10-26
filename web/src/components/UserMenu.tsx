import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Avatar, useLogout, useMe } from "../auth";

// The signed-in user's avatar in the top bar, opening Profile, History and Sign out.

export function UserMenu() {
  const me = useMe();
  const logout = useLogout();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);

  const u = me.data?.user;
  if (!u) return null;
  return (
    <div className="user-menu" ref={box}>
      <button className="user-btn" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open} aria-label={`Account: ${u.name}`}>
        <Avatar name={u.name} color={u.color} size={30} />
      </button>
      {open && (
        <div className="user-pop" role="menu">
          <div className="user-pop-head">
            <Avatar name={u.name} color={u.color} size={40} />
            <div>
              <b>{u.name}</b>
              <div className="small muted">{u.email}</div>
            </div>
          </div>
          <Link role="menuitem" to="/account">Profile and security</Link>
          <Link role="menuitem" to="/history">History</Link>
          <button role="menuitem" onClick={() => logout.mutate()} disabled={logout.isPending}>Sign out</button>
        </div>
      )}
    </div>
  );
}

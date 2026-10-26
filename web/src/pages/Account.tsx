import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { auth } from "../api";
import { Avatar, useLogout, useMe } from "../auth";

// The signed-in user's profile, password, devices and activity counts.

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—");

function device(agent: string) {
  const browser = /Edg\//.test(agent) ? "Edge" : /Chrome\//.test(agent) ? "Chrome" : /Firefox\//.test(agent) ? "Firefox" : /Safari\//.test(agent) ? "Safari" : "Browser";
  const os = /iPhone|iPad/.test(agent) ? "iOS" : /Android/.test(agent) ? "Android" : /Mac OS X/.test(agent) ? "macOS" : /Windows/.test(agent) ? "Windows" : /Linux/.test(agent) ? "Linux" : "";
  return os ? `${browser} on ${os}` : browser;
}

export function Account() {
  const me = useMe();
  const qc = useQueryClient();
  const config = useQuery({ queryKey: ["auth-config"], queryFn: auth.config });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: auth.sessions });
  const logout = useLogout();
  const u = me.data?.user;
  const [form, setForm] = useState({ name: "", organisation: "", role: "", color: "" });
  useEffect(() => { if (u) setForm({ name: u.name, organisation: u.organisation, role: u.role, color: u.color }); }, [u]);
  const dirty = !!u && (form.name !== u.name || form.organisation !== u.organisation || form.role !== u.role || form.color !== u.color);

  useEffect(() => { qc.invalidateQueries({ queryKey: ["me"] }); }, [qc]); // fresh counts each time the page opens
  const save = useMutation({
    mutationFn: () => auth.update(Object.fromEntries(Object.entries(form).filter(([k, v]) => u![k as keyof typeof form] !== v))),
    onSuccess: (d) => qc.setQueryData(["me"], { ...me.data!, user: d.user }),
  });
  const [pw, setPw] = useState({ current: "", next: "", again: "" });
  const changePw = useMutation({
    mutationFn: () => auth.password({ current: pw.current, new: pw.next }),
    onSuccess: () => { setPw({ current: "", next: "", again: "" }); qc.invalidateQueries({ queryKey: ["sessions"] }); },
  });
  const endOthers = useMutation({ mutationFn: auth.endOthers, onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }) });

  if (!u || !me.data) return <div className="page"><div className="skeleton" style={{ height: 400 }} /></div>;
  const st = me.data.stats;
  const pwMismatch = pw.again.length > 0 && pw.again !== pw.next;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Profile</h1>
          <p>Your details, password and the devices you're signed in on.</p>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={() => logout.mutate()}>Sign out</button>
      </div>

      <div className="acct-hero card">
        <Avatar name={form.name || u.name} color={form.color || u.color} size={72} />
        <div className="acct-hero-text">
          <h2>{u.name}</h2>
          <div className="muted">{u.email}{u.is_admin && <span className="chip" style={{ marginLeft: 8 }}>admin</span>}</div>
          <div className="small muted">Member since {when(u.created)} · last sign-in {when(u.last_login)}</div>
        </div>
        <div className="acct-stats">
          <Link to="/history?type=run_created"><b>{st.analyses}</b><span>analyses</span></Link>
          <Link to="/history?type=ask"><b>{st.questions}</b><span>questions</span></Link>
          <Link to="/history?type=export"><b>{st.exports}</b><span>exports</span></Link>
          <Link to="/history?type=login,signup"><b>{st.sign_ins}</b><span>sign-ins</span></Link>
        </div>
      </div>

      <div className="acct-grid">
        <form className="card acct-card" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
          <h3>Details</h3>
          <label className="field"><span>Name</span><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required /></label>
          <label className="field"><span>Email</span><input className="input" value={u.email} disabled /><small className="muted">Your email is your sign-in and can't be changed here.</small></label>
          <label className="field"><span>Organisation</span><input className="input" value={form.organisation} onChange={(e) => setForm({ ...form, organisation: e.target.value })} placeholder="e.g. team-08" /></label>
          <label className="field"><span>Role</span><input className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} placeholder="e.g. Homologation engineer" /></label>
          <div className="field"><span>Avatar colour</span>
            <div className="swatches" role="radiogroup" aria-label="Avatar colour">
              {(config.data?.colors ?? []).map((c) => (
                <button key={c} type="button" role="radio" aria-checked={form.color === c} aria-label={c} className={form.color === c ? "on" : ""}
                  style={{ background: c }} onClick={() => setForm({ ...form, color: c })} />
              ))}
            </div>
          </div>
          {save.isError && <div className="notice warn">{(save.error as Error).message}</div>}
          <div className="acct-actions">
            {save.isSuccess && !dirty && <span className="small ok-text">Saved</span>}
            <button className="btn btn-primary btn-sm" disabled={!dirty || save.isPending}>{save.isPending ? "Saving…" : "Save changes"}</button>
          </div>
        </form>

        <div className="acct-col">
          <form className="card acct-card" onSubmit={(e: FormEvent) => { e.preventDefault(); changePw.mutate(); }}>
            <h3>Password</h3>
            <label className="field"><span>Current password</span><input className="input" type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></label>
            <label className="field"><span>New password</span><input className="input" type="password" autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} /><small className="muted">At least {config.data?.min_password ?? 8} characters. Your other devices are signed out.</small></label>
            <label className="field"><span>Repeat new password</span><input className="input" type="password" autoComplete="new-password" value={pw.again} onChange={(e) => setPw({ ...pw, again: e.target.value })} aria-invalid={pwMismatch} />{pwMismatch && <small className="bad-text">The two don't match.</small>}</label>
            {changePw.isError && <div className="notice warn">{(changePw.error as Error).message}</div>}
            <div className="acct-actions">
              {changePw.isSuccess && <span className="small ok-text">Password changed</span>}
              <button className="btn btn-primary btn-sm" disabled={!pw.current || !pw.next || pw.next !== pw.again || changePw.isPending}>Change password</button>
            </div>
          </form>

          <div className="card acct-card">
            <h3>Signed-in devices</h3>
            <ul className="acct-sessions">
              {(sessions.data ?? []).map((s) => (
                <li key={s.id}>
                  <div><b>{device(s.agent)}</b>{s.current && <span className="chip" data-a="yes" style={{ marginLeft: 8 }}>this device</span>}</div>
                  <div className="small muted">{s.ip || "unknown address"} · signed in {when(s.created)} · last active {when(s.last_seen)}</div>
                </li>
              ))}
            </ul>
            <div className="acct-actions">
              {endOthers.isSuccess && <span className="small ok-text">Signed out of {endOthers.data.ended} other device{endOthers.data.ended === 1 ? "" : "s"}</span>}
              <button className="btn btn-secondary btn-sm" onClick={() => endOthers.mutate()} disabled={(sessions.data?.length ?? 0) < 2 || endOthers.isPending}>Sign out other devices</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

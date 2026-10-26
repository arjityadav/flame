import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { auth } from "../api";
import * as I from "../components/Icons";
import { ThemeToggle } from "../components/ThemeToggle";
import { useMe } from "../auth";

// Sign in, or create an account (with the team's access code when the server asks for one).

type Mode = "signin" | "signup";

function strength(pw: string) {
  let s = 0;
  if (pw.length >= 8) s++;
  if (pw.length >= 12) s++;
  if (/[A-Z]/.test(pw) && /[a-z]/.test(pw)) s++;
  if (/\d/.test(pw)) s++;
  if (/[^A-Za-z0-9]/.test(pw)) s++;
  return Math.min(4, s);
}
const STRENGTH = ["Too short", "Weak", "Fair", "Good", "Strong"];

export function Login() {
  const [search] = useSearchParams();
  const next = search.get("next")?.startsWith("/") && !search.get("next")!.startsWith("//") ? search.get("next")! : "/runs";
  const nav = useNavigate();
  const qc = useQueryClient();
  const me = useMe();
  const config = useQuery({ queryKey: ["auth-config"], queryFn: auth.config });
  const [mode, setMode] = useState<Mode>(search.get("mode") === "signup" ? "signup" : "signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [remember, setRemember] = useState(true);
  const [show, setShow] = useState(false);

  useEffect(() => { if (me.data) nav(next, { replace: true }); }, [me.data, nav, next]);
  useEffect(() => { if (config.data && !config.data.has_users) setMode("signup"); }, [config.data]);

  const submit = useMutation({
    mutationFn: () => (mode === "signin" ? auth.login({ email, password, remember }) : auth.register({ email, password, name, code, remember })),
    onSuccess: () => {
      qc.removeQueries({ queryKey: ["me"] });
      nav(next, { replace: true });
    },
  });
  const onSubmit = (e: FormEvent) => { e.preventDefault(); submit.mutate(); };
  const s = strength(password);
  const min = config.data?.min_password ?? 8;
  const ready = email && password && (mode === "signin" || (name && password.length >= min && (!config.data?.code_required || code)));

  return (
    <div className="login">
      <aside className="login-art">
        <Link to="/" className="brand login-brand" aria-label="Flame home"><I.Flame size={26} /> Flame</Link>
        <div className="login-pitch">
          <h1>Every UN requirement for your part, cited to the page.</h1>
          <p>Agents read your specification and exploded view, pick the regulations that apply and list what the design must meet.</p>
        </div>
        <div className="login-foot small">Industrial Agents Hackathon · Munich 2026 · team-08</div>
      </aside>

      <main className="login-main">
        <div className="login-top"><ThemeToggle /></div>
        <form className="login-card" onSubmit={onSubmit} noValidate>
          <h2>{mode === "signin" ? "Welcome back" : "Create your account"}</h2>
          <p className="muted small">{mode === "signin" ? "Sign in to open your analyses." : config.data && !config.data.has_users
            ? "You're the first one here: this account becomes the admin." : "It takes a few seconds."}</p>

          <div className="seg login-tabs" role="tablist">
            <button type="button" role="tab" aria-selected={mode === "signin"} className={mode === "signin" ? "on" : ""} onClick={() => { setMode("signin"); submit.reset(); }}>Sign in</button>
            <button type="button" role="tab" aria-selected={mode === "signup"} className={mode === "signup" ? "on" : ""} onClick={() => { setMode("signup"); submit.reset(); }}>Create account</button>
          </div>

          {mode === "signup" && (
            <label className="field"><span>Name</span>
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" placeholder="Ada Lovelace" required />
            </label>
          )}
          <label className="field"><span>Email</span>
            <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" placeholder="you@company.com" required autoFocus />
          </label>
          <label className="field"><span>Password</span>
            <span className="field-pw">
              <input className="input" type={show ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === "signin" ? "current-password" : "new-password"} required minLength={mode === "signup" ? min : undefined} />
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShow((v) => !v)} aria-label={show ? "Hide password" : "Show password"}>{show ? "Hide" : "Show"}</button>
            </span>
          </label>
          {mode === "signup" && password && (
            <div className="pw-meter" data-s={s} aria-live="polite">
              <span><i style={{ width: `${(s / 4) * 100}%` }} /></span>
              <small>{password.length < min ? `At least ${min} characters` : STRENGTH[s]}</small>
            </div>
          )}
          {mode === "signup" && config.data?.code_required && (
            <label className="field"><span>Access code</span>
              <input className="input" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" placeholder="From your team" required />
              <small className="muted">The regulation data is licensed for the hackathon, so accounts need the team's code.</small>
            </label>
          )}
          <label className="check">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
            <span>Keep me signed in for 14 days</span>
          </label>

          {submit.isError && <div className="notice warn" role="alert">{(submit.error as Error).message}</div>}
          <button className="btn btn-primary login-submit" disabled={!ready || submit.isPending}>
            {submit.isPending ? "One moment…" : mode === "signin" ? "Sign in" : "Create account"}
          </button>
          {mode === "signin" && <p className="small muted login-help">Forgot your password? Ask your team's admin to set up a new account for you; there's no email reset on this server.</p>}
        </form>
      </main>
    </div>
  );
}

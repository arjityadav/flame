import { useEffect, useState } from "react";
import * as I from "./Icons";

// Light / dark / follow the system. The choice is kept in localStorage and applied as <html data-theme>; index.html
// applies it before the first paint.

type Theme = "auto" | "light" | "dark";
const NEXT: Record<Theme, Theme> = { auto: "light", light: "dark", dark: "auto" };
const LABEL: Record<Theme, string> = { auto: "Theme: follows the system", light: "Theme: light", dark: "Theme: dark" };

function saved(): Theme {
  try {
    const t = localStorage.getItem("flame-theme");
    return t === "light" || t === "dark" ? t : "auto";
  } catch {
    return "auto";
  }
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(saved);
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "auto") delete root.dataset.theme;
    else root.dataset.theme = theme;
    try {
      if (theme === "auto") localStorage.removeItem("flame-theme");
      else localStorage.setItem("flame-theme", theme);
    } catch {
      /* private mode: the choice lasts for this page only */
    }
  }, [theme]);
  return (
    <button className="btn btn-ghost btn-icon btn-sm theme-toggle" onClick={() => setTheme(NEXT[theme])}
      title={`${LABEL[theme]} (click to change)`} aria-label={`${LABEL[theme]}; switch to ${NEXT[theme] === "auto" ? "system" : NEXT[theme]}`}>
      {theme === "dark" ? <I.Moon size={18} /> : theme === "light" ? <I.Sun size={18} /> : <I.Auto size={18} />}
    </button>
  );
}

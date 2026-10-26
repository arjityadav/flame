// Word-level diff (longest common subsequence over words) for showing what a regulation amendment changed.

export type Seg = { text: string; op: "same" | "del" | "ins" };

const norm = (w: string) => w.toLowerCase().replace(/[“”"]/g, '"').replace(/[‘’]/g, "'");

export function wordDiff(a: string, b: string): Seg[] {
  const x = a.split(/\s+/).filter(Boolean);
  const y = b.split(/\s+/).filter(Boolean);
  const n = x.length, m = y.length;
  // lcs[i][j] = LCS length of x[i:] and y[j:]
  const lcs = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      lcs[i][j] = norm(x[i]) === norm(y[j]) ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  const out: Seg[] = [];
  const push = (text: string, op: Seg["op"]) => {
    const last = out[out.length - 1];
    if (last && last.op === op) last.text += " " + text;
    else out.push({ text, op });
  };
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (norm(x[i]) === norm(y[j])) { push(y[j], "same"); i++; j++; }
    else if (lcs[i + 1][j] >= lcs[i][j + 1]) push(x[i++], "del");
    else push(y[j++], "ins");
  }
  while (i < n) push(x[i++], "del");
  while (j < m) push(y[j++], "ins");
  return out;
}

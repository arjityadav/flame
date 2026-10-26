// Typed access to the Flame backend (app/main.py). Shapes follow docs/ui/LLD.md §2.

export type Stage = { key: string; label: string; status: "pending" | "running" | "done" | "failed"; detail: string; seconds: number | null };

export type Run = {
  id: string;
  title: string;
  created: string;
  status: "queued" | "running" | "done" | "failed";
  stage: string | null;
  preloaded: boolean;
  error: string | null;
  cost_usd: number;
  counts: { requirements?: number; regulations?: number };
  stages: Stage[];
};

export type Source = { regulation: string; version: string; page: number; paragraph?: string };

export type Requirement = {
  id: string;
  priority: number;
  text: string;
  summary: string;
  source: Source;
  node: string | null;
  severity: 1 | 2 | 3 | 4 | 5;
  severity_label: string;
  severity_reason: string;
  phase: 1 | 2 | 3 | 4 | 5;
  phase_label: string;
  phase_reason: string;
  rated_by: "model" | "rules";
  applies_to?: string | null;
  test_case?: string | null;
  kind?: string | null;
  regulation_title?: string;
};

export type Box = { page: number; bbox: [number, number, number, number] };

export type Paragraph = {
  node: string;
  regulation: string;
  version: string;
  role: string;
  label: string;
  section: string;
  pages: number[];
  text: string;
  boxes: Box[];
  pdf: string | null;
  pdf_sha256: string | null;
  title: string;
  definitions: { term: string; node: string; label: string }[];
  references: { node: string; label: string; regulation: string }[];
};

export type Citation = { tag: string; node: string; regulation: string; version: string; label: string; page: number };

export type AskFinal = { answer: string; spoken: string; citations: Citation[]; not_in_sources: boolean; offline?: boolean };

export type GraphNode = {
  id: string;
  kind: "regulation" | "section" | "paragraph";
  label: string;
  regulation: string;
  title?: string;
  page?: number;
  x: number;
  y: number;
  requirement_ids?: string[];
  severity?: number;
  phase?: number;
  summaries?: string[];
  priority?: number;
  excerpt?: string;
};
export type GraphEdge = { source: string; target: string; kind: "child" | "refers" | "cites_reg" | "similar"; weight?: number };
export type GraphPayload = { view: string; regulations: string[]; nodes: GraphNode[]; edges: GraphEdge[]; centroids?: Record<string, [number, number]> };

export type Profile = {
  profile: null | {
    product_name: string;
    product_type: string;
    vehicle_categories: string[];
    mounting_location: string;
    functions: { name: string; description: string }[];
    parts: { name: string; role: string; material: string; electrical: boolean }[];
    electronics: string[];
    vehicle_interfaces: string[];
    drawing_observations: string[];
    uncertainties: string[];
  };
  regulations: { selected: string[]; rows: {
    regulation: string; title: string; selected: boolean; superseded_by: string | null; reason: string;
    applies?: "yes" | "maybe" | "no"; basis?: string; functions_covered?: string[]; scope_evidence?: string;
  }[] };
  inputs: string[];
};

export type Status = { model: boolean; voice: boolean; busy: string | null };

/** A part of the exploded-view drawing (app/parts.py): boxes are 0-1000 of the image, crops are pixels. */
export type DrawnPart = {
  name: string;
  profile_part: string;
  box: [number, number, number, number];
  boxes?: [number, number, number, number][];
  depth: number;
  callout: string;
  requirements: string[];
};
export type PartsData = {
  axis: [number, number];
  image: string;
  size: [number, number];
  crops: [number, number, number, number][];
  parts: DrawnPart[];
};

export class ApiError extends Error {
  constructor(message: string, public code?: string) {
    super(message);
  }
}

export type AgentStep = {
  key: string; name: string; kind: "agent" | "code"; role: string; model: string; calls: number | null;
  status: "pending" | "running" | "done" | "failed"; seconds: number | null; summary: string; handoff: string;
  details: Record<string, any>;
};
export type Journey = {
  i: number; regulation: string; paragraph: string; page: number | null; text: string; evidence: string;
  section: string; section_reason: string; kind: string | null; score: number | null; id: string | null;
  summary?: string | null; duplicate_of: string | null; stopped: null | "grounding" | "critic" | "ranker" | "dedup";
};
export type ChangeKind = "modified" | "removed" | "added";
export type ChangePara = { paragraph: string | null; page: number | null; text: string | null };
export type Change = {
  change: ChangeKind; requirement_id: string | null; old_requirement: string | null; new_text: string | null;
  reason: string; edits: ({ added: string } | { removed: string } | { from: string; to: string })[];
  old?: ChangePara | null; new?: ChangePara | null; verdict?: "correct" | "wrong_type" | "extra"; expected_text?: string | null; parts?: string[];
};
export type ChangeCase = { case: string; case_id: string; requirements: number; has_expected: boolean };
export type ChangeAnalysis = {
  case: string; case_id: string; regulation: string; old_version: string; new_version: string;
  counts: Record<ChangeKind, number>; requirements_old: number; changes: Change[];
  check: null | { recall: number; precision: number; type_correct: number; flagged: number; expected: number; missed: Change[] };
};
export type Src = { regulation: string; version: string; page: number; paragraph: string };
export type Level = { precision: number; recall: number; f2: number };
export type Accuracy = {
  run: string; case: string | null; regulation_level: Level; page_level: Level;
  paragraph_level: Level & { matched: number; submitted: number; expected: number };
  found: number; n_expected: number; n_ours: number;
  per_regulation: { regulation: string; expected: number; found: number; ours: number; ours_on_expected: number }[];
  expected: { id: string; text: string; source: Src; found: boolean; similarity: number | null; closest: string | null; ours: string[] }[];
  ours: { id: string; text: string; summary: string | null; source: Src; on_expected: boolean }[];
};
export type Upgrade = { run: string; regulations: ({ regulation: string; base: string | null; latest: string | null } & Partial<{
  pdf: { old: string; new: string }; counts: Record<ChangeKind, number>; requirements_old: number; changes: Change[] }>)[] };
export type User = {
  id: number; email: string; name: string; organisation: string; role: string; color: string; is_admin: boolean;
  created: string; last_login: string | null;
};
export type Me = { user: User; stats: { analyses: number; questions: number; exports: number; sign_ins: number } };
export type AuthConfig = { code_required: boolean; has_users: boolean; min_password: number; colors: string[] };
export type Session = { id: string; created: string; last_seen: string | null; agent: string; ip: string; current: boolean };
export type HistoryEvent = { id: number; ts: string; type: string; run_id: string | null; run_title: string | null; detail: Record<string, any> };

const send = (method: string, body?: unknown) => ({
  method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body),
});
export const auth = {
  config: () => fetch("/api/auth/config").then((r) => json<AuthConfig>(r)),
  me: () => fetch("/api/auth/me").then((r) => json<Me>(r)),
  login: (b: { email: string; password: string; remember: boolean }) => fetch("/api/auth/login", send("POST", b)).then((r) => json<{ user: User }>(r)),
  register: (b: { email: string; password: string; name: string; code: string; remember: boolean }) =>
    fetch("/api/auth/register", send("POST", b)).then((r) => json<{ user: User }>(r)),
  logout: () => fetch("/api/auth/logout", send("POST")).then((r) => json<{ ok: boolean }>(r)),
  update: (b: Partial<Pick<User, "name" | "organisation" | "role" | "color">>) => fetch("/api/auth/me", send("PATCH", b)).then((r) => json<{ user: User }>(r)),
  password: (b: { current: string; new: string }) => fetch("/api/auth/password", send("POST", b)).then((r) => json<{ ok: boolean }>(r)),
  sessions: () => fetch("/api/auth/sessions").then((r) => json<Session[]>(r)),
  endOthers: () => fetch("/api/auth/sessions", send("DELETE")).then((r) => json<{ ended: number }>(r)),
  history: (type: string, before?: number) =>
    fetch(`/api/me/history?limit=50${type ? `&type=${type}` : ""}${before ? `&before=${before}` : ""}`).then((r) => json<{ events: HistoryEvent[]; next: number | null }>(r)),
  clearHistory: () => fetch("/api/me/history", send("DELETE")).then((r) => json<{ deleted: number }>(r)),
  myRuns: () => fetch("/api/me/runs").then((r) => json<Run[]>(r)),
};

export type Trace = { run: string; status: string; title: string; cost_usd: number | null; agents: AgentStep[] };

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    let code: string | undefined;
    try {
      const body = await res.json();
      message = body?.error?.message ?? message;
      code = body?.error?.code;
    } catch {
      /* not JSON */
    }
    if (res.status === 401 && code === "unauthenticated" && !location.pathname.startsWith("/login")) {
      location.assign(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
    }
    throw new ApiError(message, code);
  }
  return res.json() as Promise<T>;
}

export const api = {
  status: () => fetch("/api/status").then((r) => json<Status>(r)),
  runs: () => fetch("/api/runs").then((r) => json<Run[]>(r)),
  run: (id: string) => fetch(`/api/runs/${id}`).then((r) => json<Run>(r)),
  requirements: (id: string) => fetch(`/api/runs/${id}/requirements`).then((r) => json<Requirement[]>(r)),
  profile: (id: string) => fetch(`/api/runs/${id}/profile`).then((r) => json<Profile>(r)),
  paragraph: (node: string) => fetch(`/api/paragraphs/${encodeURIComponent(node)}`).then((r) => json<Paragraph>(r)),
  graph: (id: string, view: "structure" | "meaning") => fetch(`/api/runs/${id}/graph?view=${view}`).then((r) => json<GraphPayload>(r)),
  graphChat: (id: string, question: string, selection: string | null) =>
    fetch(`/api/runs/${id}/graph/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, selection }),
    }).then((r) => json<{ answer: string; highlight: string[]; focus: string | null; offline?: boolean }>(r)),
  journeys: (id: string) => fetch(`/api/runs/${id}/trace/journeys`).then((r) => json<{ rank_min: number; items: Journey[] }>(r)),
  changeCases: () => fetch("/api/changes").then((r) => json<ChangeCase[]>(r)),
  changes: (c: string) => fetch(`/api/changes/${c}`).then((r) => json<ChangeAnalysis>(r)),
  accuracy: (id: string) => fetch(`/api/runs/${id}/accuracy`).then((r) => json<Accuracy>(r)),
  upgrade: (id: string) => fetch(`/api/runs/${id}/upgrade`).then((r) => json<Upgrade>(r)),
  trace: (id: string) => fetch(`/api/runs/${id}/trace`).then((r) => json<Trace>(r)),
  parts: (id: string) => fetch(`/api/runs/${id}/parts`).then((r) => json<PartsData>(r)),
  detectParts: (id: string) => fetch(`/api/runs/${id}/parts/detect`, { method: "POST" }).then((r) => json<PartsData>(r)),
  createRun: (form: FormData) => fetch("/api/runs", { method: "POST", body: form }).then((r) => json<Run>(r)),
};

/** Parse a Server-Sent Events stream from a fetch response (used for POST /ask, which EventSource can't send). */
export async function* sse(res: Response): AsyncGenerator<{ event: string; data: any }> {
  if (!res.ok || !res.body) {
    await json(res);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = "message";
      let data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) yield { event, data: JSON.parse(data) };
    }
  }
}

export function ask(runId: string, question: string, focus: string | null, history: { role: string; content: string }[]) {
  return fetch(`/api/runs/${runId}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, focus, history }),
  });
}

export const PHASES: Record<number, string> = {
  1: "Concept and architecture",
  2: "Detailed design",
  3: "Design verification",
  4: "Type approval",
  5: "Series production",
};
export const PHASE_HINT: Record<number, string> = {
  1: "Decide before the part is packaged on the vehicle",
  2: "Get right before design freeze",
  3: "Pass in testing before submitting for approval",
  4: "Needed for the approval itself",
  5: "Keep true in production",
};
export const SEVERITY: Record<number, string> = {
  5: "Safety-critical",
  4: "Blocks approval",
  3: "Installation",
  2: "Marking",
  1: "Optional",
};

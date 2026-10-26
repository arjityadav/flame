import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { SeverityChip } from "../components/Chips";
import * as I from "../components/Icons";
import { ThemeToggle } from "../components/ThemeToggle";
import { UserMenu } from "../components/UserMenu";

// Measured on the dev case (rear lamp) by Certivity's platform: upload v6b, 24 Oct (see result.md).
const MEASURED = [
  { v: "4 of 4", k: "regulations chosen correctly, out of 56" },
  { v: "94%", k: "of the expert's cited pages found" },
  { v: "71%", k: "of the expert's requirements found" },
  { v: "100%", k: "of requirements traceable to a PDF paragraph" },
];

const AGENTS = [
  ["01", "Product analyst", "Reads the spec and the exploded view, and lists functions, parts and electronics in the regulations' own terms."],
  ["02", "Applicability", "Checks all 56 UN Regulations against the part, one by one, quoting each scope."],
  ["03", "Reconciler", "Keeps the current regulation where older ones were replaced, and adds installation and EMC rules."],
  ["04", "Section selector", "Picks the sections and annexes that hold obligations for this part."],
  ["05", "Extractors", "Turn regulation text into one-obligation requirements, close to the original wording."],
  ["06", "Verifier and critic", "Check every citation against the PDF and drop what doesn't apply or repeats."],
  ["07", "Prioritiser", "Writes each requirement in plain words and orders the list by when it must be met."],
];

function Receipt() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const run = runs.data?.find((r) => r.status === "done");
  const reqs = useQuery({ queryKey: ["requirements", run?.id], queryFn: () => api.requirements(run!.id), enabled: !!run });
  const r = reqs.data?.find((x) => x.severity >= 4 && x.node) ?? reqs.data?.[0];
  const para = useQuery({ queryKey: ["paragraph", r?.node], queryFn: () => api.paragraph(r!.node!), enabled: !!r?.node });
  if (!r) return null;
  return (
    <div className="receipt" aria-label="Citation receipt">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
        <b style={{ fontWeight: 500 }}>Citation receipt</b>
        <SeverityChip s={r.severity} />
      </div>
      <p style={{ marginTop: 12 }}>{r.summary}</p>
      <dl>
        <dt>Regulation</dt>
        <dd>{r.source.regulation}, version {r.source.version}</dd>
        <dt>Paragraph</dt>
        <dd>§{r.source.paragraph}</dd>
        <dt>Page</dt>
        <dd>{r.source.page}</dd>
        <dt>Highlighted lines</dt>
        <dd>{para.data ? `${para.data.boxes.length} lines on page ${para.data.pages.join(", ")}` : "…"}</dd>
        <dt>PDF SHA-256</dt>
        <dd>{para.data?.pdf_sha256 ?? "…"}</dd>
      </dl>
      {run && (
        <Link className="btn btn-secondary btn-sm" style={{ marginTop: 16 }} to={`/runs/${run.id}?r=${r.id}`}>
          Open it in the PDF
        </Link>
      )}
    </div>
  );
}

export function Landing() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const example = runs.data?.find((r) => r.status === "done");
  const preview = useQuery({ queryKey: ["requirements", example?.id], queryFn: () => api.requirements(example!.id), enabled: !!example });

  return (
    <div className="landing">
      <nav className="land-nav" aria-label="Site">
        <Link to="/" className="brand" style={{ padding: 0 }} aria-label="Flame home">
          <I.Flame /> Flame
        </Link>
        <div className="links">
          <a href="#agents">How it works</a>
          <a href="#results">Results</a>
          <a href="#proof">Proof</a>
        </div>
        <Link className="btn btn-ghost btn-sm" to="/runs">
          Runs
        </Link>
        <Link className="btn btn-primary btn-sm" to="/new">
          Analyse a part
        </Link>
        <ThemeToggle />
        <UserMenu />
      </nav>

      <section className="hero">
        <div>
          <h1>
            Drop in a part.
            <br />
            Get every UN requirement, cited to the page.
          </h1>
          <p className="lead">
            Flame reads your specification and exploded view, picks the UN Regulations that apply, and returns a short, prioritised list of what your design must meet. Every line opens the exact paragraph in the regulation.
          </p>
          <div className="cta">
            <Link className="btn btn-primary" to="/new">
              Analyse a part
            </Link>
            {example && (
              <Link className="btn btn-secondary" to={`/runs/${example.id}`}>
                Open the rear-lamp example
              </Link>
            )}
          </div>
          <div className="tags">
            <span className="chip">56 UN Regulations</span>
            <span className="chip">Cited to page and paragraph</span>
          </div>
        </div>
        <div className="hero-art">{example && <img src={`/api/runs/${example.id}/input/exploded_view.png`} alt="Exploded view of the rear-lamp example" />}</div>
      </section>

      <section className="section">
        <div className="kicker">The app</div>
        <h2>What your engineers see.</h2>
        <p className="sub">Requirements ordered by when they must be met. Severity on every line. One click to the source.</p>
        <div className="preview" aria-hidden>
          <div className="chrome">
            <span />
            <span />
            <span />
            <span className="url">flame · {example?.title ?? "analysis"}</span>
          </div>
          <div className="body">
            {(preview.data ?? []).slice(0, 5).map((r) => (
              <div key={r.id} className="card" style={{ display: "grid", gridTemplateColumns: "40px 1fr auto", gap: 12, alignItems: "center", padding: "14px 16px" }}>
                <span className="mono muted">#{r.priority}</span>
                <span>{r.summary}</span>
                <SeverityChip s={r.severity} label={false} />
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="section" id="agents">
        <div className="kicker">Seven agents</div>
        <h2>Agents do the reading. Code keeps them honest.</h2>
        <p className="sub">
          Plain code runs the agents in a fixed order and checks every citation against the PDF. The model never writes a page number.
        </p>
        <div className="agents">
          {AGENTS.map(([n, h, p]) => (
            <div className="agent" key={n}>
              <div className="n">{n}</div>
              <h3>{h}</h3>
              <p>{p}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="section" id="results" style={{ background: "var(--ground)", maxWidth: "none" }}>
        <div style={{ maxWidth: 1280, margin: "0 auto" }}>
          <div className="kicker">Measured</div>
          <h2>Scored against Certivity's expert answer.</h2>
          <p className="sub">On the worked example, a vehicle rear lamp, scored by Certivity's platform.</p>
          <div className="metrics">
            {MEASURED.map((m) => (
              <div className="metric" key={m.k}>
                <div className="v">{m.v}</div>
                <div className="k">{m.k}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="section">
        <div className="kicker">How it works</div>
        <h2>Three steps.</h2>
        <div className="steps3">
          <div>
            <div className="n">1</div>
            <h3>Upload</h3>
            <p>The specification and the exploded-view drawing, as they are.</p>
          </div>
          <div>
            <div className="n">2</div>
            <h3>Wait five minutes</h3>
            <p>Watch the agents choose regulations, read sections and check each citation.</p>
          </div>
          <div>
            <div className="n">3</div>
            <h3>Work the list</h3>
            <p>Start at the top: what must be settled at concept stage, then design, testing, approval and production.</p>
          </div>
        </div>
      </section>

      <section className="section" id="proof">
        <div className="kicker">Proof</div>
        <h2>Every requirement leaves a receipt.</h2>
        <p className="sub">Regulation, version, page, paragraph, and the PDF's checksum, so anyone can verify the source.</p>
        <Receipt />
      </section>

      <footer className="footer">
        <span>Flame · Built for the Industrial Agents Hackathon, Munich 2026</span>
        {/* <span>Regulation texts © UNECE, rendered by Certivity for the hackathon only.</span> */}
      </footer>
    </div>
  );
}

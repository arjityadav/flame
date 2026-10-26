import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Profile as ProfileData } from "../api";

// What the product analyst understood from the specification and drawing, and why each regulation is in or out.

type Row = ProfileData["regulations"]["rows"][number];
const BASIS: Record<string, string> = {
  device_approval: "Device approval", installation_on_vehicle: "Installation on the vehicle",
  component_technology: "Component technology", vehicle_system: "Vehicle system", none: "—",
};

function More({ items, n = 3 }: { items: string[]; n?: number }) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, n);
  return (
    <>
      <ul className="pp-bullets">{shown.map((o) => <li key={o}>{o}</li>)}</ul>
      {items.length > n && (
        <button className="btn btn-ghost btn-sm pp-more" onClick={() => setAll((a) => !a)}>
          {all ? "Show less" : `Show all ${items.length}`}
        </button>
      )}
    </>
  );
}

export function Profile() {
  const { runId } = useParams();
  const data = useQuery({ queryKey: ["profile", runId], queryFn: () => api.profile(runId!) });
  const p = data.data?.profile;
  const rows = data.data?.regulations.rows ?? [];
  const drawing = data.data?.inputs.find((n) => /^exploded_view\.(png|jpe?g)$/i.test(n));

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Product profile</h1>
          <p>What the product analyst understood from the specification and drawing, and why each regulation was chosen or left out.</p>
        </div>
      </div>
      {data.isLoading && <div className="skeleton" style={{ height: 300 }} />}
      {data.isError && <div className="notice warn">{(data.error as Error).message}</div>}
      {data.data && !p && <div className="notice">The product analyst has not finished yet.</div>}

      {p && (
        <>
          <section className="card pp-hero">
            <div className="pp-hero-text">
              <div className="small muted">Product</div>
              <h2>{p.product_name}</h2>
              <p className="pp-lead">{p.product_type}</p>
              <div className="pp-facts">
                <div><b>{p.vehicle_categories.join(", ") || "—"}</b><span>vehicle category</span></div>
                <div><b>{p.functions.length}</b><span>functions</span></div>
                <div><b>{p.parts.length}</b><span>parts</span></div>
                <div><b>{p.electronics.length}</b><span>electronic items</span></div>
              </div>
              <div className="pp-mount"><span className="small muted">Mounted</span><p>{p.mounting_location}</p></div>
            </div>
            {drawing && (
              <figure className="pp-drawing">
                <img className="drawing" src={`/api/runs/${runId}/input/${drawing}`} alt="Exploded-view drawing of the part" />
                <figcaption className="small muted">Exploded view as uploaded · <Link to={`/runs/${runId}/3d`}>open in 3D</Link></figcaption>
              </figure>
            )}
          </section>

          <h3 className="pp-h">Functions</h3>
          <div className="pp-functions">
            {p.functions.map((f, i) => (
              <div key={f.name} className="card pp-function">
                <span className="pp-num">{i + 1}</span>
                <div>
                  <b>{f.name}</b>
                  <p className="small muted">{f.description}</p>
                </div>
              </div>
            ))}
          </div>

          <div className="pp-two">
            <section>
              <h3 className="pp-h">Parts</h3>
              <ul className="card pp-parts">
                {p.parts.map((x) => (
                  <li key={x.name}>
                    <div className="pp-part-head">
                      <b>{x.name}</b>
                      {x.electrical && <span className="chip pp-elec">electrical</span>}
                    </div>
                    {x.material && <div className="small pp-material">{x.material}</div>}
                    <div className="small muted">{x.role}</div>
                  </li>
                ))}
              </ul>
            </section>
            <section className="pp-stack">
              <div>
                <h3 className="pp-h">Electronics</h3>
                <div className="card"><More items={p.electronics} n={4} /></div>
              </div>
              {p.vehicle_interfaces.length > 0 && (
                <div>
                  <h3 className="pp-h">Vehicle interfaces</h3>
                  <div className="card"><More items={p.vehicle_interfaces} n={4} /></div>
                </div>
              )}
            </section>
          </div>

          {(p.drawing_observations.length > 0 || p.uncertainties.length > 0) && (
            <div className="pp-two">
              {p.drawing_observations.length > 0 && (
                <section>
                  <h3 className="pp-h">Seen only in the drawing</h3>
                  <div className="card"><More items={p.drawing_observations} /></div>
                </section>
              )}
              {p.uncertainties.length > 0 && (
                <section>
                  <h3 className="pp-h">Assumed or unclear</h3>
                  <div className="card pp-unclear"><More items={p.uncertainties} /></div>
                </section>
              )}
            </div>
          )}
        </>
      )}

      {rows.length > 0 && <Regulations rows={rows} />}
    </div>
  );
}

function Regulations({ rows }: { rows: Row[] }) {
  const applies = rows.filter((r) => r.selected);
  const replaced = rows.filter((r) => !r.selected && r.superseded_by);
  const maybe = rows.filter((r) => !r.selected && !r.superseded_by && r.applies === "maybe");
  const no = rows.filter((r) => !r.selected && !r.superseded_by && r.applies !== "maybe");
  const [showNo, setShowNo] = useState(false);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const noShown = useMemo(() => {
    const t = q.trim().toLowerCase();
    return t ? no.filter((r) => `${r.regulation} ${r.title} ${r.reason}`.toLowerCase().includes(t)) : no;
  }, [no, q]);

  return (
    <section className="pp-regs">
      <div className="pp-regs-head">
        <h2>Regulations</h2>
        <p className="muted">{rows.length} considered: {applies.length} apply, {replaced.length} replaced by a consolidated one, {maybe.length} uncertain and left out, {no.length} don't apply.</p>
      </div>

      <div className="pp-apply">
        {applies.map((r) => (
          <article key={r.regulation} className="card pp-reg-card">
            <div className="pp-reg-top">
              <span className="pp-reg-id">{r.regulation}</span>
              {r.basis && r.basis !== "none" && <span className="chip">{BASIS[r.basis] ?? r.basis}</span>}
            </div>
            <div className="pp-reg-title">{r.title}</div>
            <p className="small muted">{r.reason}</p>
            {(r.functions_covered?.length ?? 0) > 0 && (
              <div className="pp-covers">{r.functions_covered!.slice(0, 5).map((f) => <span key={f} className="chip">{f}</span>)}</div>
            )}
          </article>
        ))}
      </div>

      {(replaced.length > 0 || maybe.length > 0) && (
        <div className="pp-two">
          {replaced.length > 0 && (
            <div>
              <h3 className="pp-h">Replaced by a consolidated regulation</h3>
              <ul className="card pp-rows">
                {replaced.map((r) => (
                  <li key={r.regulation}>
                    <div><s className="muted">{r.regulation}</s> → <b>{r.superseded_by}</b></div>
                    <div className="small muted">{r.title}</div>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {maybe.length > 0 && (
            <div>
              <h3 className="pp-h">Uncertain, left out</h3>
              <ul className="card pp-rows">
                {maybe.map((r) => (
                  <li key={r.regulation}>
                    <div><b>{r.regulation}</b> <span className="muted">{r.title}</span></div>
                    <div className="small muted">{r.reason}</div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      <div className="card pp-no">
        <button className="pp-no-head" onClick={() => setShowNo((s) => !s)} aria-expanded={showNo}>
          <span><b>{no.length} don't apply</b> <span className="muted small">to this product: open to see each agent's reason</span></span>
          <span className="muted">{showNo ? "▴" : "▾"}</span>
        </button>
        {showNo && (
          <>
            <input className="input" placeholder="Search regulation, title or reason…" value={q} onChange={(e) => setQ(e.target.value)} />
            <ul className="pp-no-list">
              {noShown.map((r) => (
                <li key={r.regulation}>
                  <button onClick={() => setOpen(open === r.regulation ? null : r.regulation)} aria-expanded={open === r.regulation}>
                    <span className="pp-no-id">{r.regulation}</span>
                    <span className="pp-no-title">{r.title}</span>
                  </button>
                  {open === r.regulation && <p className="small muted">{r.reason}</p>}
                </li>
              ))}
              {noShown.length === 0 && <li className="small muted">Nothing matches.</li>}
            </ul>
          </>
        )}
      </div>
    </section>
  );
}

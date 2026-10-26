import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";

export function Runs() {
  const navigate = useNavigate();
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  return (
    <div className="page page-narrow" style={{ maxWidth: 1080 }}>
      <div className="page-head">
        <div>
          <h1>Runs</h1>
          <p>Every analysis on this server. Finished ones open instantly.</p>
        </div>
        <Link className="btn btn-primary" to="/new">
          New analysis
        </Link>
      </div>
      {runs.isLoading && <div className="skeleton" style={{ height: 200 }} />}
      {runs.isError && <div className="notice warn">{(runs.error as Error).message}</div>}
      {runs.data && runs.data.length === 0 && (
        <div className="card empty">
          No analyses yet. <Link to="/new">Start one</Link>.
        </div>
      )}
      {runs.data && runs.data.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Part</th>
              <th>Status</th>
              <th>Requirements</th>
              <th>Regulations</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {runs.data.map((r) => (
              <tr key={r.id} className="clickable" onClick={() => navigate(r.status === "done" ? `/runs/${r.id}` : `/new/${r.id}`)}>
                <td>
                  <Link to={r.status === "done" ? `/runs/${r.id}` : `/new/${r.id}`} onClick={(e) => e.stopPropagation()}>
                    {r.title}
                  </Link>
                  {r.preloaded && <span className="chip" style={{ marginLeft: 8 }}>saved</span>}
                </td>
                <td>{r.status === "done" ? "Complete" : r.status === "failed" ? "Failed" : "Running"}</td>
                <td>{r.counts.requirements ?? "–"}</td>
                <td>{r.counts.regulations ?? "–"}</td>
                <td className="muted">{new Date(r.created).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

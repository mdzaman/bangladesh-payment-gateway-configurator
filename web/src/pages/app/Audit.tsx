import { useInfiniteQuery } from "@tanstack/react-query";
import * as api from "../../api/endpoints";
import { useEnv } from "../../lib/env";
import { when } from "../../lib/format";
import { Empty, ErrorNote, Loading, PageHead, Pill } from "../../ui";

export default function Audit() {
  const ctx = useEnv();
  const query = useInfiniteQuery({
    queryKey: ["workspace", ctx.slug, "audit"],
    queryFn: ({ pageParam }) => api.workspaces.auditLog(ctx.slug, pageParam || undefined),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  if (query.isPending) return <Loading />;
  if (query.error) return <ErrorNote error={query.error} />;
  const entries = query.data.pages.flatMap((p) => p.data);
  return (
    <div className="stack">
      <PageHead title="Audit log">Who changed what in {ctx.workspace.name}. Secret values are never written here.</PageHead>
      <section className="card flush">
        {entries.length === 0 ? <Empty title="Nothing yet" /> : (
          <div className="scroll">
            <table className="cards">
              <thead><tr><th>When</th><th>Who</th><th>What</th><th>On</th><th>Mode</th><th>Details</th></tr></thead>
              <tbody>
                {entries.map((e) => (
                  <tr key={e.id}>
                    <td data-l="When" className="small nowrap">{when(e.at)}</td>
                    <td data-l="Who" className="small">{e.actor || e.actor_type}{e.ip && <div className="mute">{e.ip}</div>}</td>
                    <td data-l="What"><code>{e.action}</code></td>
                    <td data-l="On" className="small">{e.target_id ? <code>{e.target_id}</code> : <span className="mute">—</span>}</td>
                    <td data-l="Mode">{e.mode ? <Pill tone={e.mode === "live" ? "live" : "grey"}>{e.mode}</Pill> : null}</td>
                    <td data-l="Details" className="small mute">{Object.keys(e.detail).length ? <code>{JSON.stringify(e.detail)}</code> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {query.hasNextPage && (
        <div className="row" style={{ justifyContent: "center" }}>
          <button type="button" onClick={() => query.fetchNextPage()} disabled={query.isFetchingNextPage}>Load more</button>
        </div>
      )}
    </div>
  );
}

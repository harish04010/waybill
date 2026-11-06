import { city, money, pct, statusLabel } from "@/lib/format";
import type { AgentEvent, Shipment } from "@/lib/types";

const HIDDEN = new Set(["state", "tool_call"]);

function when(e: AgentEvent, first?: AgentEvent) {
  const t0 = first?.t ?? first?.ts ?? 0;
  const t = e.t ?? e.ts ?? 0;
  return `+${(t - t0).toFixed(1)}s`;
}

export function Trace({ s, events }: { s: Shipment | undefined; events: AgentEvent[] }) {
  if (!s) {
    return (
      <aside className="trace trace--empty">
        <p>Pick a shipment to see what each agent did and why.</p>
      </aside>
    );
  }
  const mine = events.filter((e) => e.shipment_id === s.id);
  const shown = mine.filter((e) => !HIDDEN.has(e.kind));

  return (
    <aside className="trace" aria-live="polite">
      <header className="trace__head">
        <h2>{s.vehicle}</h2>
        <p className="muted">
          {s.id}, VIN {s.vin}
        </p>
        <p>
          {city(s.origin)} to {city(s.destination)}, {s.miles.toLocaleString()} mi on an {s.trailer} trailer.{" "}
          <strong>{statusLabel[s.status]}.</strong>
        </p>
        <p className="muted">
          Market {money(s.market_rate)}, quoted {money(s.quote)}
          {s.carrier ? `, ${s.carrier.name} at ${money(s.carrier_rate)} (${pct(s.margin)} margin)` : ""}
        </p>
        {s.failure_reason && <p className="trace__failure">{s.failure_reason}</p>}
      </header>

      <ol className="trace__list">
        {shown.map((e) => (
          <li key={e.seq} className={`ev ev--${e.kind} ev--agent-${e.agent.split(" ")[0]}`}>
            <span className="ev__meta">
              <span className="ev__agent">{e.agent}</span>
              <span className="ev__time">{when(e, mine[0])}</span>
            </span>
            <span className="ev__msg">{e.message}</span>
            {e.data?.reasoning && <span className="ev__why">{e.data.reasoning}</span>}
            {e.data?.say && <span className="ev__say">“{e.data.say}”</span>}
            {e.kind === "message" && <span className="ev__sms">{e.data.body}</span>}
            {e.kind === "thought" && e.data?.ranking && (
              <table className="ev__table">
                <tbody>
                  {e.data.ranking.map((c: any) => (
                    <tr key={c.name}>
                      <td>{c.name}</td>
                      <td>{money(c.asking)}</td>
                      <td>{c.rating}★</td>
                      <td>{Math.round(c.on_time * 100)}% on time</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {e.kind === "tool_result" && (
              <details className="ev__raw">
                <summary>What the tool returned</summary>
                <pre>{JSON.stringify(e.data.result, null, 2)}</pre>
              </details>
            )}
          </li>
        ))}
      </ol>
    </aside>
  );
}

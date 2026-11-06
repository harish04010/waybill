"use client";

import { useEffect, useMemo, useState } from "react";
import { ApprovalSlip } from "@/components/ApprovalSlip";
import { ShipmentRow } from "@/components/ShipmentRow";
import { Trace } from "@/components/Trace";
import { pct } from "@/lib/format";
import { useFeed } from "@/lib/useFeed";

export default function Dashboard() {
  const feed = useFeed();
  const { shipments, events, mode } = feed;
  const [selected, setSelected] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  // Follow the action until someone picks a shipment themselves.
  useEffect(() => {
    if (selected || !shipments.length) return;
    setSelected(shipments[0].id);
  }, [shipments, selected]);

  const waiting = shipments.filter((s) => s.status === "needs_approval");
  const summary = useMemo(() => {
    const count = (st: string) => shipments.filter((s) => s.status === st).length;
    const margins = shipments.map((s) => s.margin).filter((m): m is number => m != null);
    const retries = events.filter((e) => e.kind === "retry").length;
    const parts = [
      [count("delivered"), "delivered"],
      [count("in_transit") + count("booked"), "on the road"],
      [count("new") + count("quoted"), "being quoted or booked"],
      [waiting.length, "waiting on you"],
      [count("failed"), "escalated"],
    ]
      .filter(([n]) => n)
      .map(([n, label]) => `${n} ${label}`);
    return {
      line: parts.join(", "),
      margin: margins.length ? margins.reduce((a, b) => a + b, 0) / margins.length : null,
      retries,
    };
  }, [shipments, events, waiting.length]);

  const start = async () => {
    setStarting(true);
    try {
      await feed.simulate(6);
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="page">
      <header className="masthead">
        <div className="sign">
          <span className="sign__name">Waybill</span>
          <span className="sign__sub">Freight agents at work</span>
        </div>

        <div className="controls">
          {mode === "live" ? (
            <>
              <span className={`conn ${feed.connected ? "is-on" : ""}`}>
                {feed.connected ? "Connected to the API" : "Can't reach the API. Is the backend running?"}
              </span>
              <button className="btn btn--primary" onClick={start} disabled={starting || !feed.connected}>
                {starting ? "Starting…" : "Start 6 shipments"}
              </button>
            </>
          ) : (
            <>
              <span className="conn is-on">
                {feed.replayDone ? "Recorded run finished" : "Replaying a recorded run"}
              </span>
              <div className="speed" role="group" aria-label="Replay speed">
                {[1, 2, 4].map((x) => (
                  <button
                    key={x}
                    className={`btn btn--small ${feed.speed === x ? "is-active" : ""}`}
                    onClick={() => feed.setSpeed(x)}
                    aria-pressed={feed.speed === x}
                  >
                    {x}×
                  </button>
                ))}
              </div>
              <button className="btn" onClick={() => { setSelected(null); feed.restart(); }}>
                Play again
              </button>
            </>
          )}
        </div>
      </header>

      <section className="summary" aria-live="polite">
        <p className="summary__line">
          {shipments.length ? (
            <>
              <strong>{shipments.length} shipments.</strong> {summary.line}.
            </>
          ) : mode === "live" ? (
            "No shipments yet. Start a few and watch the agents pick them up."
          ) : (
            "Loading the recorded run…"
          )}
        </p>
        {shipments.length > 0 && (
          <p className="summary__sub muted">
            Average booked margin {pct(summary.margin)}. {summary.retries} failed calls retried automatically.
          </p>
        )}
      </section>

      {waiting.length > 0 && (
        <section className="approvals" aria-label="Waiting on a decision">
          <h2 className="section-title">Needs your sign-off</h2>
          <div className="approvals__list">
            {waiting.map((s) => (
              <ApprovalSlip
                key={s.id}
                s={s}
                live={mode === "live"}
                onOpen={() => setSelected(s.id)}
                onDecide={(d) => feed.decide(s.id, d)}
              />
            ))}
          </div>
        </section>
      )}

      <main className="workspace">
        <section className="board" aria-label="Shipments">
          <h2 className="section-title">Shipments</h2>
          <div className="board__rows">
            {shipments.map((s) => (
              <ShipmentRow key={s.id} s={s} selected={s.id === selected} onSelect={() => setSelected(s.id)} />
            ))}
          </div>
        </section>
        <Trace s={shipments.find((s) => s.id === selected)} events={events} />
      </main>

      <footer className="foot muted">
        {mode === "replay"
          ? "This page replays a run recorded from the Python backend. Clone the repo to run it live and make the approval calls yourself."
          : "Agents run in the Python backend. Every action here comes from its event stream."}
      </footer>
    </div>
  );
}

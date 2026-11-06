"use client";

import { useState } from "react";
import { city, money, pct } from "@/lib/format";
import type { Shipment } from "@/lib/types";

interface Props {
  s: Shipment;
  live: boolean;
  onDecide: (decision: "approve" | "reject") => Promise<void>;
  onOpen: () => void;
}

export function ApprovalSlip({ s, live, onDecide, onOpen }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!s.approval) return null;
  const a = s.approval;

  const act = async (d: "approve" | "reject") => {
    setBusy(true);
    setError(null);
    try {
      await onDecide(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save that decision. Try again.");
      setBusy(false);
    }
  };

  return (
    <article className="slip">
      <header className="slip__head">
        <button className="linklike" onClick={onOpen}>
          {s.id}
        </button>
        <span>
          {s.vehicle}, {city(s.origin)} to {city(s.destination)}
        </span>
      </header>
      <p className="slip__reason">{a.reason}</p>
      <dl className="slip__facts">
        <div>
          <dt>Carrier</dt>
          <dd>
            {a.carrier.name}, rated {a.carrier.rating}
          </dd>
        </div>
        <div>
          <dt>Rate</dt>
          <dd>{money(a.rate)}</dd>
        </div>
        <div>
          <dt>Our quote</dt>
          <dd>{money(s.quote)}</dd>
        </div>
        <div>
          <dt>Margin</dt>
          <dd>{pct(a.margin)}</dd>
        </div>
      </dl>
      {live ? (
        <div className="slip__actions">
          <button className="btn btn--primary" disabled={busy} onClick={() => act("approve")}>
            Book {a.carrier.name.split(" ")[0]}
          </button>
          <button className="btn" disabled={busy} onClick={() => act("reject")}>
            Find another carrier
          </button>
        </div>
      ) : (
        <p className="slip__note">In this recording, ops signs off a few seconds later.</p>
      )}
      {error && <p className="slip__error">{error}</p>}
    </article>
  );
}

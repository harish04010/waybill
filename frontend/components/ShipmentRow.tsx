import { city, money, pct, statusLabel } from "@/lib/format";
import type { Shipment } from "@/lib/types";
import { StageTrack } from "./StageTrack";

export function ShipmentRow({ s, selected, onSelect }: { s: Shipment; selected: boolean; onSelect: () => void }) {
  const retries = Object.values(s.attempts).reduce((a, b) => a + b, 0);
  return (
    <button className={`row row--${s.status} ${selected ? "is-selected" : ""}`} onClick={onSelect} aria-pressed={selected}>
      <span className="row__who">
        <strong>{s.vehicle}</strong>
        <span>
          {city(s.origin)} to {city(s.destination)}{" "}
          <span className="muted">
            ({s.miles.toLocaleString()} mi, {s.trailer})
          </span>
        </span>
        <span className="muted">
          {s.id} for {s.customer}
        </span>
      </span>
      <span className="row__track">
        <StageTrack s={s} />
        <span className="row__status">
          {statusLabel[s.status]}
          {s.delayed && s.status === "in_transit" ? ", running late" : ""}
          {retries > 0 ? <span className="muted"> ({retries} {retries === 1 ? "retry" : "retries"})</span> : null}
        </span>
      </span>
      <span className="row__num">
        <span className="muted">Quote</span>
        {money(s.quote)}
      </span>
      <span className="row__num">
        <span className="muted">Carrier</span>
        {money(s.carrier_rate)}
      </span>
      <span className="row__num">
        <span className="muted">Margin</span>
        {pct(s.margin)}
      </span>
    </button>
  );
}

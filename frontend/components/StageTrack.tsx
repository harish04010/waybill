import type { Shipment } from "@/lib/types";

// Where along the road a shipment is, from 0 to 1.
// Quoting happens at the origin, booking a quarter of the way in, and the
// rest of the line is the actual drive, filled from GPS progress.
function position(s: Shipment): number {
  switch (s.status) {
    case "new":
      return 0;
    case "quoted":
      return 0.08;
    case "needs_approval":
    case "booked":
      return 0.25;
    case "in_transit":
      return 0.25 + 0.75 * (s.progress / 100);
    case "delivered":
      return 1;
    case "failed":
      return s.carrier ? 0.25 + 0.75 * (s.progress / 100) : s.quote ? 0.08 : 0;
  }
}

const STOPS = [
  { at: 0, label: "Quote" },
  { at: 0.25, label: "Carrier" },
  { at: 1, label: "Drop" },
];

export function StageTrack({ s }: { s: Shipment }) {
  const pos = position(s);
  return (
    <div className={`track track--${s.status}`} aria-label={`${Math.round(pos * 100)}% of the way`}>
      <div className="track__road">
        <div className="track__fill" style={{ width: `${pos * 100}%` }} />
        {STOPS.map((stop) => (
          <span
            key={stop.label}
            className={`track__stop ${pos >= stop.at ? "is-passed" : ""}`}
            style={{ left: `${stop.at * 100}%` }}
          >
            <span className="track__label">{stop.label}</span>
          </span>
        ))}
        <span className="track__unit" style={{ left: `${pos * 100}%` }} />
      </div>
    </div>
  );
}

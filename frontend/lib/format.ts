export const money = (n: number | null | undefined) =>
  n == null ? "–" : "$" + Math.round(n).toLocaleString("en-US");

export const pct = (n: number | null | undefined) =>
  n == null ? "–" : (n * 100).toFixed(1) + "%";

export const city = (place: string) => place.split(",")[0];

export const statusLabel: Record<string, string> = {
  new: "New",
  quoted: "Finding a carrier",
  needs_approval: "Waiting on you",
  booked: "Booked",
  in_transit: "On the road",
  delivered: "Delivered",
  failed: "Escalated",
};

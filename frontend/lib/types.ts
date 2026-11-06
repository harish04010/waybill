export type Status =
  | "new"
  | "quoted"
  | "needs_approval"
  | "booked"
  | "in_transit"
  | "delivered"
  | "failed";

export interface Carrier {
  name: string;
  mc_number: string;
  rating: number;
  on_time_rate: number;
  asking_rate: number;
}

export interface Shipment {
  id: string;
  customer: string;
  vehicle: string;
  vin: string;
  trailer: "open" | "enclosed";
  origin: string;
  destination: string;
  lane: string;
  miles: number;
  status: Status;
  market_rate: number | null;
  quote: number | null;
  carrier: Carrier | null;
  carrier_rate: number | null;
  margin: number | null;
  approval: { reason: string; carrier: Carrier; rate: number; margin: number } | null;
  progress: number;
  last_location: string | null;
  eta_days: number | null;
  delayed: boolean;
  attempts: Record<string, number>;
  failure_reason: string | null;
  created_at: number;
}

export interface AgentEvent {
  seq: number;
  shipment_id: string;
  agent: string;
  kind: string;
  message: string;
  data: Record<string, any>;
  t?: number; // seconds from start, only in recorded runs
  ts?: number;
}

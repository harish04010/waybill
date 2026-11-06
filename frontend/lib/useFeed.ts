"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AgentEvent, Shipment } from "./types";

const API = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "");

export type Mode = "live" | "replay";

/**
 * One hook, two sources. Live mode streams events from the backend over SSE.
 * Replay mode plays back a recorded run from /demo-run.json, so the hosted
 * dashboard works with no backend at all. Either way the UI only ever sees
 * a list of events and derives everything else from it.
 */
export function useFeed() {
  const mode: Mode = API ? "live" : "replay";
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [speed, setSpeed] = useState(2);
  const [replayDone, setReplayDone] = useState(false);
  const [runId, setRunId] = useState(0);
  const speedRef = useRef(speed);
  speedRef.current = speed;

  // live
  useEffect(() => {
    if (mode !== "live") return;
    const es = new EventSource(`${API}/events`);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (msg) => {
      const e = JSON.parse(msg.data) as AgentEvent;
      setEvents((prev) => (prev.length && prev[prev.length - 1].seq >= e.seq ? prev : [...prev, e]));
    };
    return () => es.close();
  }, [mode, runId]);

  // replay
  useEffect(() => {
    if (mode !== "replay") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    setEvents([]);
    setReplayDone(false);

    fetch("/demo-run.json")
      .then((r) => r.json())
      .then(({ events: recorded }: { events: AgentEvent[] }) => {
        if (cancelled) return;
        setConnected(true);
        let i = 0;
        let clock = 0;
        const tick = () => {
          if (cancelled) return;
          clock += 0.1 * speedRef.current;
          const batch: AgentEvent[] = [];
          while (i < recorded.length && (recorded[i].t ?? 0) <= clock) batch.push(recorded[i++]);
          if (batch.length) setEvents((prev) => [...prev, ...batch]);
          if (i < recorded.length) timer = setTimeout(tick, 100);
          else setReplayDone(true);
        };
        tick();
      })
      .catch(() => setConnected(false));

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [mode, runId]);

  const shipments = useMemo(() => {
    const map = new Map<string, Shipment>();
    for (const e of events) if (e.kind === "state") map.set(e.shipment_id, e.data.shipment as Shipment);
    return Array.from(map.values()).sort((a, b) => a.created_at - b.created_at);
  }, [events]);

  const simulate = useCallback(async (count: number) => {
    if (!API) return;
    await fetch(`${API}/simulate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ count }),
    });
  }, []);

  const decide = useCallback(async (id: string, decision: "approve" | "reject") => {
    if (!API) return;
    const r = await fetch(`${API}/shipments/${id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    if (!r.ok) throw new Error((await r.json()).detail ?? "Decision failed");
  }, []);

  const restart = useCallback(() => setRunId((n) => n + 1), []);

  return { mode, events, shipments, connected, speed, setSpeed, replayDone, simulate, decide, restart };
}

"use client";

import { useEffect, useState } from "react";

export type LabEvent = {
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  idem_key: string | null;
  created_at: string;
};

const WS_URL = (process.env.LAB_API_URL ?? "").replace(/^http/, "ws");

// Durable events after which the streamed tokens are stale: the answer itself, or a run end.
const CLEARS_LIVE = new Set(["agent_message", "run_finished", "run_failed", "run_interrupted"]);

/**
 * The durable events of a session, in seq order, plus the live text of the current answer.
 *
 * Opens the WebSocket first, then catches up with GET /events?after=lastSeq; both deliver
 * contiguous runs of events starting after a seq this hook already has, so keeping only
 * seq > lastSeq de-duplicates without gaps. On a dropped connection it reconnects from
 * lastSeq. Live `agent_delta` tokens are never stored by the API, so after a reload only
 * durable events show. Mount it with `key={sessionId}`: it does not reset on a new id.
 */
export function useSessionStream(sessionId: string) {
  const [events, setEvents] = useState<LabEvent[]>([]);
  const [live, setLive] = useState("");
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let lastSeq = 0;
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const merge = (batch: LabEvent[]) => {
      const fresh = batch.filter((e) => e.seq > lastSeq).sort((a, b) => a.seq - b.seq);
      if (fresh.length === 0) return;
      lastSeq = fresh[fresh.length - 1].seq;
      setEvents((prev) => [...prev, ...fresh]);
      if (fresh.some((e) => CLEARS_LIVE.has(e.type))) setLive("");
    };

    const connect = () => {
      const socket = new WebSocket(`${WS_URL}/sessions/${sessionId}/stream?after=${lastSeq}`);
      ws = socket;
      socket.onopen = async () => {
        setConnected(true);
        const r = await fetch(`/api/sessions/${sessionId}/events?after=${lastSeq}`);
        if (r.ok && !stopped) merge(await r.json());
      };
      socket.onmessage = (message) => {
        const item = JSON.parse(message.data);
        if (item.kind === "delta") setLive((text) => text + (item.payload.text ?? ""));
        else merge([item]);
      };
      socket.onclose = () => {
        setConnected(false);
        if (!stopped) retry = setTimeout(connect, 1000);
      };
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      ws?.close();
    };
  }, [sessionId]);

  return { events, live, connected };
}

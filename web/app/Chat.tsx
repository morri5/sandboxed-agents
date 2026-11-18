"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useRef, useState, type FormEvent } from "react";
import { useSessionStream, type LabEvent } from "./useSessionStream";

const HARNESSES = ["scripted", "deepagents_ollama", "claude_agent", "claude_agent_local"];
const MAX_LINES = 15;

export default function Chat() {
  const sessionId = useSearchParams().get("session");
  const router = useRouter();
  if (!sessionId) return <NewSession onCreated={(id) => router.push(`/?session=${id}`)} />;
  return <Session key={sessionId} sessionId={sessionId} />;
}

function NewSession({ onCreated }: { onCreated: (id: string) => void }) {
  const [repoUrl, setRepoUrl] = useState("");
  const [harness, setHarness] = useState(HARNESSES[0]);
  const [error, setError] = useState("");

  async function create(e: FormEvent) {
    e.preventDefault();
    setError("");
    const r = await fetch("/api/sessions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ repo_url: repoUrl, harness }),
    });
    if (r.ok) onCreated((await r.json()).session_id);
    else setError(`${r.status}: ${await r.text()}`);
  }

  return (
    <form onSubmit={create}>
      <h1>New chat</h1>
      <label>
        Repo URL{" "}
        <input
          value={repoUrl}
          onChange={(e) => setRepoUrl(e.target.value)}
          placeholder="https://github.com/... or a fixtures/ directory"
          size={60}
          required
        />
      </label>{" "}
      <label>
        Harness{" "}
        <select value={harness} onChange={(e) => setHarness(e.target.value)}>
          {HARNESSES.map((h) => (
            <option key={h}>{h}</option>
          ))}
        </select>
      </label>{" "}
      <button type="submit">Create</button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

function Session({ sessionId }: { sessionId: string }) {
  const { events, live, connected } = useSessionStream(sessionId);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  // One key per message: a double click or a retry of the same text reuses it, so the
  // API stores the message once. It is dropped once the API accepted the message.
  const idemKey = useRef<string | null>(null);

  async function send(e: FormEvent) {
    e.preventDefault();
    if (!draft.trim()) return;
    idemKey.current ??= crypto.randomUUID();
    setError("");
    const r = await fetch(`/api/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: draft, idem_key: idemKey.current }),
    });
    if (r.ok) {
      idemKey.current = null;
      setDraft("");
    } else setError(`${r.status}: ${await r.text()}`);
  }

  return (
    <>
      <h1>
        Session <code>{sessionId}</code> <small>{connected ? "live" : "reconnecting"}</small>
      </h1>
      <ol className="events">
        {events.map((e) => (
          <li key={e.seq} data-seq={e.seq} data-type={e.type}>
            <span className="meta">
              #{e.seq} {e.type}
            </span>
            <pre>{detail(e)}</pre>
          </li>
        ))}
      </ol>
      {live && <pre className="live">{live}</pre>}
      <form onSubmit={send}>
        <textarea
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            idemKey.current = null; // different text, different message
          }}
          rows={3}
          cols={80}
          placeholder="Ask the agent"
        />
        <br />
        <button type="submit">Send</button>
        {error && <p className="error">{error}</p>}
      </form>
    </>
  );
}

function clip(text: string): string {
  const lines = text.trimEnd().split("\n");
  if (lines.length <= MAX_LINES) return lines.join("\n");
  return [...lines.slice(0, MAX_LINES), `... (${lines.length - MAX_LINES} more lines)`].join("\n");
}

function detail(e: LabEvent): string {
  const p = e.payload;
  switch (e.type) {
    case "user_message":
    case "agent_message":
      return String(p.text);
    case "tool_call":
      return `${p.name}(${JSON.stringify(p.args)})`;
    case "tool_result":
      if ("stdout" in p) return clip(`exit ${p.exit_code}\n${p.stdout}${p.stderr}`);
      return clip(("exit_code" in p ? `exit ${p.exit_code}\n` : "") + String(p.content));
    case "workspace_saved":
      return "(working tree saved)";
    default:
      return JSON.stringify(p);
  }
}

-- The append-only event log: the single source of truth for every session.
CREATE TABLE events (
  session_id  uuid        NOT NULL,
  seq         bigint      NOT NULL,          -- 1..n per session, gapless
  type        text        NOT NULL,          -- session_created, user_message, tool_call, tool_result, agent_message, sandbox_attached, sandbox_lost, run_started, run_finished, run_failed, run_interrupted, forked_from, harness_switched
  payload     jsonb       NOT NULL,
  idem_key    text,                          -- client-supplied for user actions; "<run_id>:<rseq>" for in-VM runner events
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, seq),
  UNIQUE (session_id, idem_key)
);

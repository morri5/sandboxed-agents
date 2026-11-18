# web

A single chat page for the session API: create a chat on a repo, send messages, and watch the agent's events live. The session id is in the URL (`?session=...`), so a reload or a second tab shows the same chat.

```bash
make api                 # in the repo root, on port 8000
npm --prefix web install
npm --prefix web run dev # http://localhost:3000
```

`LAB_API_URL` points the page at another API (default `http://127.0.0.1:8000`). REST calls go through a Next.js rewrite under `/api`; the WebSocket connects to the API directly.

`make web-test` runs the Playwright test: its own API on port 8765 with the `lab_test` database, the page on port 3100, the scripted harness in a real VM, and two tabs on one session.

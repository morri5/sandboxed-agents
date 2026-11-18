import type { NextConfig } from "next";

// The session API (`make api`). REST goes through the rewrite below, so the API needs
// no CORS; the WebSocket connects to it directly (rewrites do not carry WebSockets).
const LAB_API_URL = process.env.LAB_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  env: { LAB_API_URL },
  agentRules: false, // `next dev` would otherwise write an AGENTS.md when a coding agent runs it
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${LAB_API_URL}/:path*` }];
  },
};

export default nextConfig;

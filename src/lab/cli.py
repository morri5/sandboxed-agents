"""Command-line client for the session API.

    lab new <repo_url> [--harness scripted]   create a session, print its id
    lab say <session_id> "text"               send a message
    lab tail <session_id> [--after N]         stream events until Ctrl-C
    lab replay <session_id>                   print a session's log in full, saved diffs included

The API address comes from LAB_API_URL (default http://127.0.0.1:8000).
"""

import argparse
import asyncio
import base64
import json
import os
import sys
import uuid
from typing import Any

import httpx
import websockets

API_URL = os.environ.get("LAB_API_URL", "http://127.0.0.1:8000")


def new(repo_url: str, harness: str) -> None:
    r = httpx.post(f"{API_URL}/sessions", json={"repo_url": repo_url, "harness": harness})
    r.raise_for_status()
    print(r.json()["session_id"])


def say(session_id: str, text: str) -> None:
    body = {"text": text, "idem_key": str(uuid.uuid4())}
    r = httpx.post(f"{API_URL}/sessions/{session_id}/messages", json=body)
    r.raise_for_status()
    print(f"sent ({body['idem_key']})")


MAX_RESULT_LINES = 15


def clip(text: str, limit: int | None = MAX_RESULT_LINES) -> str:
    """Keep long tool output readable: the first lines plus a count of the rest."""
    lines = text.rstrip().splitlines()
    if limit is None or len(lines) <= limit:
        return "\n".join(lines)
    return "\n".join(lines[:limit] + [f"... ({len(lines) - limit} more lines)"])


def render(
    item: dict[str, Any], streamed: bool = False, limit: int | None = MAX_RESULT_LINES
) -> str:
    """One stream item as text. `streamed` means the agent_message was already shown as
    deltas; `limit` is the number of lines kept of a tool result or diff (None: all)."""
    if item["kind"] == "delta":
        return str(item["payload"].get("text", ""))
    p = item["payload"]
    match item["type"]:
        case "tool_call":
            detail = f"{p['name']}({json.dumps(p['args'])})"
        case "tool_result" if "stdout" in p:
            detail = clip(f"exit {p['exit_code']}\n{p['stdout']}{p['stderr']}", limit)
        case "tool_result" if "content" in p:
            exit_code = f"exit {p['exit_code']}\n" if "exit_code" in p else ""
            detail = clip(exit_code + p["content"], limit)
        case "workspace_saved":
            patch = base64.b64decode(p["patch_b64"]).decode(errors="replace")
            detail = clip("\n" + patch, limit) if patch else "(no changes)"
        case "agent_message" if streamed:
            detail = "(streamed above)"
        case "user_message" | "agent_message":
            detail = p["text"]
        case _:
            detail = json.dumps(p)
    return f"\n#{item['seq']} {item['type']}: {detail}\n"


async def tail(session_id: str, after: int) -> None:
    url = f"{API_URL.replace('http', 'ws', 1)}/sessions/{session_id}/stream?after={after}"
    streamed = False  # the previous item was a delta
    async with websockets.connect(url) as ws:
        async for message in ws:
            item = json.loads(message)
            sys.stdout.write(render(item, streamed))
            sys.stdout.flush()
            streamed = item["kind"] == "delta"


def replay(session_id: str) -> None:
    """The durable log of a session (deltas are never stored), nothing clipped."""
    r = httpx.get(f"{API_URL}/sessions/{session_id}/events")
    r.raise_for_status()
    for event in r.json():
        sys.stdout.write(render({"kind": "event", **event}, limit=None))


def main() -> None:
    parser = argparse.ArgumentParser(prog="lab", description="Chat with a repo in a microVM.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_new = sub.add_parser("new", help="create a session on a git repo")
    p_new.add_argument("repo_url")
    p_new.add_argument("--harness", default="scripted")
    p_say = sub.add_parser("say", help="send a message to a session")
    p_say.add_argument("session_id")
    p_say.add_argument("text")
    p_tail = sub.add_parser("tail", help="stream a session's events")
    p_tail.add_argument("session_id")
    p_tail.add_argument("--after", type=int, default=0)
    p_replay = sub.add_parser("replay", help="print a session's whole log")
    p_replay.add_argument("session_id")
    args = parser.parse_args()
    match args.command:
        case "new":
            new(args.repo_url, args.harness)
        case "say":
            say(args.session_id, args.text)
        case "replay":
            replay(args.session_id)
        case "tail":
            try:
                asyncio.run(tail(args.session_id, args.after))
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()

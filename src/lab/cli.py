"""Command-line client for the session API.

    lab new <repo_url> [--harness scripted]   create a session, print its id
    lab say <session_id> "text"               send a message
    lab tail <session_id> [--after N]         stream events until Ctrl-C

The API address comes from LAB_API_URL (default http://127.0.0.1:8000).
"""

import argparse
import asyncio
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


def render(item: dict[str, Any]) -> str:
    if item["kind"] == "delta":
        return str(item["payload"].get("text", ""))
    p = item["payload"]
    match item["type"]:
        case "tool_call":
            detail = json.dumps(p["args"])
        case "tool_result" if "exit_code" in p:
            detail = f"exit {p['exit_code']}\n{p['stdout']}{p['stderr']}".rstrip()
        case "tool_result" if "content" in p:
            detail = p["content"][:500]
        case "user_message" | "agent_message":
            detail = p["text"]
        case _:
            detail = json.dumps(p)
    return f"\n#{item['seq']} {item['type']}: {detail}\n"


async def tail(session_id: str, after: int) -> None:
    url = f"{API_URL.replace('http', 'ws', 1)}/sessions/{session_id}/stream?after={after}"
    async with websockets.connect(url) as ws:
        async for message in ws:
            sys.stdout.write(render(json.loads(message)))
            sys.stdout.flush()


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
    args = parser.parse_args()
    match args.command:
        case "new":
            new(args.repo_url, args.harness)
        case "say":
            say(args.session_id, args.text)
        case "tail":
            try:
                asyncio.run(tail(args.session_id, args.after))
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()

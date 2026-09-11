"""
M0 spike: validate the confirmed Open WebUI v0.11.3 chat/tool-loop protocol
end-to-end from a non-browser client.

This is throwaway exploration code -- NOT part of the Gateway package (see
docs/design.md Section 6). The protocol itself is now confirmed by live
capture (see docs/protocol.md) -- this script's job is to prove a plain
Python client can drive it:

    1. Connect Socket.IO, get its `sid`.
    2. POST /api/chat/completions with session_id=<sid> (this is what ties
       the REST call to the socket -- confirmed from capture).
    3. The REST response is just an ack ({"status", "task_ids", "chat_id"}),
       NOT the answer. Listen on the socket instead.
    4. All generation streams as socket.io "events" frames shaped
       {chat_id, message_id, data: {type, data}}.
    5. Terminal event: data.type == "chat:completion" and data.data.done is
       true. data.data.output contains the full turn (function_call /
       function_call_output / message items) -- read content[0].text off
       the "message" item for the final answer.

Remaining unknowns (see docs/protocol.md "Open questions"), which running
this script against your instance should help resolve:
  - exact login endpoint / how the token reaches the socket.io handshake
  - which completion payload fields are actually required vs. omittable
  - parent_id shape for the first message of a brand-new chat
  - terminal-event shape for a turn with no tool call

Setup:
    pip install "python-socketio[client]" httpx

Usage:
    export OWUI_BASE_URL=https://open-webui.example.ts.net
    export OWUI_EMAIL=you@example.com
    export OWUI_PASSWORD=...
    export OWUI_MODEL=ophelia
    export OWUI_TOOL_IDS=openweathermap_forecast   # comma-separated, optional
    export OWUI_CHAT_ID=...                        # optional; omit to start a new chat
    python scripts/m0_owui_spike.py "What's the weather in Boston right now?"
"""

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
import socketio

BASE_URL = os.environ["OWUI_BASE_URL"].rstrip("/")
EMAIL = os.environ["OWUI_EMAIL"]
PASSWORD = os.environ["OWUI_PASSWORD"]
MODEL = os.environ["OWUI_MODEL"]
TOOL_IDS = [t for t in os.environ.get("OWUI_TOOL_IDS", "").split(",") if t]
CHAT_ID = os.environ.get("OWUI_CHAT_ID")
PROMPT = sys.argv[1] if len(sys.argv) > 1 else "What's the weather in Boston right now?"

LOG_PATH = Path("m0_spike_log.jsonl")


def log(source: str, payload) -> None:
    entry = {"t": time.time(), "source": source, "payload": payload}
    print(f"[{source}] {json.dumps(payload, default=str)[:500]}")
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def login() -> str:
    """POST /api/v1/auths/signin -- OWUI's standard login endpoint (assumed;
    not yet directly captured -- flag in docs/protocol.md if this 404s)."""
    r = httpx.post(
        f"{BASE_URL}/api/v1/auths/signin",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    log("login", {"status": r.status_code, "keys": list(data.keys())})
    return data["token"]


def send_completion(token: str, sid: str, parent_id: str | None = None) -> dict:
    """POST /api/chat/completions. Confirmed: the response is just an ack
    ({"status", "task_ids", "chat_id"}), not the answer -- the answer
    streams over the socket.io connection identified by `sid`.

    The user message and the (not-yet-generated) assistant message are two
    DISTINCT, linked tree nodes -- confirmed from a real browser payload:
    top-level `id`/`message_ids` refer to the assistant's future message id,
    `user_message.id` is a different id for the user's own message, and
    `user_message.childrenIds` pre-links to the assistant id. An earlier
    version of this script collapsed both into one id with empty
    childrenIds -- a malformed tree that may be why the backend fell back
    to a generic system-prompt response instead of answering the prompt.
    """
    user_message_id = str(uuid.uuid4())
    assistant_message_id = str(uuid.uuid4())
    payload = {
        "stream": True,
        "model": MODEL,
        "tool_ids": TOOL_IDS,
        "session_id": sid,
        "id": assistant_message_id,
        "message_ids": [{"model_id": MODEL, "message_id": assistant_message_id, "modelIdx": 0}],
        "parent_id": parent_id,
        "user_message": {
            "id": user_message_id,
            "parentId": parent_id,
            "childrenIds": [assistant_message_id],
            "role": "user",
            "content": PROMPT,
            "timestamp": int(time.time()),
            "models": [MODEL],
        },
        "background_tasks": {"follow_up_generation": False},
    }
    if CHAT_ID:
        payload["chat_id"] = CHAT_ID

    headers = {"Authorization": f"Bearer {token}"}
    r = httpx.post(
        f"{BASE_URL}/api/chat/completions",
        headers=headers,
        json=payload,
        timeout=30,
    )
    log("rest:ack_request", payload)
    log("rest:ack_response", {"status": r.status_code, "body": r.text})
    r.raise_for_status()
    return r.json()


def extract_final_text(done_payload: dict) -> str | None:
    for item in done_payload.get("output", []):
        if item.get("type") == "message":
            content = item.get("content", [])
            if content:
                return content[0].get("text")
    return None


async def main() -> None:
    token = login()

    sio = socketio.AsyncClient(logger=False, engineio_logger=False)
    all_events: list = []  # every "events" frame, unfiltered -- buffer first, filter after

    @sio.on("*")
    async def catch_all(event, data=None):
        log(f"socketio:{event}", data)

    @sio.on("events")
    async def on_events(payload):
        all_events.append(payload)

    # ASSUMPTION: bearer token passed via socket.io `auth` payload -- if this
    # fails to connect, check DevTools for the real auth mechanism (a
    # `token` query param is the other common pattern).
    await sio.connect(
        BASE_URL,
        socketio_path="/ws/socket.io",
        auth={"token": token},
        transports=["websocket"],
    )
    log("socketio", {"connected": True, "sid": sio.sid})

    # CONFIRMED from a real browser capture: the client must explicitly emit
    # "user-join" with the same auth token right after connecting. Without
    # this, the socket likely isn't subscribed to whatever room/scope the
    # server delivers this user's real chat events to -- suspected root
    # cause of the earlier run receiving an unrelated completion.
    await sio.emit("user-join", {"auth": {"token": token}})
    log("socketio", {"emitted": "user-join"})

    await asyncio.sleep(1)  # let any post-connect handshake settle

    ack = await asyncio.to_thread(send_completion, token, sio.sid)
    chat_id = ack.get("chat_id")
    log("owui", {"chat_id": chat_id, "task_ids": ack.get("task_ids")})

    # Don't race to the first done=true event and disconnect -- a prior run
    # produced an unrelated-looking "done" completion, and the OWUI web UI
    # was left spinning on that chat afterward. Observe the full sequence
    # for this specific chat_id instead, to see whether there are multiple
    # completion turns (and whether disconnecting early is what left the
    # server-side task stuck).
    OBSERVE_SECONDS = 90
    print(f"\nObserving events for chat_id={chat_id} for {OBSERVE_SECONDS}s...")
    await asyncio.sleep(OBSERVE_SECONDS)

    matching = [e for e in all_events if e.get("chat_id") == chat_id]
    print(f"\n=== {len(matching)} event(s) for this chat, in order ===")
    done_events = []
    for e in matching:
        data = e.get("data", {})
        is_done = data.get("type") == "chat:completion" and data.get("data", {}).get("done")
        print(f"  type={data.get('type')!r} message_id={e.get('message_id')!r} done={bool(is_done)}")
        if is_done:
            done_events.append(e)

    print(f"\n=== {len(done_events)} terminal (done=true) chat:completion event(s) ===")
    for e in done_events:
        text = extract_final_text(e["data"]["data"])
        print(f"  message_id={e.get('message_id')}: {text!r}")

    await sio.disconnect()
    log("done", {"log_file": str(LOG_PATH), "total_events_all_chats": len(all_events)})


if __name__ == "__main__":
    asyncio.run(main())

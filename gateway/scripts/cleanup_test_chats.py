"""
Delete OWUI chats left over from Gateway testing.

Targets chats still titled the default "New Chat" -- real conversations
get auto-titled by OWUI's background task (we saw this as the `chat:title`
socket event in docs/protocol.md); a chat stuck on "New Chat" almost always
means a test run that never got far enough for titling to fire, or was
killed/timed out mid-turn. Dry-run by default since deleting is
destructive and hard to reverse.

Usage (run from gateway/, so .env is picked up):
    python scripts/cleanup_test_chats.py            # lists what would be deleted
    python scripts/cleanup_test_chats.py --yes       # actually deletes them
"""

import sys

import httpx

from switchboard.config import settings


def login() -> str:
    r = httpx.post(
        f"{settings.owui_base_url}/api/v1/auths/signin",
        json={"email": settings.owui_email, "password": settings.owui_password},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["token"]


def list_chats(token: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    chats: list[dict] = []
    page = 1
    while True:
        r = httpx.get(
            f"{settings.owui_base_url}/api/v1/chats/?page={page}", headers=headers, timeout=10
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        chats.extend(batch)
        page += 1
    return chats


def delete_chat(token: str, chat_id: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    r = httpx.delete(f"{settings.owui_base_url}/api/v1/chats/{chat_id}", headers=headers, timeout=10)
    r.raise_for_status()


def main() -> None:
    do_delete = "--yes" in sys.argv
    token = login()
    chats = list_chats(token)
    if chats:
        print(f"(sample chat fields: {list(chats[0].keys())})")
    targets = [c for c in chats if c.get("title") == "New Chat"]

    print(f"Found {len(targets)} chat(s) titled 'New Chat' out of {len(chats)} total.")
    for c in targets:
        print(f"  {c.get('id')}  updated_at={c.get('updated_at')}")

    if not do_delete:
        print("\nDry run -- pass --yes to actually delete these.")
        return

    for c in targets:
        delete_chat(token, c["id"])
        print(f"deleted {c.get('id')}")
    print(f"Deleted {len(targets)} chat(s).")


if __name__ == "__main__":
    main()

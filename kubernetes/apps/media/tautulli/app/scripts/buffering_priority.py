#!/usr/bin/env python3
"""Stops another user's transcode when the stream that is buffering matters more.

Run it from a Tautulli script notification on Buffer Warning, with a
condition limiting it to the user whose stream has priority:

  --username {username} --session-id {session_id} --message 'Message shown.'

Among the other users' streams that are transcoding video, the one furthest
from the end is stopped, so the ones nearly finished carry on. One stream is
stopped per warning. Tautulli provides TAUTULLI_URL and TAUTULLI_APIKEY.
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

URL = os.environ.get("TAUTULLI_URL", "http://localhost:8181").rstrip("/")
KEY = os.environ.get("TAUTULLI_APIKEY", "")


def api(cmd, **params):
    query = urllib.parse.urlencode({"apikey": KEY, "cmd": cmd, **params})
    with urllib.request.urlopen(f"{URL}/api/v2?{query}", timeout=30) as reply:
        return json.load(reply)["response"]["data"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--message", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sessions = api("get_activity")["sessions"]
    candidates = [
        s
        for s in sessions
        if s["session_id"] != args.session_id
        and s["username"] != args.username
        and s.get("video_decision") == "transcode"
    ]
    if not candidates:
        print("No other transcoding streams to stop.")
        return 0

    victim = min(candidates, key=lambda s: int(s.get("progress_percent") or 0))
    print(
        f"{args.username} is buffering; stopping {victim['username']}'s "
        f"{victim.get('full_title', 'stream')} "
        f"({victim.get('progress_percent')}% done)"
    )
    if not args.dry_run:
        api("terminate_session", session_id=victim["session_id"], message=args.message)
    return 0


if __name__ == "__main__":
    sys.exit(main())

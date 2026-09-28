#!/usr/bin/env python3
"""Stops a new stream when a user has used up the day's viewing budget.

Every finished play today costs something: an episode EPISODE_COST, a movie
MOVIE_COST. A new stream is stopped when the plays so far plus the new one
would go over BUDGET. Streams already running are left alone.

Run it from a Tautulli script notification on Playback Start, with the
conditions limiting it to the users it applies to:

  --user-id {user_id} --username {username} --session-id {session_id}
  --media-type {media_type} --budget 3 --episode-cost 1 --movie-cost 3
  --message 'Message shown to the user.'

Tautulli provides TAUTULLI_URL and TAUTULLI_APIKEY to scripts.
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date

URL = os.environ.get("TAUTULLI_URL", "http://localhost:8181").rstrip("/")
KEY = os.environ.get("TAUTULLI_APIKEY", "")


def api(cmd, **params):
    query = urllib.parse.urlencode({"apikey": KEY, "cmd": cmd, **params})
    with urllib.request.urlopen(f"{URL}/api/v2?{query}", timeout=30) as reply:
        return json.load(reply)["response"]["data"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--username", default="")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--media-type", required=True)
    parser.add_argument("--budget", type=float, required=True)
    parser.add_argument("--episode-cost", type=float, default=1)
    parser.add_argument("--movie-cost", type=float, default=1)
    parser.add_argument("--other-cost", type=float, default=0)
    parser.add_argument(
        "--min-seconds",
        type=int,
        default=120,
        help="plays shorter than this are not counted",
    )
    parser.add_argument(
        "--settle-seconds",
        type=int,
        default=15,
        help="wait for the previous play to be written to the history",
    )
    parser.add_argument("--message", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    costs = {
        "episode": args.episode_cost,
        "movie": args.movie_cost,
    }
    cost = lambda media_type: costs.get(media_type, args.other_cost)

    time.sleep(args.settle_seconds)

    # include_activity=0 leaves out the streams running right now, so the new
    # stream is not counted against itself.
    history = api(
        "get_history",
        user_id=args.user_id,
        after=date.today().isoformat(),
        include_activity=0,
        length=1000,
    )["data"]
    plays = [p for p in history if int(p.get("duration") or 0) >= args.min_seconds]
    used = sum(cost(p["media_type"]) for p in plays)
    new = cost(args.media_type)

    print(
        f"{args.username or args.user_id}: used {used:g} of {args.budget:g} "
        f"today ({len(plays)} plays), new {args.media_type} costs {new:g}"
    )
    if used + new <= args.budget:
        return 0

    print("Over budget, stopping the stream.")
    if not args.dry_run:
        api("terminate_session", session_id=args.session_id, message=args.message)
    return 0


if __name__ == "__main__":
    sys.exit(main())

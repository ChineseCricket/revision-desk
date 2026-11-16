"""Machine-readable client for local manuscript reviews."""
import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--port", type=int, default=8540)
sub = parser.add_subparsers(dest="command", required=True)
sub.add_parser("queue", help="list current proposals, including untouched ones")
history = sub.add_parser("history", help="read every version and discussion event")
history.add_argument("id")
for command in ("publish", "reply"):
    action = sub.add_parser(command)
    action.add_argument("file", type=Path, help="UTF-8 proposal JSON or reply text")
    action.add_argument("--version", help="expected version from history (omit for creation)", required=command=="reply")
    action.add_argument("--revision", type=int, default=0, required=command=="reply")
    if command == "reply":
        action.add_argument("--item", required=True)
        action.add_argument("--agent", required=True)
args = parser.parse_args()
origin = f"http://127.0.0.1:{args.port}"


def api(route, payload=None):
    request = Request(origin + "/api/" + route)
    if payload is not None:
        request.data = json.dumps(payload, ensure_ascii=False).encode()
        request.add_header("Content-Type", "application/json")
        request.add_header("Origin", origin)
        request.add_header("X-Review-Token", session["token"])
    with urlopen(request, timeout=20) as response:
        return json.load(response)


try:
    session = api("data")
    data = session["data"]
    if args.command == "publish":
        result = api("proposal", dict(item=json.loads(args.file.read_text(encoding="utf-8-sig")),
                                      expected_version=args.version, revision=args.revision))
    elif args.command == "reply":
        state = api("event", dict(entity=args.item, version=args.version, revision=args.revision,
                                  action="comment", value="", actor="Agent / " + args.agent,
                                  text=args.file.read_text(encoding="utf-8-sig")))
        result = state["items"][args.item]["history"][-1]
    else:
        state = api("state")
        if args.command == "queue":
            result = [dict(id=i["id"], title=i["title"], task=i["task"], kind=i["kind"],
                           version=i["version"], lifecycle=i["lifecycle"], deferred=i["review_deferred"],
                           revision=state["items"][i["id"]]["revision"],
                           decision=state["items"][i["id"]]["decision"],
                           implementation=state["items"][i["id"]]["implementation"])
                      for i in data["items"]]
        else:
            item = next((i for i in data["items"] if i["id"] == args.id), None)
            if item is None:
                parser.error("Unknown item ID")
            result = dict(item=item, state=state["items"][args.id],
                          versions=[v for v in state["proposals"].values() if v["id"] == args.id],
                          stale_sources=session["stale"])
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
except HTTPError as exc:
    sys.exit(f"HTTP {exc.code}: " + exc.read().decode())
except (URLError, OSError, ValueError) as exc:
    sys.exit(str(exc))

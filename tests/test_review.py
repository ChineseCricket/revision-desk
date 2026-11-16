import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


class ReviewCycle(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name) / "garden"
        shutil.copytree(ROOT / "examples/garden", self.work)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = str(sock.getsockname()[1])
        self.origin = "http://127.0.0.1:" + self.port
        self.start()

    def start(self):
        self.proc = subprocess.Popen([sys.executable, str(ROOT/"server.py"), "--workspace", str(self.work),
                                      "--port", self.port], stdout=subprocess.DEVNULL)
        self.addCleanup(self.stop)
        for _ in range(100):
            if self.proc.poll() is not None:
                self.fail("server exited during startup")
            try:
                self.session = self.get("data")
                return
            except URLError:
                time.sleep(.05)
        self.fail("server did not start")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=5)

    def get(self, route):
        with urlopen(self.origin + "/api/" + route, timeout=5) as response:
            return json.load(response)

    def post(self, route, payload):
        req = Request(self.origin + "/api/" + route, data=json.dumps(payload).encode(),
                      headers={"Origin": self.origin, "X-Review-Token": self.session["token"],
                               "Content-Type": "application/json"})
        with urlopen(req, timeout=5) as res:
            return json.load(res)

    def cli(self, *args):
        raw = subprocess.check_output([sys.executable, str(ROOT/"agent.py"), "--port", self.port, *args])
        return json.loads(raw)

    def test_review_cycle(self):
        self.assertEqual(self.cli("queue"), [])
        old = self.cli("publish", str(ROOT/"examples/change.json"))
        version = old["version"]
        choice = dict(entity="P-01", version=version, revision=0, action="decision",
                      value="accepted", actor="Demo author", text="Include all four plots in a new version.")
        state = self.post("event", choice)
        seq = state["items"]["P-01"]["revision"]
        self.assertFalse(state["groups"]["METHODS"]["eligible"])
        history = self.cli("history", "P-01")
        self.assertEqual(history["state"]["history"][-1]["text"], choice["text"])
        event = self.cli("reply", str(ROOT/"examples/reply.txt"), "--item", "P-01",
                         "--version", version, "--revision", str(seq), "--agent", "sample")
        self.assertEqual(event["actor"], "Agent / sample")
        with self.assertRaises(HTTPError) as conflict:
            self.post("batch", dict(items=[dict(id="P-01", version=version, revision=seq)], value="rejected"))
        self.assertEqual(conflict.exception.code, 409)
        new = self.cli("publish", str(ROOT/"examples/change-v2.json"), "--version", version,
                       "--revision", str(event["seq"]))
        self.assertNotEqual(new["version"], version)
        history = self.cli("history", "P-01")
        self.assertEqual(history["state"]["decision"], "pending")
        self.assertEqual(len(history["versions"]), 2)
        self.assertEqual([e["version"] for e in history["state"]["history"]], [version, version])
        choice.update(version=new["version"], revision=event["seq"])
        state = self.post("event", choice)
        state = self.post("event", dict(choice, revision=state["items"]["P-01"]["revision"],
                                        action="implementation", value="verified", text="Checked demo manuscript."))
        group = state["groups"]["METHODS"]
        self.assertTrue(group["eligible"])
        self.post("event", dict(entity="METHODS", version=group["version"], revision=group["revision"],
                                action="closure", value="closed", text="Reviewed both versions."))
        self.assertTrue(self.get("export")["state"]["groups"]["METHODS"]["closed"])
        (self.work/"current.txt").write_text("A new current draft.\n", encoding="utf-8")
        self.assertIn("current.txt", self.get("data")["stale"])
        with self.assertRaises(HTTPError) as changed:
            self.post("event", choice)
        self.assertEqual(changed.exception.code, 409)
        self.stop()
        self.start()
        history = self.cli("history", "P-01")
        self.assertEqual(history["state"]["implementation"], "verified")
        self.assertEqual(history["item"]["current_lines"], [])
        self.assertEqual(history["item"]["lines"], [3])
        self.assertEqual(len(history["versions"]), 2)

    def test_text_only_and_invalid_location(self):
        self.stop()
        config = json.loads((self.work/"project.json").read_text("utf-8"))
        for side in ("original", "current"):
            config[side].pop("pages")
        (self.work/".reviewdesk/review.sqlite3").unlink()
        (self.work/"project.json").write_text(json.dumps(config), encoding="utf-8")
        self.start()
        change = json.loads((ROOT/"examples/change.json").read_text("utf-8"))
        packet = dict(item=change, expected_version=None, revision=0)
        with self.assertRaises(HTTPError) as bad:
            self.post("proposal", packet)
        self.assertEqual(bad.exception.code, 400)
        edit = change["edits"][0]
        edit.pop("anchor")
        edit.pop("current_anchor")
        self.post("proposal", packet)
        self.assertEqual(self.cli("queue")[0]["decision"], "pending")
        with self.assertRaises(HTTPError) as conflict:
            self.post("proposal", packet)
        self.assertEqual(conflict.exception.code, 409)

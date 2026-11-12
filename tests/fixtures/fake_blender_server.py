"""A stand-in for blender/server.py, run with the venv's Python by the protocol tests (no Blender needed).

It speaks the same protocol, with Blender's habits: untagged lines on stdout before and between replies, prints on
stderr. Ops: ping; slow (progress steps, honours cancel unless {"stubborn": true}); fail (an error with a trace);
copy (an npz in -> out, like a one-stage melt); die (exits mid-request).
"""
import json
import os
import sys
import threading
import time

TAG = "\x1eATLAS1 "
out = sys.stdout
lock = threading.Lock()


def send(msg):
    with lock:
        out.write("Read prefs: noise before a tag " + TAG + json.dumps(msg) + "\n")
        out.flush()


def main():
    out.write("Blender 9.9.9 (fake) banner line\n")
    out.flush()
    print("fake server starting", file=sys.stderr, flush=True)
    version = [int(x) for x in os.environ.get("FAKE_BLENDER_VERSION", "5.2.0").split(".")]
    cancelled = set()
    inbox = []
    cond = threading.Condition()

    def reader():
        for line in sys.stdin:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "cancel" in msg:
                cancelled.add(msg["cancel"])
                continue
            with cond:
                inbox.append(msg)
                cond.notify()
        os._exit(0)

    threading.Thread(target=reader, daemon=True).start()
    send({"ready": True, "protocol": int(os.environ.get("FAKE_PROTOCOL", "1")), "server": "fake",
          "blender": ".".join(map(str, version)), "version": version, "numpy": "?", "pid": os.getpid(),
          "startup_s": 0.0, "missing_nodes": []})
    while True:
        with cond:
            while not inbox:
                cond.wait()
            msg = inbox.pop(0)
        if msg.get("quit"):
            return 0
        rid = msg.get("id")
        op = msg.get("op")
        out.write("Import finished in 0.01 sec. (untagged)\n")
        out.flush()
        if op == "ping":
            send({"id": rid, "ok": True, "pong": True})
        elif op == "slow":
            for k in range(int(msg.get("steps", 50))):
                if rid in cancelled and not msg.get("stubborn"):
                    send({"id": rid, "ok": False, "cancelled": True})
                    break
                send({"id": rid, "progress": k / 50.0, "msg": f"step {k}"})
                time.sleep(0.05)
            else:
                send({"id": rid, "ok": True, "steps": msg.get("steps", 50)})
        elif op == "fail":
            print("Traceback: something broke inside", file=sys.stderr, flush=True)
            send({"id": rid, "ok": False, "error": "ValueError: bad mesh", "trace": "trace text"})
        elif op == "copy":
            import shutil
            shutil.copyfile(msg["mesh"], msg["out"])
            send({"id": rid, "ok": True, "out": msg["out"]})
        elif op == "die":
            print("fatal error in fake blender", file=sys.stderr, flush=True)
            os._exit(3)
        else:
            send({"id": rid, "ok": False, "error": f"unknown op {op}"})


if __name__ == "__main__":
    sys.exit(main())

import os
import json
import hmac
import hashlib
import subprocess
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler

OPTIONS_FILE = "/data/options.json"
SECRET = "Supersecretsecret"
TARGET_BRANCH = "refs/heads/master"
PORT = 5000

if os.path.exists(OPTIONS_FILE):
    with open(OPTIONS_FILE, "r") as f:
        opts = json.load(f)
        SECRET = opts.get("secret", "")
        TARGET_BRANCH = opts.get("target_branch", "refs/heads/master")
        PORT = opts.get("port", 5000)

SYNC_LOCK = asyncio.Lock()
TARGET_DIR = "/config/esphome"


def run_git_cmd(cmd):
    print(f"[GIT] Running: {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=TARGET_DIR, capture_output=True, text=True)
    if res.stdout:
        print(f"[GIT STDOUT] {res.stdout.strip()}")
    if res.stderr:
        print(f"[GIT STDERR] {res.stderr.strip()}")
    return res.returncode == 0


async def perform_sync():
    async with SYNC_LOCK:
        print("[SYNC] Starting Git Synchronization...")

        if not os.path.exists(TARGET_DIR):
            print(f"[ERROR] Directory {TARGET_DIR} does not exist.")
            return False

        if not run_git_cmd(["git", "fetch"]):
            print("[ERROR] git fetch failed. Aborting.")
            return False

        if not run_git_cmd(["git", "reset", "--hard"]):
            print("[ERROR] git reset --hard failed. Aborting.")
            return False

        if not run_git_cmd(["git", "clean", "-fd"]):
            print("[ERROR] git clean -fd failed.")
            return False

        print("[SYNC] Synchronization completed successfully.")
        return True


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        signature = self.headers.get("X-Gitea-Signature", "")
        if SECRET:
            computed_sig = hmac.new(
                SECRET.encode("utf-8"), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(computed_sig, signature):
                print("[SECURITY] Invalid webhook signature.")
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Unauthorized: Invalid signature")
                return

        event = self.headers.get("X-Gitea-Event", "")
        if event != "push":
            print(f"[INFO] Ignored event type: {event}")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Event ignored")
            return

        try:
            payload = json.loads(body.decode("utf-8"))
            ref = payload.get("ref", "")
            if ref and ref != TARGET_BRANCH:
                print(f"[INFO] Push to {ref} ignored (target is {TARGET_BRANCH}).")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Branch ignored")
                return
        except Exception as e:
            print(f"[ERROR] Payload parsing failed: {e}")
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Bad payload")
            return

        print("[WEBHOOK] Valid push webhook received.")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        success = loop.run_until_complete(perform_sync())
        loop.close()

        if success:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Sync Successful")
        else:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Sync Failed")


def main():
    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    print(f"[INIT] Server started on port {PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == "__main__":
    main()

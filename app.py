from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any

OPTIONS_FILE = Path("/data/options.json")
TARGET_DIR = Path("/config/esphome")
DEFAULT_BRANCH = "refs/heads/main"
DEFAULT_PORT = 5000
MAX_BODY_SIZE = 1_048_576  # 1 MiB
GIT_TIMEOUT_SECONDS = 60

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
_LOG = logging.getLogger("gitea_esphome_sync")
_SYNC_LOCK = Lock()


def load_options(path: Path = OPTIONS_FILE) -> dict[str, Any]:
    """Load Home Assistant add-on options, or safe development defaults."""
    if not path.exists():
        return {"secret": "", "target_branch": DEFAULT_BRANCH, "port": DEFAULT_PORT}

    try:
        with path.open("r", encoding="utf-8") as options_file:
            options = json.load(options_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read add-on options from {path}: {exc}") from exc

    if not isinstance(options, dict):
        raise RuntimeError("Add-on options must be a JSON object")
    return options


_OPTIONS = load_options()
SECRET = _OPTIONS.get("secret", "")
TARGET_BRANCH = _OPTIONS.get("target_branch", DEFAULT_BRANCH)
PORT = _OPTIONS.get("port", DEFAULT_PORT)


def validate_configuration() -> None:
    if not isinstance(SECRET, str) or not SECRET.strip():
        raise RuntimeError(
            "A non-empty webhook secret is required. Configure it in the add-on options."
        )
    if not isinstance(TARGET_BRANCH, str) or not TARGET_BRANCH.startswith(
        "refs/heads/"
    ):
        raise RuntimeError(
            "target_branch must be a full branch ref, e.g. refs/heads/main"
        )
    if not isinstance(PORT, int) or isinstance(PORT, bool) or not 1 <= PORT <= 65535:
        raise RuntimeError("port must be an integer between 1 and 65535")


def run_git_cmd(args: list[str]) -> bool:
    """Run a Git command in TARGET_DIR with bounded execution time."""
    command = ["git", *args]
    _LOG.info("Running Git command: %s", " ".join(command))
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            command,
            cwd=TARGET_DIR,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        _LOG.error(
            "Git command timed out after %s seconds: %s", GIT_TIMEOUT_SECONDS, command
        )
        return False
    except OSError as exc:
        _LOG.error("Could not execute Git: %s", exc)
        return False

    if result.stdout.strip():
        _LOG.info("Git stdout: %s", result.stdout.strip())
    if result.stderr.strip():
        log = _LOG.info if result.returncode == 0 else _LOG.error
        log("Git stderr: %s", result.stderr.strip())
    if result.returncode != 0:
        _LOG.error("Git command failed with exit code %s", result.returncode)
        return False
    return True


def perform_sync() -> bool:
    """Reset tracked ESPHome files to the configured branch on origin.

    Untracked files are deliberately preserved. Removing them with `git clean`
    can delete locally generated or manually maintained ESPHome files.
    """
    with _SYNC_LOCK:
        _LOG.info("Starting Git synchronization of %s", TARGET_DIR)

        if not TARGET_DIR.is_dir():
            _LOG.error("Target directory does not exist: %s", TARGET_DIR)
            return False

        if not run_git_cmd(["rev-parse", "--is-inside-work-tree"]):
            _LOG.error("Target directory is not a usable Git working tree")
            return False

        if not run_git_cmd(["remote", "get-url", "origin"]):
            _LOG.error("Git remote 'origin' is not configured")
            return False

        if not run_git_cmd(["check-ref-format", TARGET_BRANCH]):
            _LOG.error("Invalid target branch reference: %s", TARGET_BRANCH)
            return False

        branch_name = TARGET_BRANCH.removeprefix("refs/heads/")
        if not run_git_cmd(["fetch", "--no-tags", "origin", branch_name]):
            _LOG.error("Git fetch failed; synchronization aborted")
            return False

        if not run_git_cmd(["reset", "--hard", "FETCH_HEAD"]):
            _LOG.error("Git reset failed; synchronization aborted")
            return False

        _LOG.info("Git synchronization completed successfully")
        return True


def _send_response(
    handler: BaseHTTPRequestHandler, status: int, message: bytes
) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(message)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(message)


def _valid_signature(body: bytes, signature: str) -> bool:
    if not signature:
        return False
    expected = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


class WebhookHandler(BaseHTTPRequestHandler):
    def setup(self) -> None:
        self.request.settimeout(10)
        super().setup()

    server_version = "Gitea-ESPHome-Sync"
    sys_version = ""

    def do_POST(self) -> None:
        transfer_encoding = self.headers.get("Transfer-Encoding", "").lower()
        if transfer_encoding and transfer_encoding != "identity":
            _send_response(self, 400, b"Unsupported transfer encoding")
            return

        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            _send_response(self, 411, b"Content-Length required")
            return
        try:
            content_length = int(raw_length)
        except ValueError:
            _send_response(self, 400, b"Invalid Content-Length")
            return
        if content_length < 0:
            _send_response(self, 400, b"Invalid Content-Length")
            return
        if content_length > MAX_BODY_SIZE:
            _send_response(self, 413, b"Request body too large")
            return

        body = self.rfile.read(content_length)
        if len(body) != content_length:
            _send_response(self, 400, b"Incomplete request body")
            return

        if not _valid_signature(body, self.headers.get("X-Gitea-Signature", "")):
            _LOG.warning("Rejected webhook with invalid signature")
            _send_response(self, 401, b"Unauthorized: invalid signature")
            return

        if self.headers.get("X-Gitea-Event", "") != "push":
            _send_response(self, 200, b"Event ignored")
            return

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _send_response(self, 400, b"Invalid JSON payload")
            return

        if not isinstance(payload, dict):
            _send_response(self, 400, b"Invalid JSON payload")
            return
        ref = payload.get("ref")
        if not isinstance(ref, str) or not ref:
            _send_response(self, 400, b"Missing or invalid branch ref")
            return
        if ref != TARGET_BRANCH:
            _LOG.info("Ignoring push to %s (configured target: %s)", ref, TARGET_BRANCH)
            _send_response(self, 200, b"Branch ignored")
            return

        _LOG.info("Valid push webhook received for %s", ref)
        if perform_sync():
            _send_response(self, 200, b"Sync successful")
        else:
            _send_response(self, 500, b"Sync failed")

    def log_message(self, fmt: str, *args: Any) -> None:
        _LOG.info("HTTP %s - %s", self.address_string(), fmt % args)


def main() -> None:
    validate_configuration()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), WebhookHandler)
    server.daemon_threads = True
    _LOG.info("Webhook server listening on port %s", PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _LOG.info("Shutting down webhook server")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

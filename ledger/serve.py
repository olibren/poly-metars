"""Single-container reference runner. Put an HTTPS proxy in front for public use."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import signal
import subprocess
import sys
import threading


def main():
    Path("/data/public").mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(
        ("0.0.0.0", 8000), partial(SimpleHTTPRequestHandler, directory="/site")
    )
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "ledger",
            "run",
            "--archive",
            "/data/archive",
            "--output",
            "/data/public",
            "--lookback-days",
            "3",
            "--interval",
            "900",
        ]
    )

    def stop(*_):
        child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        return child.wait()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())

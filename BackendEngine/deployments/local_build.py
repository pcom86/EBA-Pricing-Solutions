"""Monitor the local Docker CLI without requiring PowerShell or elevation."""

import logging
import re
import subprocess
import sys
import threading
import time


def check_server():
    logging.info("Checking Docker server (30s timeout); build not started.")
    with subprocess.Popen(["docker.exe", "version", "--format", "{{.Server.Version}}"]) as probe:
        for elapsed in range(5, 31, 5):
            try:
                code = probe.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logging.info("Waiting for Docker server: %ds elapsed.", elapsed)
            else:
                if code:
                    logging.error("Server check failed (exit %d); no build submitted.", code)
                return code
        probe.kill()
        logging.error("Docker server timed out; no build submitted. Check Docker Desktop and docker context ls.")
        return 124


def build():
    logging.info("Docker server responded. Starting Compose build.")
    start = time.monotonic()
    last_output = None
    step = "none"
    finished = threading.Event()

    def heartbeat():
        while not finished.wait(10):
            now = time.monotonic()
            last = last_output
            activity = "no output yet" if last is None else f"last output {int(now - last)}s ago"
            logging.info("Build: %ds elapsed; %s; last observed step: %s.", now - start, activity, step)

    with subprocess.Popen(
        ["docker.exe", "compose", "build", "--progress", "plain", "pricing"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
    ) as process:
        watcher = threading.Thread(target=heartbeat)
        watcher.start()
        try:
            assert process.stdout is not None
            for line in process.stdout:
                last_output = time.monotonic()
                print(line, end="", flush=True)
                match = re.match(r"^(#\d+)\s+(DONE|CACHED|ERROR|\[)", line)
                if match:
                    state = match[2]
                    if state == "[":
                        state = "started"
                        for token, label in (("load metadata", "base image metadata"),
                                             ("load build context", "build context"),
                                             ("pip install", "Python dependency installation")):
                            if token in line:
                                state = label
                                break
                    step = f"{match[1]} {state}"
                    logging.info("Build step %s.", step)
            code = process.wait()
            logging.info("Compose build exited %d after %.1fs.", code, time.monotonic() - start)
            return code
        finally:
            finished.set()
            watcher.join()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="[%(asctime)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(sys.argv[1], encoding="utf-8")],
    )
    try:
        sys.exit(check_server() or build())
    except OSError as exc:
        logging.error("Local Docker preparation failed: %s", exc)
        sys.exit(1)

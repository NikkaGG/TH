"""Supervise one local API and one local worker; no hidden orphan workers."""

from pathlib import Path
import socket
import subprocess
import sys
import time

from .config import get_settings


def main():
    if get_settings().queue_mode != "local":
        raise SystemExit("Use Docker Compose for celery mode, or set LH_QUEUE_MODE=local.")
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", 8001))
        except OSError:
            raise SystemExit(
                "Port 8001 is already in use. Stop the existing API before starting another one."
            ) from None
    root = Path(__file__).resolve().parents[1]
    processes = []
    try:
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "tiktok_api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8001",
                    "--no-access-log",
                ],
                cwd=root,
            )
        )
        processes.append(subprocess.Popen([sys.executable, "-m", "tiktok_api.cli", "worker-local"], cwd=root))
        print("API panel: http://127.0.0.1:8001. Ctrl+C stops both processes.")
        while all(p.poll() is None for p in processes):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()

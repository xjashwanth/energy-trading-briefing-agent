"""Start a local Streamlit dashboard and wait until it is ready."""
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser


def open_dashboard(root, target):
    # Choose an unused local port so another application's server is never reused.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log_path = root / "runs" / "dashboard.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        child = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(root / "app.py"),
            "--server.address=127.0.0.1", f"--server.port={port}", "--server.headless=true",
            "--browser.gatherUsageStats=false"], cwd=root, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    url = f"http://127.0.0.1:{port}"
    for _ in range(40):
        if child.poll() is not None:
            raise RuntimeError(f"Dashboard exited; inspect {log_path}")
        try:
            with urllib.request.urlopen(url + "/_stcore/health", timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url + f"/?date={target}")
                    print(f"Dashboard: {url}/?date={target} (PID {child.pid})")
                    return
        except OSError:
            pass
        time.sleep(0.25)
    child.terminate()
    raise RuntimeError(f"Dashboard did not become ready; inspect {log_path}")

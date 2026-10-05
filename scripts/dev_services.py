"""Manage only FC666 processes we launched; credentials are never printed."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".runtime"
SERVICES = {
    "api": ([str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "apps.api.main:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000"], ROOT, "http://127.0.0.1:8000/health/ready"),
    "web": (["node", str(ROOT / "apps/web/node_modules/next/dist/bin/next"), "start", "--hostname", "127.0.0.1", "--port", "3000"], ROOT / "apps/web", "http://127.0.0.1:3000/api/health"),
    "market": ([str(ROOT / ".venv/bin/python"), "-m", "apps.worker.market"], ROOT, "http://127.0.0.1:8000/api/v1/market/status"),
}

def start_time(pid):
    # Linux proc starttime guards against PID reuse; process names may contain spaces.
    try:
        fields=Path(f"/proc/{pid}/stat").read_text().rsplit(")",1)[1].split()
        return fields[19] if fields[0]!="Z" else None
    except (OSError,IndexError): return None

def owned(name):
    path = STATE / f"{name}.json"
    if not path.exists(): return None
    record = json.loads(path.read_text())
    return record["pid"] if start_time(record["pid"]) == record["start_time"] else None

def probe(url):
    try:
        with urllib.request.urlopen(url, timeout=6) as response:
            data = json.load(response)
            return response.status == 200 and data.get("status") in {"ready","healthy"} and data.get("trading_enabled") is False
    except (OSError,ValueError): return False

def stop(name):
    pid = owned(name)
    if pid:
        os.killpg(pid, signal.SIGTERM)
        for _ in range(50):
            if start_time(pid) is None: break
            time.sleep(.1)
        if owned(name)==pid:
            os.killpg(pid,signal.SIGKILL)
    (STATE / f"{name}.json").unlink(missing_ok=True)

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv)>1 else "status"
    if action not in {"start","stop","status"}: raise SystemExit("Use start, stop or status")
    STATE.mkdir(mode=0o700,exist_ok=True)
    if action == "stop":
        for name in reversed(SERVICES): stop(name)
        print("Stopped owned FC666 API/web/market processes; database volumes preserved")
    elif action == "status":
        for name, (_,_,url) in SERVICES.items(): print(name, "ready" if probe(url) else "not ready")
        if not all(probe(url) for _,_,url in SERVICES.values()): raise SystemExit(1)
    else:
        launched=[]
        try:
            for name,(command,cwd,url) in SERVICES.items():
                if owned(name):
                    if not probe(url): raise RuntimeError(f"Owned {name} process is not ready; inspect .runtime/{name}.log")
                    print(name,"already ready"); continue
                if probe(url): raise RuntimeError(f"Port for {name} is occupied by an unmanaged service")
                with (STATE / f"{name}.log").open("a") as log:
                    env = os.environ | {"NEXT_TELEMETRY_DISABLED":"1"}
                    proc = subprocess.Popen(command,cwd=cwd,env=env,stdout=log,stderr=log,start_new_session=True)
                (STATE / f"{name}.json").write_text(json.dumps({"pid":proc.pid,"start_time":start_time(proc.pid)}))
                launched.append(name)
                deadline=time.monotonic()+45
                while not probe(url):
                    if proc.poll() is not None or time.monotonic()>deadline:
                        raise RuntimeError(f"{name} failed readiness; inspect .runtime/{name}.log")
                    time.sleep(.3)
                print(name,"ready; trading disabled")
        except Exception:
            for name in reversed(launched): stop(name)
            raise

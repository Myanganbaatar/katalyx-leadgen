"""Local dashboard: python app.py, then open http://localhost:8800

Shows what each pipeline step does, runs a step (or all of them) with one click, streams its log,
and displays every intermediate file as a table. Standard library only.
"""
import csv
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parent
OUTPUT = ROOT / "output"
PORT = 8800
STEPS = ["jobs", "signals", "enrich", "research", "qualify", "verify"]
FILES = {"jobs": "jobs.csv", "signals": "signals.csv", "enrich": "enriched.csv", "research": "researched.csv", "qualify": "leads.csv",
         "all_scored": "all_scored.csv", "verify": "verification.csv"}

state = {"running": None, "started": 0, "log": [], "returncode": None, "queue": [], "done": {}, "step_started": {}}


def run_steps(steps):
    state.update(running=steps[0], started=time.time(), log=[], returncode=None, queue=list(steps), done={},
                 step_started={})
    for step in steps:
        state["running"] = step
        titles = {"jobs": "0. Offres d'emploi", "signals": "1. Signaux de recrutement", "enrich": "2. Filtrage au registre",
                  "research": "3. Recherche par l'agent IA", "qualify": "4. Qualification", "verify": "5. Vérification et rapport"}
        state["log"].append(f"\n——— Étape {titles.get(step, step)} ———")
        state["step_started"][step] = time.time()
        p = subprocess.Popen([sys.executable, "-u", f"{step}.py"], cwd=ROOT, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                             env={**os.environ, "PYTHONIOENCODING": "utf-8"})  # accents survive the pipe
        for line in p.stdout:
            state["log"].append(line.rstrip())
        p.wait()
        state["returncode"] = p.returncode
        state["done"][step] = "ok" if p.returncode == 0 else "error"
        if p.returncode:
            state["log"].append(f"!! {step}.py stopped with code {p.returncode}")
            break
    state["log"].append(f"Done in {time.time() - state['started']:.0f} s")
    state["running"] = None


def read_csv(name):
    path = OUTPUT / FILES[name]
    if not path.exists():
        return {"exists": False, "rows": [], "columns": []}
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig"), delimiter=";"))
    return {"exists": True, "columns": list(rows[0]) if rows else [], "rows": rows,
            "modified": time.strftime("%d/%m %H:%M", time.localtime(path.stat().st_mtime))}


def status():
    out = {}
    for s in STEPS:
        path = OUTPUT / FILES[s]
        out[s] = {"exists": path.exists(),
                  "modified": time.strftime("%d/%m %H:%M", time.localtime(path.stat().st_mtime)) if path.exists() else None}
    if (OUTPUT / "jobs.csv").exists():
        out["jobs"]["count"] = len(read_csv("jobs")["rows"])
    if (OUTPUT / "signals.csv").exists():
        out["signals"]["count"] = len(read_csv("signals")["rows"])
    if (OUTPUT / "enriched.csv").exists():
        rows = read_csv("enrich")["rows"]
        out["enrich"]["count"] = len(rows)
        out["enrich"]["kept"] = sum(1 for r in rows if not r["excluded_because"])
        reasons = {}
        for r in rows:
            if r["excluded_because"]:
                reasons[r["excluded_because"]] = reasons.get(r["excluded_because"], 0) + 1
        out["enrich"]["excluded"] = reasons
    if (OUTPUT / "researched.csv").exists():
        rows = read_csv("research")["rows"]
        heat = {}
        for r in rows:
            heat[r.get("r_heat") or "erreur"] = heat.get(r.get("r_heat") or "erreur", 0) + 1
        out["research"].update(count=len(rows), heat=heat)
    if (OUTPUT / "leads.csv").exists():
        out["qualify"]["count"] = len(read_csv("qualify")["rows"])
    if (OUTPUT / "verification.csv").exists():
        rows = read_csv("verify")["rows"]
        out["verify"].update(count=len(rows), ok=sum(1 for r in rows if r["Verdict"] == "Vérifié"))
    return out


def progress():
    """Plain-language state of the current run, step by step (for the progress panel)."""
    if not state["queue"]:
        return None
    steps = []
    for s in state["queue"]:
        st = state["done"].get(s) or ("running" if state["running"] == s else "pending")
        item = {"id": s, "state": st}
        if s == state["running"]:
            item["seconds"] = round(time.time() - state["step_started"].get(s, time.time()))
            if s == "research" and (OUTPUT / "enriched.csv").exists():
                total = min(45, sum(1 for r in read_csv("enrich")["rows"] if not r["excluded_because"]))
                start = state["step_started"].get(s, time.time())
                done = sum(1 for f in (OUTPUT / "research").glob("*.json") if f.stat().st_mtime >= start)
                item.update(done=done, total=total)
        steps.append(item)
    return {"steps": steps, "elapsed": round(time.time() - state["started"]), "finished": state["running"] is None}


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/":
            return self.send(200, (ROOT / "dashboard.html").read_bytes(), "text/html")
        if url.path == "/api/status":
            return self.send(200, {"steps": status(), "running": state["running"], "returncode": state["returncode"],
                                   "progress": progress()})
        if url.path == "/api/log":
            start = int(q.get("from", ["0"])[0])
            return self.send(200, {"lines": state["log"][start:], "next": len(state["log"]), "running": state["running"]})
        if url.path == "/api/data":
            name = q.get("name", [""])[0]
            return self.send(200, read_csv(name)) if name in FILES else self.send(404, {"error": "unknown file"})
        if url.path == "/rapport.pdf" and (OUTPUT / "rapport.pdf").exists():
            return self.send(200, (OUTPUT / "rapport.pdf").read_bytes(), "application/pdf")
        if url.path == "/leads.xlsx" and (OUTPUT / "leads.xlsx").exists():
            return self.send(200, (OUTPUT / "leads.xlsx").read_bytes(),
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/api/run":
            if state["running"]:
                return self.send(409, {"error": f"{state['running']} is already running"})
            step = parse_qs(url.query).get("step", ["all"])[0]
            steps = STEPS if step == "all" else [step] if step in STEPS else None
            if not steps:
                return self.send(400, {"error": "unknown step"})
            threading.Thread(target=run_steps, args=(steps,), daemon=True).start()
            return self.send(200, {"started": steps})
        self.send(404, {"error": "not found"})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"Dashboard: http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

#!/usr/bin/env python3
"""Headless RAM drive: the real earthd with production-like flags, driven by a protocol-4
client that replays the mod's rules (server/tests/modsim.py), while a sampler records the
whole process tree's RSS to a CSV. BeamNG.drive is never launched.

  python tools/ram_drive.py --label baseline --minutes 12 --out docs/overhaul/ram-baseline.csv

Scenario defaults replicate the user's 2026-11-04 packaged run (server.log): teleport to
Folkestone (51.14, 0.91), drive north-west toward London at 30 m/s, cells 64, block 1024,
near-visual detail, deshadow full, colormatch s2, buildings/trees/sea/airports/gas on.
The scratch cache (--cache-dir, default G:/bnge-ram-cache) is kept between runs so the
'after' drive sees the same warm data; it is NEVER the real G:/BeamNG.Earth Cache.
"""
import argparse
import collections
import csv
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))
sys.path.insert(0, str(SERVER / "tests"))

import geodesy  # noqa: E402
import protocol  # noqa: E402
import socket  # noqa: E402
from modsim import CORE, REBASE, cheb, follow_decide, ground_box, rebase_decide, rebase_hint  # noqa: E402

MOD_VERSION = re.search(r"MOD_VERSION = '([^']+)'", (SERVER.parent / "mod" / "beamng_earth" /
                                                     "lua" / "ge" / "extensions" / "beamngEarth" /
                                                     "core.lua").read_text()).group(1)

SERVER_FLAGS = ["--near-visual", "detail", "--cells", "64", "--block", "1024",
                "--deshadow", "full", "--colormatch", "s2",
                "--sea", "on", "--buildings", "on", "--trees", "on", "--airports", "on",
                "--gas-stations", "on", "--weather", "off", "--gravity", "off",
                "--update-check", "off", "--credits", "off"]


# ---------------------------------------------------------------- Windows process tree + RSS
import ctypes  # noqa: E402
from ctypes import wintypes  # noqa: E402

TH32CS_SNAPPROCESS = 2
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


def proc_tree(root_pid):
    """[(pid, ppid, exe)] of root_pid and its descendants (Toolhelp32 snapshot)."""
    snap = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        return []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        rows = []
        ok = ctypes.windll.kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            rows.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            ok = ctypes.windll.kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        ctypes.windll.kernel32.CloseHandle(snap)
    kids = {}
    for pid, ppid, exe in rows:
        kids.setdefault(ppid, []).append((pid, exe))
    out, stack = [], [(root_pid, 0, None)]
    exe_of = {pid: exe for pid, ppid, exe in rows}
    seen = set()
    while stack:
        pid, ppid, exe = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        out.append((pid, ppid, exe or exe_of.get(pid, "?")))
        for kpid, kexe in kids.get(pid, []):
            stack.append((kpid, pid, kexe))
    return out


def proc_mem(pid):
    """(working-set MB, private-bytes MB) or None."""
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return None
    try:
        c = PROCESS_MEMORY_COUNTERS_EX()
        c.cb = ctypes.sizeof(c)
        if ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
            return c.WorkingSetSize / 1e6, c.PrivateUsage / 1e6
    finally:
        ctypes.windll.kernel32.CloseHandle(h)
    return None


def stop_tree(proc):
    if proc.poll() is None:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    try:
        proc.wait(15)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------- the client
class Drive:
    def __init__(self, args):
        self.args = args
        self.sock_buf = b""
        self.dec = protocol.Decoder()
        self.frames = {}                       # anchorId -> EnuFrame
        self.session = None
        self.st = {"anchor": None, "block": None, "pending": None, "tp": None, "next": 1,
                   "pose_t": 0.0, "place": None, "phase": "wait", "driven": 0.0, "retry_at": 0.0,
                   "latency": None, "loaded_wall": None, "t0_wall": None}
        self.player = {"p": [0.0, 0.0, 0.0], "v": [0.0, 0.0]}
        self.counts = {}
        self.errors = []
        self.far_done = threading.Event()
        self.lock = threading.Lock()
        self.sim = 0.0

    def rid(self):
        self.st["next"] += 1
        return self.st["next"] - 1

    # -- messages ---------------------------------------------------------------------------
    def handle(self, m):
        t = m.get("type")
        self.counts[t] = self.counts.get(t, 0) + 1
        if t in ("anchor_error", "terrain_error"):
            self.errors.append(m)
        elif t == "status" and "mismatch" in str(m.get("text", "")):
            self.errors.append(m)
        elif t == "status":
            print(f"status: {str(m.get('text'))[:90]}", flush=True)
        elif t == "hello":
            self.session = m["sessionId"]
        elif t == "anchor_ready":
            p = self.st["pending"]
            if not p or m.get("requestId") != p["requestId"]:
                return
            with self.lock:
                self.frames[m["anchorId"]] = geodesy.EnuFrame(m["lat"], m["lon"])
                tr = m["terrain"]
                if p["purpose"] == "teleport":
                    self.st["pending"] = None
                    self.commit_anchor(m, tr)
                    self.st["phase"] = "stand"
                else:                                   # rebase: commit when past the distance
                    p["ready"], p["msg"] = True, m
                    # the anchor latency in SIM seconds (rebase.lua hints and the stale rule work
                    # on the sim clock: at speedup 4 a 73 s wall build is ~292 s of driving)
                    self.st["latency"] = max(1.0, self.sim - p["sent"])
        elif t == "terrain_ready":
            tp = self.st["tp"]
            if tp and m.get("requestId") == tp["requestId"]:
                tp["ready"], tp["msg"] = True, m
        elif t == "far_done":
            if self.st["phase"] == "stand":
                self.st["phase"] = "drive"
                self.st["loaded_wall"] = time.monotonic()
                self.far_done.set()

    def commit_anchor(self, m, tr):
        self.client.send({"type": "commit_anchor", "anchorId": m["anchorId"]})
        self.client.send({"type": "commit_terrain", "anchorId": m["anchorId"],
                          "cx": tr["cx"], "cy": tr["cy"]})
        if self.st["pending"] and self.st["pending"]["purpose"] == "rebase":
            # move the player into the new anchor's frame
            old = self.frames[self.st["anchor"]]
            new = self.frames[m["anchorId"]]
            lat, lon, z = old.enu_to_geodetic(*self.player["p"])
            nx, ny, nz = new.geodetic_to_enu(lat, lon, z)
            self.player["p"] = [nx, ny, (m.get("groundZ") or nz) + 1.0]
        else:
            self.player["p"] = [0.0, 0.0, (m.get("groundZ") or 0.0) + 1.0]
            self.player["v"] = [0.0, 0.0]
        self.st["anchor"], self.st["block"] = m["anchorId"], (tr["cx"], tr["cy"])
        self.st["tp"] = None
        self.st["place"] = CORE["PLACE_DELAY_FRAMES"] * (1 / 60)
        self.st["pose_t"] = CORE["POSE_INTERVAL"]

    def commit_rebase(self):
        p = self.st["pending"]
        m = p["msg"]
        if self.st["latency"] is None:
            self.st["latency"] = 5.0
        self.commit_anchor(m, m["terrain"])
        self.st["pending"] = None

    # -- the frame --------------------------------------------------------------------------
    def send_pose(self):
        x, y, z = self.player["p"]
        vx, vy = self.player["v"]
        lat, lon, _ = self.frames[self.st["anchor"]].enu_to_geodetic(x, y, z)
        sp = math.hypot(vx, vy)
        fx, fy = (vx / sp, vy / sp) if sp > 1 else (1.0, 0.0)
        cam = {"x": x - 8 * fx, "y": y - 8 * fy, "z": z + 3, "fx": fx, "fy": fy, "fz": -0.05,
               "fovDeg": 60}
        self.client.send({"type": "pose", "anchorId": self.st["anchor"], "x": x, "y": y, "z": z,
                          "vx": vx, "vy": vy, "lat": float(lat), "lon": float(lon), "speed": sp,
                          "cam": cam})

    def step(self, dt):
        st, a = self.st, self.args
        self.sim += dt
        if st["place"] is not None:
            st["place"] -= dt
            if st["place"] <= 0:
                st["place"] = None
            return
        if st["anchor"] is None:
            return
        if st["phase"] == "drive":
            h = math.radians(a.heading)
            self.player["v"] = [a.speed * math.cos(h), a.speed * math.sin(h)]
        elif st["phase"] == "stand":
            self.player["v"] = [0.0, 0.0]
        x, y, z = self.player["p"]
        vx, vy = self.player["v"]
        self.player["p"] = [x + vx * dt, y + vy * dt, z]
        st["driven"] += math.hypot(vx, vy) * dt
        st["pose_t"] += dt
        if st["pose_t"] >= CORE["POSE_INTERVAL"]:
            st["pose_t"] = 0.0
            self.send_pose()
        size = a.block
        # rebase (rebase.lua decide; the stale rule needs the pending anchor's terrain)
        p = st["pending"]
        stale = edge = None
        if p and p.get("ready"):
            m = p["msg"]
            old, new = self.frames[st["anchor"]], self.frames[m["anchorId"]]
            lat, lon, zz = old.enu_to_geodetic(x, y, z)
            nx, ny, _ = new.geodetic_to_enu(lat, lon, zz)
            stale = cheb(nx, ny, m["terrain"]["cx"], m["terrain"]["cy"])
            gx0, gy0, gx1, gy1 = ground_box(m["terrain"]["cx"], m["terrain"]["cy"], size)
            edge = min(nx - gx0, gx1 - nx, ny - gy0, gy1 - ny)
        act = rebase_decide(math.hypot(x, y), p, stale, a.rebase_m, now=self.sim,
                            retry_at=st["retry_at"], edge=edge, size=size)
        if (a.force_rebase and act is None and p and p.get("ready")
                and p["purpose"] == "rebase" and math.hypot(x, y) >= a.rebase_m):
            act = "commit"                       # (harness: see --force-rebase)
        if act == "prefetch":
            lat, lon, _ = self.frames[st["anchor"]].enu_to_geodetic(x, y, z)
            cx, cy = rebase_hint(vx, vy, math.hypot(x, y), a.rebase_m, size, st["latency"])
            st["pending"] = {"requestId": self.rid(), "purpose": "rebase", "sent": self.sim,
                             "wall": time.monotonic()}
            self.client.send({"type": "prepare_anchor", "requestId": st["pending"]["requestId"],
                              "lat": float(lat), "lon": float(lon), "cx": cx, "cy": cy})
        elif act == "commit":
            self.commit_rebase()
        elif act == "discard":
            st["pending"] = None
            st["retry_at"] = self.sim + 1.0
        # terrain follow (follow.lua decide; no far-field gate: uncovered_ready is modelled as True)
        b = st["block"]
        tp = st["tp"]
        fa = follow_decide(x, y, vx, vy, b[0] if b else None, b[1] if b else None, tp,
                           now=self.sim, retry_at=st["retry_at"], gate=None,
                           latency=st["latency"], size=size)
        if fa and fa[0] == "prepare":
            st["tp"] = {"requestId": self.rid(), "cx": fa[1], "cy": fa[2], "sent": self.sim}
            self.client.send({"type": "prepare_terrain", "requestId": st["tp"]["requestId"],
                              "anchorId": st["anchor"], "cx": fa[1], "cy": fa[2]})
        elif fa and fa[0] == "commit":
            self.client.send({"type": "commit_terrain", "anchorId": st["anchor"],
                              "cx": tp["cx"], "cy": tp["cy"]})
            st["block"], st["tp"] = (tp["cx"], tp["cy"]), None
        elif fa and fa[0] == "discard":
            st["tp"] = None
            st["retry_at"] = self.sim + 1.0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--label", default="run")
    ap.add_argument("--minutes", type=float, default=12.0, help="wall minutes of driving")
    ap.add_argument("--lat", type=float, default=51.14)
    ap.add_argument("--lon", type=float, default=0.91)
    ap.add_argument("--heading", type=float, default=150.4, help="ENU degrees (150.4 = NW toward London)")
    ap.add_argument("--speed", type=float, default=30.0, help="m/s")
    ap.add_argument("--speedup", type=float, default=4.0, help="simulated seconds per wall second")
    ap.add_argument("--block", type=int, default=1024)
    ap.add_argument("--rebase-m", type=float, default=REBASE["DEFAULT_DISTANCE"])
    ap.add_argument("--force-rebase", action="store_true",
                    help="commit a ready rebase anchor even when the mod's stale rule would discard "
                         "it (a loaded server's anchor builds take ~200 s wall = ~800 s sim at "
                         "speedup 4, and the player outruns STALE_DIST=450 m; the harness is "
                         "measuring the server, so prune()/anchor churn must still happen)")
    ap.add_argument("--out", default=None, help="CSV path (default docs/overhaul/ram-<label>.csv)")
    ap.add_argument("--cache-dir", default="G:/bnge-ram-cache", help="scratch download cache (never the real one)")
    ap.add_argument("--sample-s", type=float, default=5.0)
    ap.add_argument("--idle-s", type=float, default=60.0, help="parked sampling after the drive")
    ap.add_argument("--server-arg", action="append", default=[], help="extra earthd flags")
    ap.add_argument("--python", default=r"G:\Projects\BeamNG.Earth-1.8.1-beta\.venv\Scripts\python.exe")
    args = ap.parse_args()

    out = Path(args.out or (ROOT / "docs" / "overhaul" / f"ram-{args.label}.csv"))
    out.parent.mkdir(parents=True, exist_ok=True)
    Path(args.cache_dir).mkdir(parents=True, exist_ok=True)
    user = tempfile.mkdtemp(prefix=f"bnge-ram-user-{args.label}-", dir="G:/")
    errlog_path = os.path.join(user, "earthd.log")
    errlog = open(errlog_path, "w+", encoding="utf-8", errors="replace")

    env = dict(os.environ, BNGE_TRACEMALLOC="1")
    cmd = [args.python, str(SERVER / "earthd.py"), "--port", "0", "--user-dir", user,
           "--cache-dir", args.cache_dir, *SERVER_FLAGS, *args.server_arg]
    print("launch:", " ".join(cmd), flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errlog, text=True, env=env,
                            cwd=str(ROOT))
    try:
        line = proc.stdout.readline()
        m = re.search(r"listening on 127\.0\.0\.1:(\d+)", line)
        if not m:
            raise SystemExit(f"earthd did not start: {line!r} (see {errlog_path})")
        port = int(m.group(1))
        print("earthd on port", port, "pid", proc.pid, flush=True)

        sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        d = Drive(args)

        def on_msg(msg):
            try:
                d.handle(msg)
            except Exception as e:                # keep the drive alive on a client-side bug
                d.errors.append({"type": "client_error", "err": repr(e),
                                 "msg": msg.get("type")})

        d.client = _Sender(sock, on_msg)

        stop = threading.Event()

        samples = []

        def sample_loop():
            t0 = time.monotonic()
            while not stop.is_set():
                el = time.monotonic() - t0
                for pid, ppid, exe in proc_tree(proc.pid):
                    mem = proc_mem(pid)
                    if mem:
                        samples.append((args.label, round(el, 1),
                                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                        pid, ppid, exe, round(mem[0], 1), round(mem[1], 1)))
                stop.wait(args.sample_s)

        st_ = threading.Thread(target=sample_loop, daemon=True)
        st_.start()

        d.client.send({"type": "hello", "protocol": int(CORE["PROTOCOL"]),
                       "format": int(CORE["FORMAT"]), "modVersion": MOD_VERSION})
        d.st["pending"] = {"requestId": d.rid(), "purpose": "teleport"}
        d.client.send({"type": "prepare_anchor", "requestId": d.st["pending"]["requestId"],
                       "lat": args.lat, "lon": args.lon, "cx": 0, "cy": 0})
        print("teleport ->", args.lat, args.lon, flush=True)

        # wait for the initial far field (cap 15 min on a cold cache; drive anyway after)
        if not d.far_done.wait(900):
            if d.st["phase"] == "stand":
                print("WARNING: no far_done in 900 s; driving anyway", flush=True)
                d.st["phase"] = "drive"
                d.st["loaded_wall"] = time.monotonic()
            else:
                raise SystemExit(f"no anchor_ready; errors: {d.errors[:3]}; see {errlog_path}")

        # the drive, paced on the wall clock at --speedup sim seconds per second
        wall0 = time.monotonic()
        sim = 0.0
        DT = 1 / 60
        while True:
            el = time.monotonic() - d.st["loaded_wall"]
            if el >= args.minutes * 60:
                break
            target = (time.monotonic() - wall0) * args.speedup
            while sim < target:
                d.step(DT)
                sim += DT
            if d.errors or d.client.dead is not None:
                print("ERRORS:", d.errors[:3], "conn:", d.client.dead, flush=True)
                break
            time.sleep(0.005)
        driven_km = d.st["driven"] / 1000
        print(f"drove {driven_km:.1f} km sim in {args.minutes} min wall "
              f"(rebases: {d.counts.get('anchor_ready', 0) - 1})", flush=True)

        # parked: keep sampling while the server drains its queues
        d.player["v"] = [0.0, 0.0]
        idle0 = time.monotonic()
        while time.monotonic() - idle0 < args.idle_s:
            d.step(DT)                            # poses keep flowing, player stationary
            time.sleep(DT * 60 / args.speedup)

        stop.set()
        time.sleep(0.6)
        d.client.close()
        sock.close()
    finally:
        stop_tree(proc)
        errlog.close()

    # CSV
    new = not out.exists()
    with open(out, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["label", "elapsed_s", "utc", "pid", "ppid", "exe", "rss_mb", "private_mb"])
        w.writerows(samples)
    log_copy = out.with_suffix(".log")
    log_copy.write_text(Path(errlog_path).read_text(encoding="utf-8", errors="replace"),
                        encoding="utf-8")
    print(f"CSV: {out} ({len(samples)} rows); server log: {log_copy}")

    # summary: total tree RSS per elapsed bucket
    by_t = {}
    for label, el, _utc, pid, ppid, exe, rss, priv in samples:
        by_t.setdefault(el, {})[pid] = rss
    totals = sorted((el, sum(v.values()), len(v)) for el, v in by_t.items())
    if totals:
        tail = [t for el, t, n in totals if el >= totals[-1][0] - 180]
        peak = max(t for _, t, _ in totals)
        print(f"total RSS: peak {peak / 1000:.2f} GB, last-3-min median "
              f"{sorted(tail)[len(tail) // 2] / 1000:.2f} GB, procs {totals[-1][2]}")
    print("message counts:", dict(sorted(d.counts.items(), key=lambda kv: -kv[1])[:12]))
    print("sent counts:", d.client.sent)
    if d.errors:
        print("ERRORS:", d.errors[:5])


class _Sender:
    """One non-blocking I/O thread for the connection: it drains the server's stream (which
    never stops, even while the event loop blocks in a synchronous build) and writes queued
    messages as the socket accepts them — what the game's TCP buffers do. A pose replaces an
    unsent pose (only the newest matters)."""

    def __init__(self, sock, on_msg):
        self.sock = sock
        self.on_msg = on_msg
        self.q = collections.deque()
        self.out = b""
        self.dec = protocol.Decoder()
        self.dead = None
        self.sent = {}
        self.stop = threading.Event()
        sock.setblocking(False)
        self.t = threading.Thread(target=self._loop, daemon=True, name="ram-drive-io")
        self.t.start()

    def _loop(self):
        import select
        while not self.stop.is_set():
            try:
                r, w, _ = select.select([self.sock], [self.sock] if (self.out or self.q) else [],
                                        [], 0.2)
            except (OSError, ValueError):
                return
            try:
                if self.sock in r:
                    data = self.sock.recv(1 << 16)
                    if not data:
                        self.dead = ConnectionError("server closed")
                        return
                    for msg in self.dec.feed(data):
                        self.on_msg(msg)
                if self.sock in w:
                    while self.q:
                        msg = self.q[0]
                        head = protocol.encode(msg)
                        self.q.popleft()
                        self.out += head
                    if self.out:
                        n = self.sock.send(self.out)
                        self.out = self.out[n:]
            except BlockingIOError:
                pass
            except OSError as e:
                self.dead = e
                return
            except Exception as e:                    # a client-side bug must not kill the drive
                print("io: client error", repr(e), flush=True)

    def send(self, msg):
        if self.dead is not None:
            raise self.dead
        t = msg.get("type")
        self.sent[t] = self.sent.get(t, 0) + 1
        if t == "pose":
            for i in range(len(self.q) - 1, -1, -1):   # drop the unsent poses
                if self.q[i].get("type") == "pose":
                    del self.q[i]
        else:
            print(f"send {t} {msg.get('requestId', '')}", flush=True)
        self.q.append(msg)

    def close(self):
        self.stop.set()
        try:
            self.sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass


if __name__ == "__main__":
    main()

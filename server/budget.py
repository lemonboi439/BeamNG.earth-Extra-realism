"""The server's memory and CPU budget for the machine it runs on.

Every worker process keeps its own caches (decoded DEM tiles, map data cells, imagery tiles), sized for a desktop.
On a Steam Deck (16 GB shared with the GPU, 4 cores) seven processes filled the memory: 8.2 GB for the server next to
the game's 2.4 GB and the GPU's 1.9 GB, swap at 6.9 GB, and the system's out-of-memory guard killed the game in a
Baltimore drive (the user, 2026-10-20: "the game just closed"; "I want to keep world generation fast on the deck as
well"). SMALL machines (less than SMALL_GB of RAM) get smaller caches and fewer processes; the others keep the
desktop sizes.

    budget.apply()          # in each process before its caches are made (earthd main, blockworker.init)
"""
import os
import sys

SMALL_GB = 20.0             # below this much RAM: the small profile (a Steam Deck shows ~14.5 GB)
# per process, small profile (desktop size in the module: dem 512 tiles). The sea floors (seacarve.MEM_CELLS) and the
# imagery tiles (imagery.MEM_TILES, the main process only) keep theirs, the map data cells nearly: smaller,
# the workers and the server's ground-ahead and imagery threads spent minutes reading them back from disk and
# decoding them again (sampled on a Steam Deck, 2026-10-20)
SMALL_DEM_TILES = 192       # decoded 512² float32 DEM tiles (1 MB each)
SMALL_WALL_CELLS = 12       # cells of building walls for the street widths (roadwidth.WALL_CELLS, 48)
SMALL_MAPDATA_MB = 160      # decoded map data layers (mapdata.MEM_MB, 192): a road tile's cells still fit
SMALL_ROAD_PROCESSES = 2    # road-tile processes (earthd ROAD_PROCESSES; with one the road tiles never caught up)
MID_ROAD_PROCESSES = 3      # a 32 GB PC: the game and the system need the other 18 GB; 3 cut the road tiles round the
                            # car from ~25 s to ~10 s after the terrain (the user, 2026-11-08: "roads need to load in
                            # straight after terrain"), measured ~0.7 GB the process
MID_FAR_WORKERS = 2
# A desktop's road-tile processes by its threads and memory (each ~1-1.5 GB): driving off right after a teleport into
# Manhattan at 50 mph, 2 had the road under the car for the first ~15 s and solid ~27 s after, 4 at ~6 s and ~15 s,
# at the same frame rate (a 16-thread PC, 2026-10-25)
ROAD_PROCESSES_BY_CPU = ((12, 24.0, 4), (8, 16.0, 3))     # (threads at least, GB at least, processes)
SMALL_FAR_K = 0.8           # the far field's refinement factors (farfield.K_TABLE) times this: 204 tiles for 332
SMALL_FAR_TEX = 256         # px of the far tiles' textures at every level (512 from L2 on a desktop)
ARENA_MAX = 2               # glibc malloc arenas a process (every profile): one per thread kept freed memory ...
TRIM_S = 20.0               # ... and every TRIM_S the freed memory is given back (malloc_trim): a worker held ~1 GB
                            # for ~0.3 GB of Python and numpy objects (traced, a Steam Deck 2026-10-20)
SMALL_FAR_WORKERS = 2       # far-tile worker processes (earthd --workers)
SMALL_SERVER_SHARE = 0.5    # of the physical cores for the server (cpupin.SERVER_SHARE): one of four was too few
# The imagery's background analyses (de-shadowing, colour seams) off: on a Steam Deck they kept one of the server's
# threads busy (colour matching ~85 s of CPU in a Baltimore drive) and stream large arrays through the memory the GPU
# shares (on a desktop such a thread made the game's shadow pass 5-6x slower, pacer.py)
SMALL_ANALYSES_OFF = True
# The finished imagery pyramid's memory (imagery.MEM_FINISHED_MB of its JPEGs, MEM_DECODED tiles decoded at 192 KB) in
# every process that renders textures (the server; on a small machine its far-tile workers too, the analyses off):
# 160 MB + 384 on a desktop, ~230 MB a process; on a small machine FIN_MB_PER_GB per GB of RAM (at most the desktop's,
# at least SMALL_FIN_MB) and SMALL_FIN_DECODED (a rebase's textures share their neighbours' tiles: a far texture
# touches 9-16 of them)
FIN_MB_PER_GB = 4.0
SMALL_FIN_MB = 32
SMALL_FIN_DECODED = 96


def total_gb():
    """The machine's RAM in GB (None if unknown)."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    except (ValueError, OSError, AttributeError):
        pass
    try:                                    # Windows
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return m.ullTotalPhys / 1e9
    except Exception:
        pass
    return None


def free_gb():
    """The RAM free for new work now, in GB (Linux MemAvailable, Windows ullAvailPhys; None if unknown)."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024 / 1e9
    except (OSError, ValueError, IndexError):
        pass
    try:                                    # Windows
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return m.ullAvailPhys / 1e9
    except Exception:
        pass
    return None


def small(gb=None):
    """Is this a small machine (the small profile)? BNGE_BUDGET=small / desktop overrides."""
    force = os.environ.get("BNGE_BUDGET", "").lower()
    if force in ("small", "desktop"):
        return force == "small"
    gb = total_gb() if gb is None else gb
    return gb is not None and gb < SMALL_GB


SMALL_FACTS_CELLS = 192         # roads._FACTS_MEM entries on a small machine (~24 cells; a Steam Deck: ~9 server processes)

# --- every machine's caches scale with its RAM (2026-11-04 overhaul) -------------------------------------------
# The desktop sizes are per-process constants (dem 512 MB, mapdata 192, seacarve ~228, roadwidth ~140, heightmap
# 64-128, imagery ~330, facts ~48, deshadow ~32: ~1.5 GB a process in all), and the process count is the multiplier:
# a desktop's server held 13-17 GB in its 11-12 processes (budget's own note below; the user's Folkestone->London
# run measured headlessly at a 15.0 GB peak, docs/overhaul/ram-baseline.csv). Every process now gets the same share
# of the machine's RAM: CACHE_RAM_SHARE of it over the layout's process count (proc_count), and each module's
# constant is scaled by one factor against its desktop size (_scaled), never below its floor: the small profile's
# floors are the measured Steam-Deck minima (the sea floors and the imagery tiles keep the desktop sizes there:
# smaller, the Deck spent minutes re-reading them from disk), the desktop floors are half the desktop size.
FAR_WORKERS_DESKTOP = 3     # far-tile processes (earthd --workers' default) on a desktop
LAYOUT_FIXED_PROCS = 5      # main + block request + block prefetch + photo + far building (earthd's pools)
CACHE_RAM_SHARE = 0.20      # of the machine's RAM all server processes' caches together may hold
CACHE_RAM_SHARE_MID = 0.12  # ... on a 32 GB PC with the game and the system in the other 18 GB (2026-11-08)
DESKTOP_CACHE_MB = 1550.0   # the desktop per-process constants' sum: the scale's reference
CACHE_MIN_MB = 224.0        # a process's cache budget at least this (whatever the machine)
CACHE_MAX_MB = 1600.0       # ... and at most ~the desktop sizes (a 96 GB machine with 12 processes keeps them)
FLOORS_SMALL = {"dem": SMALL_DEM_TILES, "mapdata": SMALL_MAPDATA_MB, "walls": SMALL_WALL_CELLS,
                "facts": SMALL_FACTS_CELLS, "fin": SMALL_FIN_MB, "decoded": SMALL_FIN_DECODED,
                "sea": 24, "img": 512,           # (the Deck's measured keep-theirs, the module comment above)
                "combos": 6, "chunks": 128, "deshadow": 128, "plans": 12}
FLOORS_DESKTOP = {"dem": 256, "mapdata": 96, "walls": 24, "facts": 320, "fin": 80, "decoded": 192,
                  "sea": 12, "img": 256, "combos": 16, "chunks": 256, "deshadow": 256, "plans": 16}


def mid(gb=None):
    """A 32 GB-class desktop (not the small profile): the game and Windows already fill half the RAM."""
    gb = (total_gb() or 0.0) if gb is None else gb
    return not small(gb) and gb < MID_GB


def far_workers(gb=None):
    gb = (total_gb() or 0.0) if gb is None else gb
    return SMALL_FAR_WORKERS if small(gb) else (MID_FAR_WORKERS if mid(gb) else FAR_WORKERS_DESKTOP)


def proc_count(cpus=None, gb=None):
    """The server processes of this machine's layout (earthd's pools): the road tiles (road_processes), the far
    tiles (--workers), and the five fixed ones (main, both block lanes, photo, far building)."""
    return road_processes(cpus, gb) + LAYOUT_FIXED_PROCS + far_workers(gb)


def cache_mb(gb=None, cpus=None):
    """A process's cache budget in MB: CACHE_RAM_SHARE of the machine's RAM over its process count."""
    gb = total_gb() if gb is None else gb
    share = CACHE_RAM_SHARE_MID if (not small(gb)) and (gb or 32.0) < MID_GB else CACHE_RAM_SHARE
    per = (gb or 32.0) * 1000.0 * share / max(1, proc_count(cpus, gb))
    return min(CACHE_MAX_MB, max(CACHE_MIN_MB, per))


def cache_scale(gb=None, cpus=None):
    """The factor every desktop cache constant is scaled by (cache_mb() / DESKTOP_CACHE_MB)."""
    return cache_mb(gb, cpus) / DESKTOP_CACHE_MB


def _scaled(desktop, floor, s):
    return int(min(desktop, max(floor, round(desktop * s))))


def apply(is_small=None):
    """Set the cache sizes of this process's modules for the machine and the profile; returns whether it is the
    small one. Called in each process before its caches are made; idempotent (every size derives from the module
    constants here, not from a previous call's)."""
    is_small = small() if is_small is None else is_small
    global SHED_MB
    SHED_MB = SMALL_SHED_MB if is_small else (MID_SHED_MB if (total_gb() or 1e9) < MID_GB else 0)
    if os.environ.get("BNGE_SHED_MB"):              # (A/B: the ceiling in MB, 0 off)
        SHED_MB = int(os.environ["BNGE_SHED_MB"])
    s = cache_scale()
    F = FLOORS_SMALL if is_small else FLOORS_DESKTOP
    import dem
    import deshadow
    import grading
    import heightmap
    import imagery
    import mapdata
    import roadwidth
    import roads
    import seacarve
    dem.MEM_TILES = _scaled(512, F["dem"], s)               # decoded 512² float32 DEM tiles (1 MB each)
    mapdata.MEM_MB = _scaled(192, F["mapdata"], s)          # decoded cell layers (byte LRU)
    seacarve.MEM_CELLS = _scaled(24, F["sea"], s)           # sea-floor rasters (~9.5 MB each)
    roadwidth.WALL_CELLS = _scaled(48, F["walls"], s)       # cells of building walls (also on disk)
    roadwidth.COMBOS = _scaled(32, F["combos"], s)          # wall sets of a cell's edge neighbours
    heightmap.CHUNK_CACHE = _scaled(512, F["chunks"], s)    # 128²-sample lattice chunks (128 KB each)
    imagery.MEM_TILES = _scaled(512, F["img"], s)           # decoded 256² RGB source tiles (192 KB each)
    imagery.MEM_DECODED = _scaled(384, F["decoded"], s)     # finished tiles also decoded (192 KB each)
    deshadow.MEM_TILES = _scaled(512, F["deshadow"], s)     # relit tiles (uint8 256²)
    roads.FACTS_MEM_KEPT = _scaled(640, F["facts"], s)      # road-facts entries (~75 KB each; also on disk)
    grading.PLAN_MEM_MB = _scaled(32, F["plans"], s)        # a planner's cells' budget (P1.2's eviction)
    gb = total_gb() or 16.0
    if is_small:
        imagery.MEM_FINISHED_MB = int(min(160, max(F["fin"], round(FIN_MB_PER_GB * gb))))
        import farfield
        for cells, table in farfield.K_TABLE.items():       # (read when the server makes its Layout)
            farfield.K_TABLE[cells] = {lvl: k * SMALL_FAR_K for lvl, k in table.items()}
        for cells, tex in farfield.TEX_DEFAULT.items():
            farfield.TEX_DEFAULT[cells] = tuple(min(t, SMALL_FAR_TEX) for t in tex)
    if os.environ.get("BNGE_DEM_MEM_TILES"):        # (A/B: decoded DEM tiles kept per process)
        import dem
        dem.MEM_TILES = int(os.environ["BNGE_DEM_MEM_TILES"])
    elif not is_small:
        imagery.MEM_FINISHED_MB = _scaled(160, F["fin"], s)  # the finished pyramid's JPEGs (~44 KB each)
    # every machine: a desktop's server held 13-17 GB in its eleven processes (1-1.5 GB a worker, mostly freed memory
    # its 40-65 threads' arenas kept), next to the game; a 32 GB PC with 4 cores swapped (GitHub #496, 2026-10-30)
    _malloc_small()
    _win_trim()
    return is_small


def _libc():
    import ctypes
    import ctypes.util
    try:
        return ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
    except OSError:
        return None


_trimming = False

# A worker process over SHED_MB (resident + swapped) after a job drops its caches (each module registers how:
# register_shed) and gives the memory back: on a Steam Deck two Manhattan drives took a road worker to 1.6 GB and the
# server to ~5 GB PSS with 6.7 GB of swap until the system killed the game's UI (2026-11-04); the caches' own caps
# count entries, and the results kept on cached map features are counted nowhere. 0 = off (a desktop: apply()).
SHED_MB = 0
SMALL_SHED_MB = 1400     # (1200 on a Deck in Manhattan: two road workers dropped every ~15 s, 1206 -> 822 MB, their working set)
MID_GB = 44.0            # a 32 GB PC (34.3 GB total) with the game and traffic: its 12 processes' caches filled the RAM
MID_SHED_MB = 1000       # (tightened for a 32 GB PC sharing RAM with the game and Windows)
SHED_MIN_S = 90.0        # a worker drops its caches at most this often (a dense city's working set comes straight back)
_shedders = []
shed_count = 0
_last_shed = None


def register_shed(fn):
    """fn(): drop a cache of this process (called between jobs, in the worker's own thread)."""
    _shedders.append(fn)


def memory_mb():
    """This process's resident + swapped memory in MB (Linux /proc; Windows: private bytes), or None."""
    try:
        rss = swap = 0
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1])
                elif line.startswith("VmSwap:"):
                    swap = int(line.split()[1])
        return (rss + swap) / 1024.0
    except (OSError, ValueError):
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                                   "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                                   "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                                                   "PrivateUsage")]
            c = PMC()
            c.cb = ctypes.sizeof(c)
            k32 = ctypes.WinDLL("kernel32")
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi = ctypes.WinDLL("psapi")
            if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
                return c.PrivateUsage / 2 ** 20
        except Exception:
            pass
    return None


def maybe_shed():
    """Between jobs: the registered caches dropped when this process is over SHED_MB. True when it shed."""
    if not SHED_MB or not _shedders:
        return False
    import time
    global _last_shed
    if _last_shed is not None and time.monotonic() - _last_shed < SHED_MIN_S:
        return False
    mb = memory_mb()
    if mb is None or mb < SHED_MB:
        return False
    _last_shed = time.monotonic()
    _shed(mb, f"over {SHED_MB}")
    return True


def _shed(mb, why):
    """The registered caches dropped and the memory given back (maybe_shed, the idle shed)."""
    global shed_count
    import gc
    import logging
    for fn in list(_shedders):
        try:
            fn()
        except Exception:
            pass
    gc.collect()
    trim()
    shed_count += 1
    after = memory_mb()
    logging.getLogger("earthd.worker").info("memory: pid %d at %.0f MB (%s): caches dropped, now %.0f MB",
                                            os.getpid(), mb or 0, why, after or 0)


# A worker that has run no job for IDLE_SHED_S drops its caches once (every profile): the far-tile workers the 3D map
# set going, every worker on a stock level, kept what they had for as long as the game ran (a desktop: 5.7 -> 9.3 GB in
# 17 workers after a minute of the 3D map, still there on West Coast USA; GitHub #1026). The next job reads its cells
# from disk again. A job waits for a shed under way (JOB_LOCK, pools._call).
IDLE_SHED_S = float(os.environ.get("BNGE_IDLE_SHED_S", "300"))     # (0: off)
IDLE_CHECK_S = 30.0
JOB_LOCK = None
_last_job_end = None
_idle_shed = False
_idle_thread = False


def job_lock():
    """The lock a worker's job holds (pools._call), made once."""
    global JOB_LOCK
    if JOB_LOCK is None:
        import threading
        JOB_LOCK = threading.Lock()
    return JOB_LOCK


def job_ended():
    """A job finished in this worker (pools._call): the idle clock starts again, its watcher started once."""
    global _last_job_end, _idle_shed, _idle_thread
    import time
    _last_job_end, _idle_shed = time.monotonic(), False
    if not _idle_thread and IDLE_SHED_S > 0 and _shedders:
        import threading
        _idle_thread = True
        threading.Thread(target=_idle_loop, daemon=True, name="idle-shed").start()


def idle_shed_now(now=None):
    """The idle shed's test: True when it shed (idle IDLE_SHED_S since the last job, not shed since, no job running)."""
    global _idle_shed
    import time
    now = time.monotonic() if now is None else now
    if IDLE_SHED_S <= 0 or _idle_shed or _last_job_end is None or now - _last_job_end < IDLE_SHED_S or not _shedders:
        return False
    lock = job_lock()
    if not lock.acquire(blocking=False):
        return False
    try:
        if _idle_shed or now - _last_job_end < IDLE_SHED_S:
            return False
        _idle_shed = True
        _shed(memory_mb(), f"idle {now - _last_job_end:.0f} s")
        return True
    finally:
        lock.release()


def _idle_loop():
    import time
    while True:
        time.sleep(IDLE_CHECK_S)
        try:
            idle_shed_now()
        except Exception:
            pass


def _malloc_small():
    """Linux (glibc): at most ARENA_MAX malloc arenas, and the freed memory given back every TRIM_S (a thread)."""
    global _trimming
    if not sys.platform.startswith("linux") or _trimming:
        return
    libc = _libc()
    if libc is None or not hasattr(libc, "malloc_trim"):
        return
    try:
        libc.mallopt(-8, ARENA_MAX)         # M_ARENA_MAX
    except Exception:
        pass
    import threading
    import time

    def loop():
        while True:
            time.sleep(TRIM_S)
            try:
                libc.malloc_trim(0)
            except Exception:
                return
    _trimming = True
    threading.Thread(target=loop, daemon=True, name="malloc-trim").start()


def _win_trim():
    """Windows (opt-in, BNGE_TRIM=1): every TRIM_S the process's working set is handed back (EmptyWorkingSet: the
    pages go to the standby list, and the hot ones fault back warm). On by default it made every server process
    (12 here) empty its working set every 20 s: a soft-fault storm that lagged the whole PC and did not lower the
    committed memory (2026-11-04). `trim()` still runs after a prune, where handing pages back is wanted."""
    global _trimming
    if sys.platform != "win32" or _trimming or os.environ.get("BNGE_TRIM", "0") != "1":
        return
    import ctypes
    import threading
    import time
    try:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.EmptyWorkingSet.argtypes = [ctypes.c_void_p]
    except OSError:
        return

    def loop():
        h = k32.GetCurrentProcess()
        while True:
            time.sleep(TRIM_S)
            try:
                psapi.EmptyWorkingSet(h)
            except OSError:
                return
    _trimming = True
    threading.Thread(target=loop, daemon=True, name="wset-trim").start()


def rss_mb(pid=None):
    """A process's working set in MB (this process by default); None when it cannot be read."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            psapi = ctypes.WinDLL("psapi")
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            if pid is None:
                h, close = k32.GetCurrentProcess(), False
            else:
                k32.OpenProcess.restype = wintypes.HANDLE
                k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                h = k32.OpenProcess(0x0400 | 0x0010, False, int(pid))   # QUERY_INFORMATION | VM_READ
                close = True
            if not h:
                return None
            try:
                c = PMC()
                c.cb = ctypes.sizeof(PMC)
                if psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
                    return c.WorkingSetSize / 1e6
            finally:
                if close:
                    k32.CloseHandle(h)
        else:
            with open(f"/proc/{pid if pid is not None else 'self'}/statm") as f:
                return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except Exception:
        pass
    return None


def trim():
    """Give the freed memory back now (glibc malloc_trim; Windows: the working set; nothing elsewhere)."""
    if sys.platform.startswith("linux"):
        libc = _libc()
        if libc is not None and hasattr(libc, "malloc_trim"):
            libc.malloc_trim(0)
    elif sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi = ctypes.WinDLL("psapi")
            psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
            psapi.EmptyWorkingSet.restype = wintypes.BOOL
            psapi.EmptyWorkingSet(k32.GetCurrentProcess())
        except OSError:
            pass


# Windows has no niceness: priority classes. The server's own (earthd --nice, default 10) is "below normal"; its workers
# keep it. Without it all nine server processes ran at the game's own priority (start.py passed --nice 0 on Windows,
# the lowering never ran; 2026-10-21). Not "idle" for the far field's and far buildings' workers (1.6.0 to 1.6.1):
# Windows runs a lower class only when no thread of a higher one is ready, and the server has its own few cores
# (cpupin.split: 2 of a 6-core CPU), which the road and block workers can fill for minutes on a first visit; then the
# far workers got nothing at all (a Windows player, i5-9400F, 1.6.1: "distant land 5/514", only the land near the car
# loading). os.nice elsewhere only weights the share.
WIN_BELOW_NORMAL = 0x4000


def lower_priority(nice, worker=False):
    """This process below the game: os.nice(nice) where it exists; on Windows the "below normal" class for the
    server (worker False, nice > 0); a worker keeps the class it inherited. Returns the change made, for the log
    ("nice +n", "below normal", None)."""
    if not nice or nice <= 0:
        return None
    if hasattr(os, "nice"):
        os.nice(int(nice))
        return f"nice +{int(nice)}"
    if sys.platform != "win32" or worker:
        return None
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # 64-bit handles: without these the pseudo handle went in as a 32-bit int and the call failed (as earthd's did)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.SetPriorityClass.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k32.SetPriorityClass.restype = wintypes.BOOL
    if not k32.SetPriorityClass(k32.GetCurrentProcess(), WIN_BELOW_NORMAL):
        raise OSError("SetPriorityClass(below normal) failed")
    return "below normal"


# The server's event loop answers every message the game sends (a pose, a block request). In the "below normal" class
# its thread waited for the interpreter lock like the ~20 busy others of the process: at 200 mph on a first visit (A13,
# 2 server cores, Windows; 2026-11-04) it lagged 0.5-1 s in the median and up to 15 s (GitHub #725). One step above
# them it is still inside the process's class, below the game's "normal".
WIN_THREAD_ABOVE_NORMAL = 1     # THREAD_PRIORITY_ABOVE_NORMAL


def raise_thread():
    """The calling thread one step above the others of its process (Windows; the event loop's, earthd.LOOP_RAISE).
    Returns the change made, for the log ("thread above normal", None: nothing to do elsewhere)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # (64-bit handles, as lower_priority's)
    k32.GetCurrentThread.restype = wintypes.HANDLE
    k32.SetThreadPriority.argtypes = (wintypes.HANDLE, ctypes.c_int)
    k32.SetThreadPriority.restype = wintypes.BOOL
    if not k32.SetThreadPriority(k32.GetCurrentThread(), WIN_THREAD_ABOVE_NORMAL):
        raise OSError("SetThreadPriority(above normal) failed")
    return "thread above normal"


def road_processes(cpus=None, gb=None):
    """Road-tile processes for a desktop (ROAD_PROCESSES_BY_CPU), 2 below them."""
    cpus = (os.cpu_count() or 1) if cpus is None else cpus
    gb = (total_gb() or 0.0) if gb is None else gb
    for c, g, n in ROAD_PROCESSES_BY_CPU:
        if cpus >= c and gb >= g:
            return min(n, MID_ROAD_PROCESSES) if mid(gb) else n
    return SMALL_ROAD_PROCESSES

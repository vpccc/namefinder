"""Name Finder scanning engine and desktop launcher."""
from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future, wait, FIRST_COMPLETED
from functools import partial
from pathlib import Path

from core import CONFIRM_URLS, PLATFORM_NAMES, UBISOFT_SPEEDS, DISCORD_SPEEDS, PLATFORMS, lookup_discord_name, SPEEDS, RequestGate, make_alphabet, History, POTENTIAL, candidates, lookup_name, webhook_url
from ubisoft import lookup_ubisoft_name, session_headers

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"


def scan(names, delay, webhook, stop, events, workers=1, platform="minecraft", ticket="", session_id=""):
    history = None
    gate = None
    checked = found = 0
    failed = False
    started = time.monotonic()
    try:
        if not 1 <= workers <= 8 or not 0 <= delay <= 3600:
            raise ValueError("Invalid scan speed")
        if platform not in PLATFORM_NAMES:
            raise ValueError("Unknown platform")
        if platform == "discord":
            workers, delay = 1, max(1.0, delay)
        lookup = lookup_discord_name if platform == "discord" else lookup_name
        if platform == "ubisoft":
            session_headers(ticket, session_id)
            workers, delay = 1, max(3.0, delay)
            lookup = partial(lookup_ubisoft_name, ticket=ticket, session_id=session_id)
        DATA.mkdir(exist_ok=True)
        history = History(DATA / "history.sqlite3", platform=platform)
        gate = RequestGate(delay)
        if platform == "ubisoft":
            cooldown = history.cooldown_remaining()
            if cooldown > 0:
                failed = True
                events.put(("error", f"Ubisoft is still cooling down. Try again in {cooldown:.0f} seconds."))
                return
        remaining = iter(names)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=platform) as pool:
            pending = {}

            def fill_slots():
                while len(pending) < workers and not stop.is_set():
                    name = next(remaining, None)
                    if name is None:
                        break
                    cached = platform == "ubisoft" and history.recently_taken(name)
                    if cached:
                        future = Future()
                        future.set_result(("Taken", "Cached taken result (less than 24 hours old)"))
                    else:
                        future = pool.submit(lookup, name, stop,
                            report=lambda msg: events.put(("message", msg)), gate=gate)
                    pending[future] = (name, cached)

            fill_slots()
            try:
                while pending and not stop.is_set():
                    ready, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                    for future in ready:
                        name, cached = pending.pop(future)
                        status, detail = future.result()
                        if stop.is_set() or status == "Cancelled":
                            break
                        checked += 1
                        if not cached:
                            history.record(name, status)
                        delivery = detail if cached else "—"
                        if status == POTENTIAL:
                            found += 1
                            delivery = history.notify(webhook, name, stop)
                        events.put(("result", (name, status, delivery if status != "Unknown" else detail)))
                        events.put(("counts", (checked, found, len(names))))
                        if status == "Unknown":
                            failed = True
                            stop.set()
                            events.put(("error", f"{name}: {detail}. Search stopped; try again later."))
                            break
                    fill_slots()
            finally:
                # Only a bounded window is submitted. Cancel queued work and interrupt
                # cooldowns before waiting for any active HTTP requests to finish.
                if pending:
                    stop.set()
                    for future in pending:
                        future.cancel()
    except Exception as exc:
        failed = True
        events.put(("error", f"Stopped because of {type(exc).__name__}. Check your connection and free disk space, then restart the batch."))
    finally:
        if history is not None:
            if platform == "ubisoft" and gate is not None:
                history.save_cooldown(max(0, gate.blocked_until - time.monotonic()))
            history.close()
        seconds = max(0.001, time.monotonic() - started)
        outcome = "Stopped with an error" if failed else "Stopped" if stop.is_set() else "Finished"
        events.put(("done", f"{outcome} • {checked} checked • {found} potentially available • {seconds:.1f}s ({checked/seconds:.1f} names/s)"))



if __name__ == "__main__":
    from desktop import launch
    launch(scan)

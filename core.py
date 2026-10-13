"""Candidate generation, Minecraft/Discord lookup, platform history and alerts."""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import sqlite3
import string
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

LOOKUP_BASE = "https://api.minecraftservices.com/minecraft/profile/lookup/name/"
DISCORD_LOOKUP = "https://discord.com/api/v9/unique-username/username-attempt-unauthed"
POTENTIAL = "Potentially available"
SPEEDS = {"Gentle": (1, 1.0), "Fast": (4, 0.2), "Turbo": (8, 0.1)}
DISCORD_SPEEDS = {"Gentle": (1, 5.0), "Fast": (1, 2.0), "Turbo": (1, 1.0)}
UBISOFT_SPEEDS = {"Gentle": (1, 10.0), "Fast": (1, 5.0), "Turbo": (1, 3.0)}
PLATFORMS = {"Minecraft Java": "minecraft", "Discord": "discord", "Ubisoft / Rainbow Six Siege": "ubisoft"}
PLATFORM_NAMES = {"minecraft": "Minecraft", "discord": "Discord", "ubisoft": "Ubisoft"}
CONFIRM_URLS = {"minecraft": "https://www.minecraft.net/profile", "discord": "https://discord.com/channels/@me", "ubisoft": "https://account.ubisoft.com/"}
WORD_FILE = Path(__file__).resolve().parent / "dictionary" / "english.txt"


class RequestGate:
    """One shared request schedule and cooldown for every lookup worker."""
    def __init__(self, interval: float):
        self.interval = interval
        self.next_request = 0.0
        self.blocked_until = 0.0
        self.lock = threading.Lock()

    def wait(self, stop: threading.Event) -> bool:
        while not stop.is_set():
            with self.lock:
                now = time.monotonic()
                remaining = max(self.next_request, self.blocked_until) - now
                if remaining <= 0:
                    self.next_request = now + self.interval
                    return True
            if stop.wait(min(remaining, 0.1)):
                break
        return False

    def cooldown(self, seconds: float):
        with self.lock:
            self.blocked_until = max(self.blocked_until, time.monotonic() + seconds)
            self.interval = max(self.interval, min(3.0, max(0.5, self.interval * 2)))

ALPHABETS = {
    "Letters + numbers + underscore": string.ascii_lowercase + string.digits + "_",
    "Letters + numbers": string.ascii_lowercase + string.digits,
    "Letters only": string.ascii_lowercase,
}


def make_alphabet(letters: bool, numbers: bool, underscores: bool, periods: bool = False, dashes: bool = False) -> str:
    alphabet = (string.ascii_lowercase if letters else "") + (string.digits if numbers else "") + ("_" if underscores else "") + ("." if periods else "") + ("-" if dashes else "")
    if not alphabet:
        raise ValueError("Select at least one character type.")
    return alphabet


def candidates(count: int, alphabet: str, pattern: str = "", supplied: str = "", length: int = 4, platform: str = "minecraft", source: str = "random") -> list[str]:
    if platform not in PLATFORM_NAMES:
        raise ValueError("Choose Minecraft Java, Discord or Ubisoft.")
    if source not in {"random", "dictionary"}:
        raise ValueError("Choose random characters or English words.")
    if length not in (3, 4, 5):
        raise ValueError("Choose a name length of 3, 4 or 5 characters.")
    alphabet = "".join(dict.fromkeys(alphabet.lower()))
    allowed = string.ascii_lowercase + string.digits + {"minecraft": "_", "discord": "_.", "ubisoft": "_.-"}[platform]
    if not alphabet or not set(alphabet) <= set(allowed):
        raise ValueError("Invalid characters: Minecraft allows _; Discord allows _ and .; Ubisoft allows _, . and -.")
    if not 1 <= count <= 5000:
        raise ValueError("Batch size must be between 1 and 5,000.")
    if supplied.strip():
        names = list(dict.fromkeys(x.lower() for x in re.split(r"[\s,;]+", supplied.strip())))
        if any(len(x) != length or not set(x) <= set(alphabet) for x in names):
            raise ValueError(f"Every pasted name must have exactly {length} characters and use only the selected character types.")
        if platform == "discord" and any(".." in x for x in names):
            raise ValueError("Discord usernames cannot contain two consecutive periods (..).")
        if platform == "ubisoft" and any(x[0] not in string.ascii_lowercase or "ubi" in x for x in names):
            raise ValueError("Ubisoft names must start with a letter and cannot contain 'ubi'.")
        if len(names) > 5000:
            raise ValueError("Paste at most 5,000 names per batch.")
        return names
    pattern = pattern.strip().lower() or "?" * length
    if len(pattern) != length or not set(pattern) <= set(alphabet + "?"):
        raise ValueError(f"Use a {length}-character pattern with ? and only the selected character types, or leave it blank.")
    if platform == "discord" and ".." in pattern:
        raise ValueError("Discord usernames cannot contain two consecutive periods (..).")
    first_letters = "".join(c for c in alphabet if c in string.ascii_lowercase)
    if platform == "ubisoft" and (not first_letters or pattern[0] not in first_letters + "?" or "ubi" in pattern):
        raise ValueError("Ubisoft names must start with a letter and cannot contain 'ubi'. Enable letters and check the pattern.")
    if source == "dictionary":
        if not first_letters:
            raise ValueError("Enable letters to search English words.")
        try:
            words = WORD_FILE.read_text(encoding="utf-8").splitlines()
        except OSError:
            raise ValueError("The English dictionary is missing. Extract the whole ZIP, including the dictionary folder.") from None
        matches = sorted({w for w in words if re.fullmatch(r"[a-z]{3,5}", w)
                          and len(w) == length and set(w) <= set(alphabet)
                          and all(p == "?" or p == c for p, c in zip(pattern, w))
                          and (platform != "ubisoft" or "ubi" not in w)})
        if not matches:
            raise ValueError("No English words match this length and pattern. Try a blank pattern with letters enabled.")
        return random.sample(matches, min(count, len(matches)))
    slot_alphabets = {i: first_letters if platform == "ubisoft" and i == 0 else alphabet
                      for i, c in enumerate(pattern) if c == "?"}
    total = math.prod(len(chars) for chars in slot_alphabets.values())
    result = []
    # Lazy random permutation of the search space: no huge list, no duplicate
    # draws, and dot-invalid names can be skipped without underfilling a batch.
    remaining = total
    swaps = {}
    while remaining and len(result) < count:
        index = random.randrange(remaining)
        number = swaps.get(index, index)
        remaining -= 1
        swaps[index] = swaps.get(remaining, remaining)
        swaps.pop(remaining, None)
        chars = list(pattern)
        for i in range(length - 1, -1, -1):
            if chars[i] == "?":
                letters = slot_alphabets[i]
                number, digit = divmod(number, len(letters))
                chars[i] = letters[digit]
        name = "".join(chars)
        if (platform != "discord" or ".." not in name) and (platform != "ubisoft" or "ubi" not in name):
            result.append(name)
    if not result:
        raise ValueError("No valid names match these settings. For Discord, periods alone cannot form a 3–5 character name.")
    return result


def parse_discord_lookup(status: int, body: bytes) -> tuple[str, str]:
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        return "Unknown", "Discord returned an unreadable response or verification page"
    if not isinstance(data, dict):
        return "Unknown", "Discord returned an unexpected response"
    if any(key.startswith("captcha") for key in data):
        return "Unknown", "Discord requested human verification; automated checking stopped"
    if status == 200 and type(data.get("taken")) is bool:
        if data["taken"]:
            return "Taken", "Discord reports this username is taken"
        return POTENTIAL, "Discord reports this username is not taken; confirm in your account settings"
    return "Unknown", f"Unrecognized Discord response (HTTP {status}); no availability result"


def lookup_discord_name(name: str, stop: threading.Event, opener=None, report=None, gate=None) -> tuple[str, str]:
    name = name.lower()
    if not re.fullmatch(r"[a-z0-9_.]{3,5}", name) or ".." in name:
        raise ValueError("Use 3–5 letters, numbers, underscores or periods; no consecutive periods.")
    opener = opener or urllib.request.build_opener(NoRedirect())
    for attempt in range(3):
        if stop.is_set() or (gate is not None and not gate.wait(stop)):
            return "Cancelled", "Stopped"
        request = urllib.request.Request(DISCORD_LOOKUP, json.dumps({"username": name}).encode(),
            {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "NameFinder/6.0"})
        delay = 10 * 2 ** attempt
        try:
            with opener.open(request, timeout=15) as response:
                return parse_discord_lookup(response.status, response.read(65536))
        except urllib.error.HTTPError as exc:
            body = exc.read(65536)
            if exc.code in {401, 403}:
                return "Unknown", "Discord blocked this check or requires verification; try manually in Discord"
            try:
                data = json.loads(body)
            except (ValueError, UnicodeError):
                data = {}
            if isinstance(data, dict) and any(key.startswith("captcha") for key in data):
                return "Unknown", "Discord requested human verification; automated checking stopped"
            if exc.code not in {429, 500, 502, 503, 504}:
                return parse_discord_lookup(exc.code, body)
            delay = retry_delay(exc.headers, delay)
            if exc.code == 429 and isinstance(data, dict):
                try:
                    seconds = float(data.get("retry_after", delay))
                    if math.isfinite(seconds):
                        delay = max(delay, seconds)
                except (ValueError, TypeError):
                    pass
            reason = "Discord lookup rate limit" if exc.code == 429 else f"Discord HTTP {exc.code}"
            if gate is not None:
                gate.cooldown(delay)
        except (urllib.error.URLError, TimeoutError, OSError):
            reason = "Discord lookup network error"
        if attempt == 2 or delay > 120:
            return "Unknown", f"{reason}; stop and try later"
        if report:
            report(f"{reason}. Retrying {name} in {delay:g} seconds…")
        if stop.wait(delay):
            return "Cancelled", "Stopped"
    return "Unknown", "Discord lookup failed"


def parse_lookup(name: str, status: int, body: bytes) -> tuple[str, str]:
    """Only a specific JSON profile-not-found response is a potential name."""
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        return "Unknown", "Minecraft returned an unreadable response"
    if not isinstance(data, dict):
        return "Unknown", "Minecraft returned an unexpected response"
    if (status == 200 and isinstance(data.get("name"), str)
            and data["name"].lower() == name.lower()
            and isinstance(data.get("id"), str)
            and re.fullmatch(r"[0-9a-fA-F]{32}", data["id"])):
        return "Taken", "An existing Java profile uses this name"
    if (status == 404
            and data.get("path") == f"/minecraft/profile/lookup/name/{name}"
            and data.get("errorMessage") == f"Couldn't find any profile with name {name}"):
        return POTENTIAL, "No Java profile found; reserved or blocked names may still be unclaimable"
    return "Unknown", f"Unrecognized Minecraft response (HTTP {status})"


def retry_delay(headers, fallback: float) -> float:
    value = headers.get("Retry-After") if headers else None
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return fallback
    return max(3, seconds) if math.isfinite(seconds) else fallback


def lookup_name(name: str, stop: threading.Event, opener=None, report=None, gate=None) -> tuple[str, str]:
    if not re.fullmatch(r"[a-zA-Z0-9_]{3,5}", name):
        raise ValueError("Choose a 3-, 4- or 5-character username using letters, numbers or underscores.")
    name = name.lower()
    opener = opener or urllib.request.build_opener(NoRedirect())
    for attempt in range(3):
        if stop.is_set():
            return "Cancelled", "Stopped"
        if gate is not None and not gate.wait(stop):
            return "Cancelled", "Stopped"
        request = urllib.request.Request(LOOKUP_BASE + name,
            headers={"Accept": "application/json", "User-Agent": "NameFinder/6.0"})
        delay = 5 * 2 ** attempt
        try:
            with opener.open(request, timeout=15) as response:
                return parse_lookup(name, response.status, response.read(65536))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return parse_lookup(name, 404, exc.read(65536))
            if exc.code not in {429, 500, 502, 503, 504}:
                return "Unknown", f"Minecraft HTTP {exc.code}; no availability result"
            reason = "Minecraft rate limit" if exc.code == 429 else f"Minecraft HTTP {exc.code}"
            delay = retry_delay(exc.headers, delay)
            if gate is not None and exc.code in {429, 503}:
                gate.cooldown(delay)
        except (urllib.error.URLError, TimeoutError, OSError):
            reason = "Minecraft network error"
        if attempt == 2 or delay > 120:
            return "Unknown", f"{reason}; stop and try later"
        if report:
            report(f"{reason}. Retrying {name} in {delay:g} seconds…")
        if stop.wait(delay):
            return "Cancelled", "Stopped"
    return "Unknown", "Lookup failed"


def webhook_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parts = urllib.parse.urlsplit(value)
    if (parts.scheme != "https" or parts.netloc not in {"discord.com", "discordapp.com"}
            or not re.fullmatch(r"/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9_-]+/?", parts.path)
            or parts.fragment):
        raise ValueError("Paste a Discord webhook URL starting with https://discord.com/api/webhooks/.")
    query = urllib.parse.parse_qs(parts.query)
    extra = {"wait": "true"}
    if "thread_id" in query:
        thread = query["thread_id"][0]
        if not thread.isdigit():
            raise ValueError("Discord thread_id must be a number.")
        extra["thread_id"] = thread
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"),
                                   urllib.parse.urlencode(extra), ""))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_discord(url: str, name: str, stop: threading.Event, opener=None, platform="minecraft") -> tuple[bool, str]:
    url = webhook_url(url)
    if not url:
        return False, "Webhook not configured"
    if platform not in PLATFORM_NAMES:
        raise ValueError("Unknown platform")
    target = CONFIRM_URLS[platform]
    description = (f"Discord's unauthenticated check reports **`{name}`** is not taken.\n"
                   "Confirm in Discord: User Settings → My Account → Username.\n"
                   "Availability can change; this does not reserve or claim the name.") if platform == "discord" else (
                   f"Minecraft's public lookup found no Java profile for **`{name}`**.\n"
                   "Confirm you can claim it on [Minecraft](https://www.minecraft.net/profile).\n"
                   "Reserved or blocked names may still be unavailable. This is not a guarantee.")
    if platform == "ubisoft":
        description = (f"Ubisoft returned no matching PC (uplay) profile for **`{name}`**.\n"
                       "Confirm in your [Ubisoft account](https://account.ubisoft.com/) before choosing it.\n"
                       "Reserved, blocked or legacy names may still be unavailable. This does not reserve the name.")
    payload = {
        "username": "Name Finder",
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": f"{PLATFORM_NAMES[platform]}: {name} — potentially available",
            "url": target,
            "description": description,
            "color": 0x44D7A8,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }],
    }
    opener = opener or urllib.request.build_opener(NoRedirect())
    for attempt in range(3):
        if stop.is_set():
            return False, "Cancelled"
        request = urllib.request.Request(url, json.dumps(payload).encode(),
                                         {"Content-Type": "application/json", "User-Agent": "NameFinder/6.0"})
        try:
            with opener.open(request, timeout=15) as response:
                if 200 <= response.status < 300:
                    return True, "Sent"
                return False, f"Discord HTTP {response.status}"
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                return False, f"Discord HTTP {exc.code}; check the webhook and channel"
            try:
                delay = float(json.loads(exc.read(65536)).get("retry_after", 5))
            except (ValueError, TypeError, AttributeError):
                delay = 5
            if not math.isfinite(delay) or delay > 120 or attempt == 2:
                return False, "Discord rate limited; retry the name later"
            if stop.wait(max(1, delay)):
                return False, "Cancelled"
        except (urllib.error.URLError, TimeoutError, OSError):
            # No exception text: network errors can contain a secret webhook URL.
            return False, "Discord network error; delivery unconfirmed"
    return False, "Discord delivery failed"


class History:
    def __init__(self, path: Path, platform="minecraft"):
        if platform not in PLATFORM_NAMES:
            raise ValueError("Unknown platform")
        self.platform = platform
        prefix = "" if platform == "minecraft" else platform + "_"
        self.results_table = prefix + "results"
        self.sent_table = prefix + "sent"
        self.db = sqlite3.connect(path)
        self.db.execute(f"CREATE TABLE IF NOT EXISTS {self.results_table} (name TEXT PRIMARY KEY, status TEXT, checked TEXT)")
        self.db.execute(f"CREATE TABLE IF NOT EXISTS {self.sent_table} (destination TEXT, name TEXT, PRIMARY KEY(destination,name))")
        self.db.execute("CREATE TABLE IF NOT EXISTS cooldowns (platform TEXT PRIMARY KEY, until REAL)")
        self.db.commit()

    def recently_taken(self, name: str, max_age: float = 86400) -> bool:
        row = self.db.execute(f"SELECT status, checked FROM {self.results_table} WHERE name=?", (name.lower(),)).fetchone()
        if not row or row[0] != "Taken":
            return False
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(row[1])).total_seconds()
            return 0 <= age < max_age
        except (ValueError, TypeError):
            return False

    def cooldown_remaining(self) -> float:
        row = self.db.execute("SELECT until FROM cooldowns WHERE platform=?", (self.platform,)).fetchone()
        return max(0, row[0] - time.time()) if row else 0

    def save_cooldown(self, seconds: float):
        if seconds > 0:
            until = time.time() + max(seconds, self.cooldown_remaining())
            self.db.execute("INSERT OR REPLACE INTO cooldowns VALUES (?,?)", (self.platform, until))
            self.db.commit()

    def record(self, name: str, status: str):
        self.db.execute(f"INSERT OR REPLACE INTO {self.results_table} VALUES (?,?,?)",
                        (name.lower(), status, datetime.now(timezone.utc).isoformat()))
        self.db.commit()

    def notify(self, url: str, name: str, stop: threading.Event, sender=None) -> str:
        if not url:
            return "Off"
        destination = hashlib.sha256(webhook_url(url).encode()).hexdigest()
        name = name.lower()
        if self.db.execute(f"SELECT 1 FROM {self.sent_table} WHERE destination=? AND name=?", (destination, name)).fetchone():
            return "Already sent"
        ok, message = sender(url, name, stop) if sender else send_discord(url, name, stop, platform=self.platform)
        if ok:
            self.db.execute(f"INSERT OR IGNORE INTO {self.sent_table} VALUES (?,?)", (destination, name))
            self.db.commit()
        return message

    def close(self):
        self.db.close()

"""Read-only Ubisoft PC profile lookup using the user's temporary session."""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

from core import NoRedirect, POTENTIAL, retry_delay

PROFILE_URL = "https://public-ubiservices.ubi.com/v3/profiles"
# Public application identifier from Ubisoft's official account website.
APP_ID = "74e71609-1ddf-47da-9073-71ac3aa8c90c"
UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"


def session_headers(ticket: str, session_id: str = "") -> dict[str, str]:
    ticket = ticket.strip()
    if ticket.startswith("Ubi_v1 t="):
        ticket = ticket[len("Ubi_v1 t="):]
    if not re.fullmatch(r"[A-Za-z0-9_+/=.\-]{16,8192}", ticket):
        raise ValueError("Add your Ubisoft Authorization value in Settings > Ubisoft session.")
    session_id = session_id.strip()
    if session_id and not re.fullmatch(UUID, session_id):
        raise ValueError("The optional Ubisoft session ID must be a UUID. Leave it blank if unavailable.")
    headers = {"Authorization": "Ubi_v1 t=" + ticket, "Ubi-AppId": APP_ID,
               "Accept": "application/json", "User-Agent": "NameFinder/6.0"}
    if session_id:
        headers["Ubi-SessionId"] = session_id
    return headers


def parse_ubisoft_lookup(name: str, status: int, body: bytes) -> tuple[str, str]:
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        return "Unknown", "Ubisoft returned an unreadable response or verification page"
    if status != 200 or not isinstance(data, dict):
        return "Unknown", f"Unexpected Ubisoft response (HTTP {status})"
    if any(k in data for k in ("errorCode", "error", "message", "httpCode")) or any("captcha" in k.lower() for k in data):
        return "Unknown", "Ubisoft returned an error or verification request"
    profiles = data.get("profiles")
    if not isinstance(profiles, list):
        return "Unknown", "Ubisoft did not return a profile list"
    if not profiles:
        return POTENTIAL, "No matching Ubisoft PC profile; reserved, blocked or legacy names may still be unavailable"
    for profile in profiles:
        if (not isinstance(profile, dict) or profile.get("platformType") != "uplay"
                or not isinstance(profile.get("nameOnPlatform"), str)
                or profile["nameOnPlatform"].lower() != name.lower()
                or not isinstance(profile.get("profileId"), str)
                or not re.fullmatch(UUID, profile["profileId"])):
            return "Unknown", "Ubisoft returned an unexpected or mismatched profile"
    return "Taken", "An existing Ubisoft PC profile uses this name"


def lookup_ubisoft_name(name: str, stop: threading.Event, opener=None, report=None, gate=None,
                        *, ticket: str, session_id: str = "") -> tuple[str, str]:
    name = name.lower()
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,4}", name) or "ubi" in name:
        raise ValueError("Use a 3–5 character Ubisoft name starting with a letter, without 'ubi'.")
    headers = session_headers(ticket, session_id)
    opener = opener or urllib.request.build_opener(NoRedirect())
    url = PROFILE_URL + "?" + urllib.parse.urlencode({"platformType": "uplay", "nameOnPlatform": name})
    for attempt in range(3):
        if stop.is_set() or (gate is not None and not gate.wait(stop)):
            return "Cancelled", "Stopped"
        delay = 10 * 2 ** attempt
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with opener.open(request, timeout=15) as response:
                return parse_ubisoft_lookup(name, response.status, response.read(65536))
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return "Unknown", "Ubisoft session is missing, expired or rejected; obtain a fresh ticket from your own account"
            if exc.code == 403:
                return "Unknown", "Ubisoft blocked the request or requires verification; continue on the official account website"
            if exc.code == 429:
                # Stop immediately; preserve the server's cooldown across app restarts.
                delay = max(60, retry_delay(exc.headers, 300))
                if gate is not None:
                    gate.cooldown(delay)
                return "Unknown", f"Ubisoft rate limit; wait at least {delay:.0f} seconds before retrying"
            if exc.code not in {500, 502, 503, 504}:
                return "Unknown", f"Ubisoft rejected this lookup (HTTP {exc.code}); no availability result"
            delay = retry_delay(exc.headers, delay)
            reason = f"Ubisoft HTTP {exc.code}"
            if gate is not None:
                gate.cooldown(delay)
        except (urllib.error.URLError, TimeoutError, OSError):
            # Never echo exceptions that may contain credentials or request headers.
            reason = "Ubisoft network error"
        if attempt == 2 or delay > 120:
            return "Unknown", f"{reason}; stop and try later"
        if report:
            report(f"{reason}. Retrying in {delay:g} seconds…")
        if stop.wait(delay):
            return "Cancelled", "Stopped"
    return "Unknown", "Ubisoft lookup failed"

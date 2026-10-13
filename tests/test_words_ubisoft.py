import io
import itertools
import json
import queue
import tempfile
import threading
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import core
from app import scan
from core import History, POTENTIAL, RequestGate, candidates, make_alphabet, send_discord
from ubisoft import APP_ID, lookup_ubisoft_name, parse_ubisoft_lookup, session_headers

TICKET = "TEST_ONLY_SESSION_TICKET"
SESSION = "11111111-2222-3333-4444-555555555555"
WEBHOOK = "https://discord.com/api/webhooks/123456/TEST_TOKEN"


class Response:
    status = 200
    def __init__(self, body=b'{"profiles":[]}'):
        self.body = body
    def read(self, size):
        return self.body
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class WordTests(unittest.TestCase):
    def test_dictionary_lengths_filters_and_unique_exhaustion(self):
        words = set(core.WORD_FILE.read_text().splitlines())
        self.assertTrue({"fox", "moon", "ghost"} <= words)
        for platform in ("minecraft", "discord", "ubisoft"):
            for length in (3, 4, 5):
                names = candidates(5000, make_alphabet(True, True, True), length=length, platform=platform, source="dictionary")
                expected = {w for w in words if len(w) == length and (platform != "ubisoft" or "ubi" not in w)}
                self.assertEqual(set(names), expected)
                self.assertEqual(len(names), len(expected))

    def test_dictionary_pattern_sample_and_paste_override(self):
        names = candidates(10, "abcdefghijklmnopqrstuvwxyz", "?o??", source="dictionary")
        self.assertEqual(len(names), 10)
        self.assertTrue(all(w[1] == "o" for w in names))
        self.assertEqual(candidates(10, "moon", "moon", source="dictionary"), ["moon"])
        self.assertEqual(candidates(10, "abc1", supplied="abc1", source="dictionary"), ["abc1"])
        for alphabet, pattern in [("123", ""), ("abc1", "1???")]:
            with self.assertRaises(ValueError):
                candidates(10, alphabet, pattern, source="dictionary")

    def test_missing_dictionary_reports_actionable_message(self):
        with tempfile.TemporaryDirectory() as folder, patch("core.WORD_FILE", Path(folder)/"missing.txt"):
            with self.assertRaisesRegex(ValueError, "Extract the whole ZIP"):
                candidates(10, "abc", source="dictionary")

    def test_ubisoft_generation_first_letter_and_symbols(self):
        for length in (3, 4, 5):
            names = candidates(5000, "a1.-_", length=length, platform="ubisoft")
            expected = {"a" + "".join(rest) for rest in itertools.product("a1.-_", repeat=length-1)}
            self.assertEqual(set(names), expected)
            self.assertEqual(len(names), len(expected))
        self.assertEqual(candidates(10, "abc.-", supplied="A.b a.b A-c", length=3, platform="ubisoft"), ["a.b", "a-c"])

    def test_ubisoft_rejects_invalid_patterns_and_reserved_names(self):
        for kwargs in ({"pattern": "1??"}, {"supplied": "1ab"}, {"pattern": "ubi"}, {"supplied": "UBI"}):
            with self.assertRaises(ValueError):
                candidates(10, "abui1", length=3, platform="ubisoft", **kwargs)
        with self.assertRaises(ValueError):
            candidates(10, "123_", length=3, platform="ubisoft")
        names = candidates(5000, "ubi", length=4, platform="ubisoft")
        self.assertTrue(all("ubi" not in n for n in names))


class UbisoftTests(unittest.TestCase):
    def test_exact_profiles_and_errors(self):
        profile = {"profileId": SESSION, "platformType": "uplay", "nameOnPlatform": "MOON"}
        self.assertEqual(parse_ubisoft_lookup("moon", 200, json.dumps({"profiles": [profile]}).encode())[0], "Taken")
        self.assertEqual(parse_ubisoft_lookup("moon", 200, b'{"profiles":[]}')[0], POTENTIAL)
        bad = [b'{}', b'[]', b'<html>verify</html>', b'{"profiles":null}', b'{"profiles":[null]}',
               b'{"profiles":[],"errorCode":3}', b'{"profiles":[],"captcha_key":[]}', b'{"profiles":{}}']
        for body in bad:
            self.assertEqual(parse_ubisoft_lookup("moon", 200, body)[0], "Unknown")
        for code in (204, 301, 400, 401, 403, 404, 429, 503):
            self.assertEqual(parse_ubisoft_lookup("moon", code, b'{"profiles":[]}')[0], "Unknown")
        for key, value in [("platformType", "psn"), ("nameOnPlatform", "other"), ("profileId", "bad")]:
            wrong = dict(profile, **{key: value})
            self.assertEqual(parse_ubisoft_lookup("moon", 200, json.dumps({"profiles": [wrong]}).encode())[0], "Unknown")

    def test_headers_and_request_only_read_official_profile(self):
        headers = session_headers("Ubi_v1 t=" + TICKET, SESSION)
        self.assertEqual(headers["Authorization"], "Ubi_v1 t=" + TICKET)
        self.assertEqual(headers["Ubi-AppId"], APP_ID)
        opener = Mock()
        opener.open.return_value = Response()
        self.assertEqual(lookup_ubisoft_name("A-b", threading.Event(), opener, ticket=TICKET, session_id=SESSION)[0], POTENTIAL)
        req = opener.open.call_args.args[0]
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(req.full_url, "https://public-ubiservices.ubi.com/v3/profiles?platformType=uplay&nameOnPlatform=a-b")
        self.assertIsNone(req.data)
        self.assertNotIn(TICKET, req.full_url)
        self.assertEqual(req.get_header("Ubi-sessionid"), SESSION)
        self.assertFalse(any(k.lower() == "cookie" for k, _ in req.header_items()))

    def test_reject_bad_auth_and_names_before_network(self):
        opener = Mock()
        for ticket in ("", "short", TICKET + "\r\nBad: yes"):
            with self.assertRaises(ValueError):
                lookup_ubisoft_name("moon", threading.Event(), opener, ticket=ticket)
        for name in ("1ab", "ubi", "rubik", "ab", "abcdef", "éab", "a/b"):
            with self.assertRaises(ValueError):
                lookup_ubisoft_name(name, threading.Event(), opener, ticket=TICKET)
        with self.assertRaises(ValueError):
            session_headers(TICKET, "not-a-uuid")
        opener.open.assert_not_called()

    def test_blocks_expiry_and_redirects_do_not_retry(self):
        for code in (301, 302, 400, 401, 403, 404):
            opener = Mock()
            opener.open.side_effect = urllib.error.HTTPError("url", code, TICKET, {}, io.BytesIO(b'{"profiles":[]}'))
            status, detail = lookup_ubisoft_name("moon", threading.Event(), opener, ticket=TICKET)
            self.assertEqual(status, "Unknown")
            self.assertNotIn(TICKET, detail)
            self.assertEqual(opener.open.call_count, 1)
        self.assertIsNone(core.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com"))

    def test_rate_limit_stops_immediately_and_preserves_long_delay(self):
        for headers, expected in [({}, 300), ({"Retry-After": "900"}, 900), ({"Retry-After": "1"}, 60)]:
            opener = Mock()
            opener.open.side_effect = urllib.error.HTTPError("url", 429, "limit", headers, io.BytesIO())
            gate = Mock()
            gate.wait.return_value = True
            self.assertEqual(lookup_ubisoft_name("moon", threading.Event(), opener, ticket=TICKET, gate=gate)[0], "Unknown")
            gate.cooldown.assert_called_once_with(expected)
            self.assertEqual(opener.open.call_count, 1)

    def test_cancellation_and_network_exception_do_not_leak_ticket(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError(TICKET)
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = False
        result = lookup_ubisoft_name("moon", stop, opener, ticket=TICKET)
        self.assertEqual(result[0], "Unknown")
        self.assertNotIn(TICKET, result[1])
        self.assertEqual(opener.open.call_count, 3)
        stop.is_set.return_value = True
        self.assertEqual(lookup_ubisoft_name("moon", stop, opener, ticket=TICKET)[0], "Cancelled")
        self.assertEqual(opener.open.call_count, 3)

    def test_ubisoft_webhook_has_correct_source_without_session(self):
        opener = Mock()
        opener.open.return_value = Response()
        self.assertTrue(send_discord(WEBHOOK, "moon", threading.Event(), opener, platform="ubisoft")[0])
        req = opener.open.call_args.args[0]
        payload = json.loads(req.data)
        self.assertIn("Ubisoft:", payload["embeds"][0]["title"])
        self.assertEqual(payload["embeds"][0]["url"], "https://account.ubisoft.com/")
        self.assertNotIn(TICKET, req.data.decode())
        self.assertFalse(any(k.lower() == "authorization" for k, _ in req.header_items()))

    def test_history_cache_age_platform_separation_and_cooldown(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"history.sqlite3"
            for platform in ("minecraft", "discord", "ubisoft"):
                history = History(path, platform)
                self.assertFalse(history.recently_taken("moon"))
                history.record("moon", "Taken")
                self.assertTrue(history.recently_taken("MOON"))
                self.assertEqual(history.notify(WEBHOOK, "moon", threading.Event(), Mock(return_value=(True,"Sent"))), "Sent")
                history.close()
            history = History(path, "ubisoft")
            old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            history.db.execute("UPDATE ubisoft_results SET checked=?", (old,))
            history.db.commit()
            self.assertFalse(history.recently_taken("moon"))
            history.record("moon", POTENTIAL)
            self.assertFalse(history.recently_taken("moon"))
            history.save_cooldown(900)
            history.save_cooldown(5)
            history.close()
            history = History(path, "ubisoft")
            self.assertGreater(history.cooldown_remaining(), 890)
            history.close()

    def test_scan_cache_preserves_timestamp_and_only_alerts_potential(self):
        with tempfile.TemporaryDirectory() as folder, patch("app.DATA", Path(folder)):
            history = History(Path(folder)/"history.sqlite3", "ubisoft")
            history.record("moon", "Taken")
            before = history.db.execute("SELECT checked FROM ubisoft_results").fetchone()[0]
            history.close()
            events = queue.Queue()
            with patch("app.lookup_ubisoft_name", return_value=(POTENTIAL, "")) as lookup, patch("core.send_discord", return_value=(True, "Sent")) as sender:
                scan(["moon", "star"], 0, WEBHOOK, threading.Event(), events, 8, "ubisoft", TICKET)
                lookup.assert_called_once()
                self.assertEqual(lookup.call_args.args[0], "star")
                self.assertGreaterEqual(lookup.call_args.kwargs["gate"].interval, 3)
                sender.assert_called_once()
                self.assertEqual(sender.call_args.kwargs["platform"], "ubisoft")
            self.assertIn(("counts", (2, 1, 2)), list(events.queue))
            history = History(Path(folder)/"history.sqlite3", "ubisoft")
            self.assertEqual(history.db.execute("SELECT checked FROM ubisoft_results WHERE name='moon'").fetchone()[0], before)
            history.close()

    def test_scan_rate_limit_persists_and_blocks_next_batch(self):
        def limited(name, stop, **kwargs):
            kwargs["gate"].cooldown(900)
            return "Unknown", "Rate limited"
        with tempfile.TemporaryDirectory() as folder, patch("app.DATA", Path(folder)), patch("app.lookup_ubisoft_name", side_effect=limited) as lookup:
            scan(["moon", "star"], 0, "", threading.Event(), queue.Queue(), 8, "ubisoft", TICKET)
            events = queue.Queue()
            scan(["moon"], 0, "", threading.Event(), events, 8, "ubisoft", TICKET)
            self.assertEqual(lookup.call_count, 1)
            self.assertTrue(any(kind == "error" and "cooling down" in value for kind, value in events.queue))


if __name__ == "__main__":
    unittest.main()

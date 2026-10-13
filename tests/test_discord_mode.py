import io
import itertools
import json
import queue
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from app import scan
from core import (POTENTIAL, History, candidates, lookup_discord_name,
                  parse_discord_lookup, send_discord, RequestGate)

URL = "https://discord.com/api/webhooks/123456/TEST_TOKEN"


class Response:
    status = 200
    def __init__(self, body=b'{"taken":false}'):
        self.body = body
    def read(self, size):
        return self.body
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class DiscordModeTests(unittest.TestCase):
    def test_valid_dot_generation_exhaustive_small_space(self):
        for length in (3, 4, 5):
            result = candidates(5000, "a._", length=length, platform="discord")
            expected = {"".join(n) for n in itertools.product("a._", repeat=length) if ".." not in "".join(n)}
            self.assertEqual(set(result), expected)
            self.assertEqual(len(result), len(expected))

    def test_dot_patterns_and_pasted_names(self):
        names = candidates(20, "a._", ".?.", length=3, platform="discord")
        self.assertEqual(set(names), {".a.", "._."})
        self.assertEqual(candidates(20, "a._", supplied="A._ a._", length=3, platform="discord"), ["a._"])
        for kwargs in ({"supplied":"a.."}, {"pattern":"a.."}):
            with self.assertRaises(ValueError):
                candidates(20, "a._", length=3, platform="discord", **kwargs)

    def test_no_valid_period_only_names(self):
        with self.assertRaises(ValueError):
            candidates(20, ".", length=3, platform="discord")
        with self.assertRaises(ValueError):
            candidates(20, "a.", length=3, platform="minecraft")

    def test_parser_only_exact_boolean_reports_available(self):
        self.assertEqual(parse_discord_lookup(200, b'{"taken":true}')[0], "Taken")
        self.assertEqual(parse_discord_lookup(200, b'{"taken":false}')[0], POTENTIAL)
        for body in (b'{}', b'{"taken":null}', b'{"taken":0}', b'{"taken":"false"}',
                     b'[]', b'<html>verify</html>', b'{"taken":false,"captcha_key":[]}'):
            self.assertEqual(parse_discord_lookup(200, body)[0], "Unknown")
        self.assertEqual(parse_discord_lookup(403, b'{"taken":false}')[0], "Unknown")

    def test_request_is_unauthenticated_and_only_checks(self):
        opener = Mock()
        opener.open.return_value = Response()
        self.assertEqual(lookup_discord_name("A.7", threading.Event(), opener)[0], POTENTIAL)
        request = opener.open.call_args.args[0]
        self.assertEqual(json.loads(request.data), {"username":"a.7"})
        self.assertTrue(request.full_url.endswith("/username-attempt-unauthed"))
        self.assertNotIn("Authorization", dict(request.header_items()))
        self.assertNotIn("Cookie", dict(request.header_items()))

    def test_invalid_name_never_sends_request(self):
        opener = Mock()
        for name in ("a..", "a-b", "éab", "ab", "abcdef"):
            with self.assertRaises(ValueError):
                lookup_discord_name(name, threading.Event(), opener)
        opener.open.assert_not_called()

    def test_verification_does_not_retry(self):
        for code, body in [(400, b'{"captcha_key":["captcha-required"]}'),
                           (403,b'{"message":"blocked"}'),
                           (429,b'{"captcha_sitekey":"test"}')]:
            opener = Mock()
            opener.open.side_effect = urllib.error.HTTPError("url", code, "error", {}, io.BytesIO(body))
            result = lookup_discord_name("a.7", threading.Event(), opener)
            self.assertEqual(result[0], "Unknown")
            self.assertIn("verification", result[1])
            self.assertEqual(opener.open.call_count, 1)

    def test_rate_limit_honors_json_and_header(self):
        opener = Mock()
        opener.open.side_effect = [urllib.error.HTTPError("url", 429, "limited", {"Retry-After":"12"}, io.BytesIO(b'{"retry_after":15}')), Response()]
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = False
        gate = Mock()
        gate.wait.return_value = True
        self.assertEqual(lookup_discord_name("a.7", stop, opener, gate=gate)[0], POTENTIAL)
        stop.wait.assert_called_once_with(15)
        gate.cooldown.assert_called_once_with(15)

    def test_long_rate_limit_stops(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError("url",429,"limited",{},io.BytesIO(b'{"retry_after":900}'))
        self.assertEqual(lookup_discord_name("a.7",threading.Event(),opener)[0],"Unknown")
        self.assertEqual(opener.open.call_count,1)

    def test_discord_alert_does_not_reference_minecraft(self):
        opener = Mock()
        opener.open.return_value = Response()
        self.assertTrue(send_discord(URL,"a.7",threading.Event(),opener,platform="discord")[0])
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertIn("Discord:",payload["embeds"][0]["title"])
        self.assertNotIn("Minecraft", json.dumps(payload))
        self.assertEqual(payload["allowed_mentions"], {"parse":[]})

    def test_histories_are_separate_and_legacy_mc_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"history.sqlite3"
            sender=Mock(return_value=(True,"Sent"))
            stop=threading.Event()
            for platform in ("minecraft","discord"):
                history=History(path,platform=platform)
                self.assertEqual(history.notify(URL,"abc",stop,sender),"Sent")
                history.record("abc","Taken" if platform=="minecraft" else POTENTIAL)
                history.close()
            history=History(path)
            self.assertEqual(history.notify(URL,"abc",stop,sender),"Already sent")
            self.assertEqual(history.db.execute("SELECT status FROM results WHERE name='abc'").fetchone()[0],"Taken")
            self.assertEqual(history.db.execute("SELECT status FROM discord_results WHERE name='abc'").fetchone()[0],POTENTIAL)
            history.close()
            self.assertEqual(sender.call_count,2)

    def test_scan_selects_discord_and_sends_only_potential(self):
        events=queue.Queue()
        with tempfile.TemporaryDirectory() as folder, patch("app.DATA",Path(folder)), patch("app.History") as cls, patch("app.lookup_discord_name",side_effect=[("Taken",""),(POTENTIAL,"")]) as lookup, patch("app.lookup_name") as mc:
            cls.return_value.notify.return_value="Sent"
            scan(["abc","a.7"],1,URL,threading.Event(),events,workers=8,platform="discord")
            cls.assert_called_once_with(Path(folder)/"history.sqlite3",platform="discord")
            cls.return_value.notify.assert_called_once()
            self.assertEqual(lookup.call_count,2)
            mc.assert_not_called()
        self.assertIn(("counts",(2,1,2)),list(events.queue))

    def test_cooldown_never_speeds_up_gentle_mode(self):
        gate=RequestGate(5)
        gate.cooldown(10)
        self.assertGreaterEqual(gate.interval,5)


if __name__ == "__main__":
    unittest.main()

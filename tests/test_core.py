import io
import json
import queue
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

from core import ALPHABETS, make_alphabet, History, POTENTIAL, RequestGate, candidates, parse_lookup, lookup_name, retry_delay, send_discord, webhook_url
from app import scan

URL = "https://discord.com/api/webhooks/123456/TEST_TOKEN"


class CandidateTests(unittest.TestCase):
    def test_all_small_space_without_duplicates(self):
        result = candidates(100, "ab", "?aa?")
        self.assertEqual(set(result), {"aaaa", "aaab", "baaa", "baab"})
        self.assertEqual(len(result), 4)

    def test_four_legal_characters(self):
        for alphabet in ALPHABETS.values():
            result = candidates(500, alphabet)
            self.assertEqual(len(set(result)), 500)
            self.assertTrue(all(len(x) == 4 and set(x) <= set(alphabet) for x in result))

    def test_pasted_case_insensitive_and_invalid(self):
        self.assertEqual(candidates(1, make_alphabet(True, True, True), supplied="AbCD,abcd; z9_0"), ["abcd", "z9_0"])
        for text in ("abc", "abcde", "éabc", "a-bc"):
            with self.assertRaises(ValueError):
                candidates(10, "ab", supplied=text)

    def test_fixed_pattern_and_bad_count(self):
        self.assertEqual(candidates(100, make_alphabet(True, True, True), "z9_0"), ["z9_0"])
        for n in (0, 5001):
            with self.assertRaises(ValueError):
                candidates(n, "ab")

    def test_all_lengths_and_type_combinations(self):
        for length in (3, 4, 5):
            for letters, numbers, underscores in [(True,False,False),(False,True,False),(False,False,True),(True,True,False),(True,False,True),(False,True,True),(True,True,True)]:
                alphabet = make_alphabet(letters, numbers, underscores)
                names = candidates(80, alphabet, length=length)
                self.assertEqual(len(names), min(80, len(alphabet)**length))
                self.assertEqual(len(set(names)), len(names))
                self.assertTrue(all(len(n)==length and set(n)<=set(alphabet) for n in names))

    def test_empty_selection_rejected(self):
        with self.assertRaises(ValueError):
            make_alphabet(False, False, False)
        with self.assertRaises(ValueError):
            candidates(10, "", length=3)

    def test_disallowed_pattern_or_list_rejected(self):
        for length in (3, 4, 5):
            for supplied in ("_"*length, "1"*length, "é"*length, "-"*length):
                with self.assertRaises(ValueError):
                    candidates(10, "abc", supplied=supplied, length=length)
            with self.assertRaises(ValueError):
                candidates(10, "abc", pattern="_"+"?"*(length-1), length=length)

    def test_blank_pattern_and_lengths(self):
        self.assertEqual(candidates(100, "_", length=3), ["___"])
        self.assertEqual(candidates(100, "_", length=5), ["_____"])
        for length in (0, 2, 6):
            with self.assertRaises(ValueError):
                candidates(10, "ab", length=length)
        with self.assertRaises(ValueError):
            candidates(10, "ab", pattern="????", length=3)

    def test_matching_lists_and_patterns(self):
        self.assertEqual(candidates(10, "abc", supplied="ABC abc", length=3), ["abc"])
        self.assertEqual(candidates(10, "abc", supplied="ABCAB abcab", length=5), ["abcab"])
        for length in (3, 4, 5):
            names = candidates(10, "abc", pattern="a"+"?"*(length-1), length=length)
            self.assertTrue(all(n.startswith("a") and len(n)==length for n in names))


def missing(name="bx_q"):
    return json.dumps({"path": f"/minecraft/profile/lookup/name/{name}",
                       "errorMessage": f"Couldn't find any profile with name {name}"}).encode()


class ParserTests(unittest.TestCase):
    def test_live_profile_fixture(self):
        self.assertEqual(parse_lookup("jeb_", 200, b'{"id":"853c80ef3c3749fdaa49938b674adae6","name":"jeb_"}')[0], "Taken")

    def test_live_missing_fixture(self):
        self.assertEqual(parse_lookup("bx_q", 404, missing())[0], POTENTIAL)

    def test_error_responses_are_never_potential(self):
        for code, body in [(404, b'<html>Not found</html>'), (404, b'{}'),
                           (200, missing()), (503, missing()), (403, b'blocked'),
                           (204, b''), (404, missing("zzzz")), (200, b'[]'),
                           (200, b'{"name":"abcd","id":"bad"}')]:
            self.assertEqual(parse_lookup("bx_q", code, body)[0], "Unknown")

    def test_mismatched_profile(self):
        self.assertEqual(parse_lookup("bx_q", 200, b'{"id":"853c80ef3c3749fdaa49938b674adae6","name":"jeb_"}')[0], "Unknown")


class LookupTests(unittest.TestCase):
    def test_missing_profile_http_error(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError("url", 404, "missing", {}, io.BytesIO(missing()))
        self.assertEqual(lookup_name("BX_Q", threading.Event(), opener)[0], POTENTIAL)

    def test_lookup_accepts_three_and_five(self):
        for name in ("q7_", "q7_z9"):
            opener = Mock()
            opener.open.side_effect = urllib.error.HTTPError("url", 404, "missing", {}, io.BytesIO(missing(name)))
            self.assertEqual(lookup_name(name, threading.Event(), opener)[0], POTENTIAL)

    def test_lookup_rejects_bad_names_without_request(self):
        for name in ("ab", "abcdef", "a-b", "a.b", "éab"):
            opener = Mock()
            with self.assertRaises(ValueError):
                lookup_name(name, threading.Event(), opener)
            opener.open.assert_not_called()

    def test_rate_limit_wait_then_success(self):
        opener = Mock()
        response = FakeResponse()
        response.read = lambda limit: b'{"id":"853c80ef3c3749fdaa49938b674adae6","name":"jeb_"}'
        opener.open.side_effect = [urllib.error.HTTPError("url", 429, "limit", {"Retry-After":"8"}, io.BytesIO()), response]
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = False
        self.assertEqual(lookup_name("jeb_", stop, opener)[0], "Taken")
        stop.wait.assert_called_once_with(8)

    def test_rate_limit_over_two_minutes_stops(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError("url", 429, "limit", {"Retry-After":"300"}, io.BytesIO())
        stop = Mock()
        stop.is_set.return_value = False
        self.assertEqual(lookup_name("bx_q", stop, opener)[0], "Unknown")
        stop.wait.assert_not_called()

    def test_network_exhaustion_is_unknown(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError("offline")
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = False
        self.assertEqual(lookup_name("bx_q", stop, opener)[0], "Unknown")
        self.assertEqual(opener.open.call_count, 3)

    def test_cancel_during_backoff(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError("offline")
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = True
        self.assertEqual(lookup_name("bx_q", stop, opener)[0], "Cancelled")
        self.assertEqual(opener.open.call_count, 1)

    def test_retry_after_invalid_and_date(self):
        self.assertEqual(retry_delay({"Retry-After":"nan"}, 5), 5)
        self.assertEqual(retry_delay({"Retry-After":"invalid"}, 5), 5)
        self.assertEqual(retry_delay({"Retry-After":"Wed, 01 Jan 2020 00:00:00 GMT"}, 5), 3)


class FakeResponse:
    status = 200
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class DiscordTests(unittest.TestCase):
    def test_url_validation_and_wait(self):
        self.assertTrue(webhook_url(URL).endswith("?wait=true"))
        self.assertIn("thread_id=456", webhook_url(URL + "?thread_id=456"))
        for url in ("http://discord.com/api/webhooks/1/x", "https://discord.com.evil.test/api/webhooks/1/x",
                    "https://discord.com@evil.test/api/webhooks/1/x", URL + "#fragment"):
            with self.assertRaises(ValueError):
                webhook_url(url)

    def test_success_payload(self):
        opener = Mock()
        opener.open.return_value = FakeResponse()
        self.assertEqual(send_discord(URL, "q7_x", threading.Event(), opener), (True, "Sent"))
        request = opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertIn("q7_x", payload["embeds"][0]["title"])
        self.assertIn("potentially available", payload["embeds"][0]["title"])
        self.assertNotIn("namemc", json.dumps(payload).lower())
        self.assertIn("wait=true", request.full_url)

    def test_rate_limit_retry(self):
        error = urllib.error.HTTPError(URL, 429, "limited", {}, io.BytesIO(b'{"retry_after": 2.5}'))
        opener = Mock()
        opener.open.side_effect = [error, FakeResponse()]
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = False
        self.assertEqual(send_discord(URL, "q7_x", stop, opener), (True, "Sent"))
        stop.wait.assert_called_once_with(2.5)

    def test_no_secret_in_error(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError(URL)
        ok, message = send_discord(URL, "q7_x", threading.Event(), opener)
        self.assertFalse(ok)
        self.assertNotIn("TEST_TOKEN", message)

    def test_cancel_does_not_send(self):
        opener = Mock()
        stop = threading.Event()
        stop.set()
        self.assertFalse(send_discord(URL, "q7_x", stop, opener)[0])
        opener.open.assert_not_called()

    def test_success_dedup_failure_retry_and_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            history = History(path)
            sender = Mock(side_effect=[(False, "Failed"), (True, "Sent"), (True, "Sent")])
            stop = threading.Event()
            self.assertEqual(history.notify(URL, "ABCD", stop, sender), "Failed")
            self.assertEqual(history.notify(URL, "abcd", stop, sender), "Sent")
            history.close()
            history = History(path)
            self.assertEqual(history.notify(URL, "abcd", stop, sender), "Already sent")
            self.assertEqual(history.notify(URL + "?thread_id=999", "abcd", stop, sender), "Sent")
            self.assertEqual(sender.call_count, 3)
            history.close()


class ScanTests(unittest.TestCase):
    def test_bounded_parallel_checks_preserve_all_results(self):
        barrier = threading.Barrier(4, timeout=3)
        lock = threading.Lock()
        active = peak = 0
        names = [f"a{i:03d}" for i in range(12)]

        def lookup(name, stop, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(active, peak)
            barrier.wait()
            with lock:
                active -= 1
            return "Taken", ""

        events = queue.Queue()
        with tempfile.TemporaryDirectory() as directory, patch("app.DATA", Path(directory)), patch("app.lookup_name", side_effect=lookup):
            scan(names, 0, "", threading.Event(), events, workers=4)
        results = [row for kind, row in list(events.queue) if kind == "result"]
        self.assertEqual(peak, 4)
        self.assertCountEqual([row[0] for row in results], names)
        self.assertFalse(any(kind == "error" for kind, _ in list(events.queue)))

    def test_stop_does_not_submit_entire_batch(self):
        stop = threading.Event()
        started = []

        def lookup(name, flag, **kwargs):
            started.append(name)
            flag.set()
            return "Cancelled", "Stopped"

        events = queue.Queue()
        with tempfile.TemporaryDirectory() as directory, patch("app.DATA", Path(directory)), patch("app.lookup_name", side_effect=lookup):
            scan([f"a{i:03d}" for i in range(100)], 0, "", stop, events, workers=4)
        self.assertLessEqual(len(started), 4)
        self.assertFalse(any(kind == "result" for kind, _ in list(events.queue)))

    def test_only_potential_sends_and_result_counts(self):
        history = Mock()
        history.notify.return_value = "Sent"
        events = queue.Queue()
        with tempfile.TemporaryDirectory() as directory, patch("app.DATA", Path(directory)), patch("app.History", return_value=history), patch("app.lookup_name", side_effect=[("Taken", ""), (POTENTIAL, "")]):
            scan(["jeb_", "bx_q"], 0, URL, threading.Event(), events)
        self.assertEqual(history.notify.call_count, 1)
        self.assertEqual(history.notify.call_args.args[1], "bx_q")
        self.assertIn(("counts", (2, 1, 2)), list(events.queue))
        history.close.assert_called_once()

    def test_unknown_stops_without_alert(self):
        history = Mock()
        events = queue.Queue()
        with tempfile.TemporaryDirectory() as directory, patch("app.DATA", Path(directory)), patch("app.History", return_value=history), patch("app.lookup_name", return_value=("Unknown", "Rate limited")) as lookup:
            scan(["abcd", "bx_q"], 0, URL, threading.Event(), events)
        history.notify.assert_not_called()
        self.assertEqual(lookup.call_count, 1)
        self.assertTrue(any(kind == "error" for kind, _ in list(events.queue)))


class GateTests(unittest.TestCase):
    def test_shared_spacing_and_cooldown(self):
        clock = [10.0]
        stop = Mock()
        stop.is_set.return_value = False
        def wait_for(seconds):
            clock[0] += max(seconds, 0.000001)
            return False
        stop.wait.side_effect = wait_for
        gate = RequestGate(0.2)
        with patch("core.time.monotonic", side_effect=lambda: clock[0]):
            self.assertTrue(gate.wait(stop))
            self.assertTrue(gate.wait(stop))
            self.assertGreaterEqual(clock[0], 10.2)
            gate.cooldown(8)
            self.assertTrue(gate.wait(stop))
            self.assertGreaterEqual(clock[0], 18.2)
            self.assertEqual(gate.interval, 0.5)

    def test_cooldown_cancel_is_immediate(self):
        gate = RequestGate(0.1)
        gate.cooldown(120)
        stop = threading.Event()
        stop.set()
        self.assertFalse(gate.wait(stop))

    def test_rate_limit_notifies_shared_gate(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError("url", 429, "limit", {"Retry-After":"300"}, io.BytesIO())
        gate = Mock()
        gate.wait.return_value = True
        self.assertEqual(lookup_name("bx_q", threading.Event(), opener, gate=gate)[0], "Unknown")
        gate.cooldown.assert_called_once_with(300)


if __name__ == "__main__":
    unittest.main()

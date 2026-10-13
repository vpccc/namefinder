import http.client
import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from desktop import Controller, Server, candidate_options
from core import POTENTIAL


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)/"preferences.json"

    def test_theme_persistence_whitelists_only_appearance(self):
        control = Controller(Mock(), self.path)
        control.save_preferences({"theme": "rose", "motion": False})
        self.assertEqual(Controller(Mock(), self.path).preferences, {"theme": "rose", "motion": False})
        for bad in ({"theme":"invalid","motion":True}, {"theme":"iris","motion":"false"},
                    {"theme":"iris","motion":True,"ticket":"secret"}):
            with self.assertRaises(ValueError):
                control.save_preferences(bad)
        self.assertNotIn("secret", self.path.read_text())

    def test_invalid_preferences_recover(self):
        self.path.write_text("not json")
        self.assertEqual(Controller(Mock(), self.path).preferences["theme"], "iris")

    def test_preview_is_offline_and_uses_dictionary(self):
        scanner = Mock()
        control = Controller(scanner, self.path)
        result = control.preview({"source":"dictionary","length":4,"count":20,"pattern":"?o??"})
        self.assertEqual(result["count"], 20)
        self.assertEqual(len(result["names"]), 6)
        self.assertTrue(all(len(n)==4 and n[1]=="o" and n.isalpha() for n in result["names"]))
        scanner.assert_not_called()

    def test_credentials_used_only_for_matching_scan_not_state_or_preferences(self):
        scanner = Mock()
        control = Controller(scanner, self.path)
        control.start({"platform":"ubisoft","ticket":"TEST_ONLY_SESSION_TICKET","session_id":"","count":1})
        control.worker.join(2)
        args = scanner.call_args.args
        self.assertEqual(args[5:8], (1, "ubisoft", "TEST_ONLY_SESSION_TICKET"))
        self.assertNotIn("TEST_ONLY", json.dumps(control.snapshot()))
        self.assertFalse(self.path.exists())
        control.start({"platform":"discord","ticket":"TEST_ONLY_SESSION_TICKET","count":1})
        control.worker.join(2)
        self.assertEqual(scanner.call_args.args[7:9], ("", ""))

    def test_scan_events_cursor_stop_and_duplicate_start(self):
        entered = threading.Event()
        def scanner(names, delay, webhook, stop, events, *args):
            events.put(("result", (names[0], POTENTIAL, "Off")))
            events.put(("counts", (1,1,len(names))))
            entered.set()
            stop.wait(2)
            events.put(("done","Stopped"))
        control = Controller(scanner, self.path)
        control.start({"count":2})
        self.assertTrue(entered.wait(2))
        first = control.snapshot()
        self.assertEqual(first["checked"], 1)
        self.assertEqual(len(first["rows"]), 1)
        self.assertEqual(control.snapshot(1, first["run"])["rows"], [])
        self.assertEqual(len(control.snapshot(1, first["run"]-1)["rows"]),1)
        with self.assertRaisesRegex(ValueError,"already running"):
            control.start({})
        control.stop.set()
        control.worker.join(2)
        self.assertFalse(control.snapshot()["running"])

    def test_invalid_settings_never_start_and_period_rules_remain(self):
        control = Controller(Mock(), self.path)
        for payload in ({"platform":"unknown"},{"length":6},{"count":5001},
                        {"letters":"yes"},{"speed":"unlimited"},{"platform":"ubisoft"},
                        {"webhook":"https://example.com/token"}):
            with self.assertRaises(ValueError):
                control.start(payload)
        control.scanner.assert_not_called()
        self.assertNotIn(".",candidate_options({"platform":"minecraft","periods":True})["alphabet"])
        self.assertIn("-",candidate_options({"platform":"ubisoft","dashes":True})["alphabet"])

    def test_environment_webhook_requires_explicit_use_and_can_be_cleared(self):
        url = "https://discord.com/api/webhooks/123456/TEST_ONLY_TOKEN"
        with patch.dict("os.environ", {"DISCORD_WEBHOOK_URL":url}):
            scanner=Mock()
            control=Controller(scanner,self.path)
            self.assertTrue(control.snapshot()["has_env_webhook"])
            self.assertNotIn(url,json.dumps(control.snapshot()))
            control.start({"count":1,"use_env_webhook":True})
            control.worker.join(2)
            self.assertIn("TEST_ONLY_TOKEN",scanner.call_args.args[2])
            control.start({"count":1,"use_env_webhook":False})
            control.worker.join(2)
            self.assertEqual(scanner.call_args.args[2],"")


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory()
        cls.control=Controller(Mock(),Path(cls.folder.name)/"prefs.json")
        cls.server=Server(cls.control)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(2); cls.folder.cleanup()

    def request(self,path,body=None,headers=None,authorized=True):
        connection=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=3)
        values={"Content-Type":"application/json"}
        if authorized: values["X-Name-Finder"]=self.server.token
        values.update(headers or {})
        connection.request("POST" if body is not None else "GET",path,json.dumps(body) if body is not None else None,values)
        response=connection.getresponse()
        result=response.status,dict(response.getheaders()),response.read()
        connection.close()
        return result

    def test_assets_csp_and_path_restrictions(self):
        for asset in ("/","/style.css","/ui.js","/icon.svg"):
            code,headers,body=self.request(asset,authorized=False)
            self.assertEqual(code,200)
            self.assertTrue(body)
            self.assertEqual(headers["Cache-Control"],"no-store")
            self.assertIn("frame-ancestors 'none'",headers["Content-Security-Policy"])
        for path in ("/../data/preferences.json","/data/history.sqlite3","/core.py"):
            self.assertEqual(self.request(path)[0],404)

    def test_api_requires_session_key_and_rejects_external_origins_and_hosts(self):
        self.assertEqual(self.request("/api/state",authorized=False)[0],403)
        self.assertEqual(self.request("/api/state",headers={"X-Name-Finder":"wrong"})[0],403)
        self.assertEqual(self.request("/api/state",headers={"Origin":"https://example.com"})[0],403)
        self.assertEqual(self.request("/api/state",headers={"Host":"evil.example"})[0],403)
        self.assertEqual(self.request("/api/state")[0],200)

    def test_preview_validation_and_preferences_endpoints(self):
        code,_,body=self.request("/api/preview",{"source":"dictionary","count":4,"length":3})
        self.assertEqual(code,200)
        self.assertEqual(len(json.loads(body)["names"]),4)
        self.assertEqual(self.request("/api/start",{"count":0})[0],400)
        self.assertEqual(self.request("/api/preferences",{"theme":"ember","motion":False})[0],200)
        self.assertEqual(json.loads(self.request("/api/state")[2])["preferences"]["theme"],"ember")
        self.assertEqual(self.request("/api/preferences",{"theme":"ember","motion":False,"ticket":"SECRET"})[0],400)
        self.assertNotIn("SECRET",self.control.preferences_path.read_text())

    def test_csv_filter_correct_platform_and_single_use_download(self):
        self.control.platform="ubisoft"
        self.control.rows=[("moon",POTENTIAL,"Off"),("star","Taken","Cached")]
        code,_,body=self.request("/api/export",{"potential_only":True})
        self.assertEqual(code,200)
        target=json.loads(body)["url"]
        code,headers,body=self.request(target,authorized=False)
        self.assertEqual(code,200)
        self.assertIn('attachment; filename="ubisoft_names.csv"',headers["Content-Disposition"])
        self.assertIn("ubisoft,moon",body.decode("utf-8-sig"))
        self.assertIn("https://account.ubisoft.com/",body.decode("utf-8-sig"))
        self.assertNotIn("star",body.decode("utf-8-sig"))
        self.assertEqual(self.request(target,authorized=False)[0],404)
        self.assertEqual(self.request("/api/export",{},authorized=False)[0],403)


if __name__ == "__main__":
    unittest.main()

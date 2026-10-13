"""Security regressions use only localhost and dummy credentials."""
import http.client
import io
import json
import socket
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

import build_release
from core import send_discord
from desktop import Controller, Server


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.scanner = Mock()
        self.control = Controller(self.scanner, Path(self.folder.name) / 'prefs.json')
        self.server = Server(self.control)
        self.server.connection_timeout = 0.3
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def post(self, path, payload, token=None, origin=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            headers = {'Content-Type': 'application/json'}
            if token is not None:
                headers['X-Name-Finder'] = token
            if origin:
                headers['Origin'] = origin
            connection.request('POST', path, json.dumps(payload), headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_all_mutations_reject_missing_key_and_foreign_origins(self):
        for path in ('start', 'preview', 'stop', 'preferences', 'export', 'quit'):
            with self.subTest(path=path):
                self.assertEqual(self.post('/api/' + path, {})[0], 403)
                self.assertEqual(self.post('/api/' + path, {}, self.server.token,
                                           'https://attacker.example')[0], 403)
        self.scanner.assert_not_called()
        self.assertFalse(self.control.stop.is_set())

    def test_non_ascii_key_is_rejected_without_crashing(self):
        self.assertEqual(self.post('/api/start', {}, '\xe9')[0], 403)
        self.scanner.assert_not_called()

    def test_large_request_and_credentials_in_invalid_payload_are_not_echoed(self):
        secret = 'TEST_ONLY_PRIVATE_VALUE'
        code, body = self.post('/api/start', {'ticket': secret * 4000}, self.server.token)
        self.assertEqual(code, 413)
        self.assertNotIn(secret.encode(), body)
        # A newline inside an otherwise valid-looking ticket must never become a header.
        code, body = self.post('/api/start', {'platform': 'ubisoft', 'ticket': secret + '\r\nInjected: yes'}, self.server.token)
        self.assertEqual(code, 400)
        self.assertNotIn(secret.encode(), body)
        self.scanner.assert_not_called()

    def test_incomplete_local_request_times_out(self):
        with socket.create_connection(self.server.server_address, timeout=2) as connection:
            connection.sendall(b'GET / HTTP/1.1\r\nHost: ')
            self.assertEqual(connection.recv(1), b'')

    def test_excess_connections_are_rejected_and_capacity_recovers(self):
        for _ in range(self.server.max_active_requests):
            self.assertTrue(self.server.request_slots.acquire(blocking=False))
        try:
            with socket.create_connection(self.server.server_address, timeout=2) as connection:
                self.assertEqual(connection.recv(1), b'')
        finally:
            for _ in range(self.server.max_active_requests):
                self.server.request_slots.release()
        self.assertEqual(self.post('/api/preview', {'count': 1}, self.server.token)[0], 200)


class ReleaseSecurityTests(unittest.TestCase):
    def test_webhook_error_body_read_is_bounded(self):
        class RecordingBody(io.BytesIO):
            def read(self, size=-1):
                self.requested_size = size
                return super().read(size)
        body = RecordingBody(b'{"retry_after":999999}' + b' ' * 100000)
        error = urllib.error.HTTPError('https://discord.com/', 429, 'limited', {}, body)
        opener = Mock()
        opener.open.side_effect = error
        ok, message = send_discord('https://discord.com/api/webhooks/123/TEST_ONLY_TOKEN',
                                  'moon', threading.Event(), opener)
        self.assertFalse(ok)
        self.assertEqual(body.requested_size, 65536)

    def test_release_excludes_runtime_secrets_and_paths(self):
        source = build_release.BASE
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in build_release.FILES:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((source / relative).read_bytes())
            (root / 'data').mkdir()
            (root / 'data' / 'history.sqlite3').write_text('TEST_ONLY_PRIVATE_HISTORY')
            (root / '.env').write_text('TEST_ONLY_SECRET_ENV')
            with patch.object(build_release, 'BASE', root):
                with ZipFile(build_release.build()) as archive:
                    self.assertEqual(set(archive.namelist()), {'Namefinder/' + f for f in build_release.FILES})
                    self.assertIsNone(archive.testzip())
                    self.assertFalse(archive.comment)
                    for info in archive.infolist():
                        self.assertFalse(info.comment)
                        self.assertNotIn(str(root).encode(), archive.read(info))


if __name__ == '__main__':
    unittest.main()

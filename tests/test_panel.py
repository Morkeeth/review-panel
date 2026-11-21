"""Exercise the actual server, storage and publisher on isolated real demo files."""
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from panel_card_publish import publish
from http.server import ThreadingHTTPServer
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError


class PanelTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        server.ROOT = Path(self.temp.name).resolve()
        server.MEDIA_ROOTS = [server.ROOT / 'media']
        server.TOKEN = 'test-access-secret-of-sufficient-length'
        server.setup()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.http.server_port)

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.temp.cleanup()

    def request(self, path, body=None, auth=True):
        headers = {'Authorization': 'Bearer ' + server.TOKEN} if auth else {}
        data = None
        if body is not None:
            headers['Content-Type'] = 'application/json'; data = json.dumps(body).encode()
        try:
            with urlopen(Request(self.base + path, data=data, headers=headers)) as r:
                return r.status, json.loads(r.read())
        except HTTPError as e:
            return e.code, json.loads(e.read())

    def card(self):
        return self.request('/review.json')[1]['items'][0]

    def answer(self, card, **extra):
        return dict(id=card['id'], verdict=card['options'][0], note='Keep this welcome.', shown_sha256=card['card_sha256'], **extra)

    def test_broken_card_is_skipped_not_fatal(self):
        (server.ROOT / 'queue/broken-card.json').write_text('{"id": "broken-card"}')
        status, body = self.request('/review.json')
        self.assertEqual(status, 200)
        self.assertEqual(len(body['items']), 2)
        self.assertEqual([s['id'] for s in body['skipped']], ['broken-card'])

    def test_auth_and_media_boundary(self):
        for path in ['/review.json', '/history.json', '/media?p=media/demo-a.png', '/audio?id=unknown']:
            self.assertEqual(self.request(path, auth=False)[0], 401)
        with self.assertRaises(ValueError):
            server.media_path('../server.py')
        with self.assertRaises(ValueError):
            server.media_path('/etc/passwd')
        (server.ROOT / 'media/escape.png').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            server.media_path('media/escape.png')

    def test_login_cookie_and_cross_origin(self):
        from http.cookiejar import CookieJar
        from urllib.request import build_opener, HTTPCookieProcessor
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        req = Request(self.base + '/login', data=json.dumps({'token': server.TOKEN}).encode(), headers={'Content-Type': 'application/json'})
        with opener.open(req) as response:
            self.assertIn('HttpOnly', response.headers['Set-Cookie'])
            self.assertIn('SameSite=Strict', response.headers['Set-Cookie'])
        with opener.open(self.base + '/review.json') as response:
            self.assertTrue(json.loads(response.read())['items'])
        card = self.card()
        req = Request(self.base + '/verdict', data=json.dumps(self.answer(card)).encode(), headers={'Content-Type': 'application/json', 'Origin': 'https://other.example', 'Authorization': 'Bearer ' + server.TOKEN})
        with self.assertRaises(HTTPError) as result:
            urlopen(req)
        self.assertEqual(result.exception.code, 403)

    def test_append_retry_snapshot_and_conflict(self):
        card = self.card(); body = self.answer(card)
        body['verdict'] = 'NOTE'
        self.assertEqual(self.request('/verdict', body)[0], 200)
        prefix = (server.ROOT / 'verdicts.jsonl').read_bytes()
        self.assertEqual(self.card()['saved_note'], body['note'])
        body['verdict'] = card['options'][0]
        status, result = self.request('/verdict', body)
        self.assertEqual(status, 200)
        path = server.ROOT / result['row']['snapshot']
        self.assertTrue((path / 'card.json').is_file())
        self.assertEqual((path / '0.mp4').read_bytes(), server.media_path(card['media'][0]).read_bytes())
        saved = (server.ROOT / 'verdicts.jsonl').read_bytes()
        self.assertTrue(saved.startswith(prefix))
        self.assertEqual(self.request('/verdict', body)[0], 200)
        self.assertEqual((server.ROOT / 'verdicts.jsonl').read_bytes(), saved)
        body['note'] = 'Conflicting retry'
        self.assertEqual(self.request('/verdict', body)[0], 409)

    def test_changed_media_refuses_answer(self):
        card = self.card()
        p = server.media_path(card['media'][0]); p.write_bytes(p.read_bytes() + b'changed')
        self.assertEqual(self.request('/verdict', self.answer(card))[0], 409)
        self.assertEqual(server.history(), [])

    def test_changed_card_refuses_answer(self):
        card = self.card(); p = server.ROOT / 'queue' / (card['id'] + '.json')
        value = json.loads(p.read_text()); value['title'] = 'Updated welcome'; p.write_text(json.dumps(value))
        self.assertEqual(self.request('/verdict', self.answer(card))[0], 409)

    def test_publisher_preserves_identities(self):
        card = server.source(self.card()['id']); p = server.ROOT / 'proposal.json'
        p.write_text(json.dumps(card))
        with self.assertRaises(ValueError): publish(p, True)
        card['id'] = 'fresh-welcome'; p.write_text(json.dumps(card))
        with self.assertRaises(ValueError): publish(p, True)
        card['what'] = 'New visitors now follow the arrows to the seed exchange without asking a volunteer.'
        p.write_text(json.dumps(card)); self.assertEqual(publish(p, True)['status'], 'GREEN')
        with self.assertRaises(ValueError): publish(p, True)

    def test_audio_original_and_retry(self):
        card = self.card(); cid = str(uuid.uuid4())
        # A real bundled clip provides reproducible binary input for durable storage.
        raw = server.media_path(card['media'][1]).read_bytes()[:1024]
        body = dict(card=card['id'], shown_sha256=card['card_sha256'], chunk_id=cid,
                    audio_base64=base64.b64encode(raw).decode(), audio_type='audio/webm',
                    segment_started_at='2026-11-15T10:00:00+00:00', segment_ended_at='2026-11-15T10:00:01+00:00')
        status, result = self.request('/voice', body)
        self.assertEqual(status, 200, result); self.assertTrue(result['audio_durable']); self.assertTrue(result['durable'])
        path = server.ROOT / 'voice.jsonl'; before = path.read_bytes()
        self.assertEqual(self.request('/voice', body)[0], 200)
        self.assertEqual(path.read_bytes(), before)
        row = json.loads(before)
        self.assertEqual(Path(row['audio']).read_bytes(), raw)
        body['audio_base64'] = base64.b64encode(b'different original').decode()
        self.assertEqual(self.request('/voice', body)[0], 409)


if __name__ == '__main__': unittest.main()

#!/usr/bin/env python3
"""Local review server. Python 3.10+, standard library only."""
import argparse
import fcntl
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import panel_audio
import panel_verdict_log
from card_gate import assert_card

APP = Path(__file__).resolve().parent
ROOT = Path(os.environ.get('PANEL_ROOT', APP / 'data')).expanduser().resolve()
MEDIA_ROOTS = [Path(p).expanduser().resolve() for p in os.environ.get('MEDIA_ROOTS', str(ROOT / 'media')).split(os.pathsep)]
TOKEN = os.environ.get('PANEL_TOKEN', '')
LOCK = threading.RLock()
EXT = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.mp4', '.mov', '.m4v', '.webm'}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def now():
    return datetime.now(timezone.utc).isoformat()


def media_path(value):
    if not isinstance(value, str):
        raise ValueError('Media must be a path string.')
    p = Path(value)
    if not p.is_absolute():
        p = ROOT / p
    p = p.resolve()
    if not any(p.is_relative_to(root) for root in MEDIA_ROOTS) or p.suffix.lower() not in EXT or not p.is_file():
        raise ValueError('Media must be a supported file inside MEDIA_ROOTS.')
    return p


def validate(card):
    assert_card(card)
    for m in card['media']:
        media_path(m)
    return card


def history():
    path = ROOT / 'verdicts.jsonl'
    if not path.exists():
        return []
    # Fail closed: damaged history must never reopen an answered identity.
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if any(not isinstance(row, dict) or not row.get('id') for row in rows):
        raise ValueError('Invalid verdict history; repair from a backup before continuing.')
    return rows


def source(card_id):
    if not re.fullmatch(r'[A-Za-z0-9-]{1,120}', str(card_id)):
        raise ValueError('Invalid card id.')
    p = ROOT / 'queue' / (card_id + '.json')
    if p.is_symlink() or not p.is_file() or p.stat().st_size > 250000:
        raise ValueError('Card unavailable.')
    card = validate(json.loads(p.read_text()))
    if card['id'] != card_id:
        raise ValueError('Card id does not match filename.')
    return card


def shown(card):
    identities = {m: hashlib.sha256(media_path(m).read_bytes()).hexdigest() for m in card['media']}
    digest = hashlib.sha256(encoded([card, identities])).hexdigest()
    return dict(card, card_sha256=digest), identities


def cards(skipped=None):
    # One broken card file must not take the whole queue down; it is reported and left out.
    settled = {r['id'] for r in history() if r.get('verdict') != 'NOTE'}
    result = []
    for p in sorted((ROOT / 'queue').glob('*.json')):
        if p.stem in settled:
            continue
        try:
            card = shown(source(p.stem))[0]
        except (ValueError, OSError, KeyError, TypeError) as e:
            if skipped is not None:
                skipped.append({'id': p.stem, 'error': str(e)})
            continue
        notes = [r for r in history() if r['id'] == p.stem and r.get('verdict') == 'NOTE' and r.get('card_sha256') == card['card_sha256']]
        if notes:
            card['saved_note'] = notes[-1]['note']
        result.append(card)
    return result


def snapshot(card, identities):
    folder = ROOT / 'snapshots' / card['card_sha256']
    folder.mkdir(parents=True, exist_ok=True)
    for i, m in enumerate(card['media']):
        target = folder / (str(i) + media_path(m).suffix.lower())
        raw = media_path(m).read_bytes()
        if hashlib.sha256(raw).hexdigest() != identities[m]:
            raise ValueError('Media changed while saving. Reload and review again.')
        immutable(target, raw)
    immutable(folder / 'card.json', encoded(card))
    immutable(folder / 'media.json', encoded(identities))
    return str(folder.relative_to(ROOT))


def immutable(path, raw):
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('Retained version differs; save refused.')
        return
    with path.open('xb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, value, mime='application/json', extra=None):
        raw = encoded(value) if mime == 'application/json' else value
        self.send_response(status)
        for key, value in {'Content-Type': mime, 'Content-Length': str(len(raw)), 'Cache-Control': 'no-store',
                           'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                           'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'", **(extra or {})}.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def authenticated(self):
        if not TOKEN:
            return True
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
        except Exception:
            return False
        supplied = cookie['panel'].value if 'panel' in cookie else self.headers.get('Authorization', '').removeprefix('Bearer ')
        expected = hmac.new(TOKEN.encode(), b'review-panel-session', hashlib.sha256).hexdigest()
        return hmac.compare_digest(supplied, expected) or hmac.compare_digest(supplied, TOKEN)

    def body(self):
        n = int(self.headers.get('Content-Length', '0'))
        if not 0 < n <= 6000000 or self.headers.get_content_type() != 'application/json':
            raise ValueError('Expected a JSON object within the request limit.')
        value = json.loads(self.rfile.read(n))
        if not isinstance(value, dict):
            raise ValueError('Expected an object.')
        return value

    def do_GET(self):
        u = urlsplit(self.path)
        path = unquote(u.path)
        try:
            public = path in {'/login', '/manifest.webmanifest', '/sw.js', '/apple-touch-icon.png', '/favicon.ico'} or path.startswith('/pwa/')
            if not public and not self.authenticated():
                if path in {'/', '/next', '/history'}:
                    return self.send(303, b'', 'text/plain', {'Location': '/login'})
                return self.send(401, {'error': 'Enter the access token.'})
            if path == '/login':
                return self.send(200, b'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><title>Review login</title><style>body{margin:0;color:#000;background:#fff;font:16px/1.5 system-ui,sans-serif}header{background:#0047ff;padding:calc(12px + env(safe-area-inset-top)) 16px 12px;color:#fff;font-weight:700}main{margin:20px 16px}label{display:block}input[type=password]{display:block;width:100%;box-sizing:border-box;margin-top:8px;font-size:16px;min-height:48px;border:1.5px solid #000;padding:8px 12px}form button{display:block;width:100%;margin-top:16px;min-height:48px;border:0;background:#0047ff;color:#fff;font-size:16px;font-weight:500;cursor:pointer}p[role=status]{margin:12px 0 0}</style><body><header>Review</header><main><form><label>Access token <input type="password" autocomplete="current-password" required autocapitalize="off" autocorrect="off" spellcheck="false"></label><button>Unlock</button></form><p role="status"></p></main><script>document.querySelector('form').onsubmit=async e=>{e.preventDefault();const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:document.querySelector('input').value})});if(r.ok)location.href='/';else{let m='Token refused.';try{const j=await r.json();if(j.error)m=j.error;}catch(_){}document.querySelector('p').textContent=m;}};</script></body></html>''', 'text/html')
            if path in {'/review.json', '/review-list.json'}:
                skipped = []
                return self.send(200, {'items': cards(skipped), 'skipped': skipped, 'scanned_at': time.time()})
            if path == '/history.json':
                return self.send(200, {'verdicts': history(), 'voice': panel_verdict_log.read_rows(ROOT / 'voice.jsonl')})
            if path == '/history':
                return self.send(200, b'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Review history</title><style>body{margin:0;color:#000;background:#fff;font:16px/1.5 system-ui,sans-serif}header{background:#0047ff;padding:12px 16px;color:white;font-weight:700}header a{color:white;display:inline-flex;align-items:center;min-height:44px}main,h1{margin:20px 16px}article{border-top:2px solid #000;padding:16px 0}h2{font-size:18px;margin:0 0 8px}p{margin:8px 0;overflow-wrap:anywhere}time{font-size:13px;color:#575757}audio{max-width:100%;display:block;margin:12px 0}a{color:#0047ff}</style><body><header><a href="/">Back to review</a></header><h1>Saved answers</h1><main></main><script>fetch('/history.json').then(r=>r.json()).then(j=>{for(const row of [...j.verdicts,...j.voice]){const article=document.createElement('article'),heading=document.createElement('h2'),p=document.createElement('p'),time=document.createElement('time');heading.textContent=(row.verdict==='NOTE'?'Note saved':row.verdict||'Voice note')+' / '+row.id.replaceAll('-',' ');p.textContent=row.note||'';time.textContent=new Date(row.ts).toLocaleString();article.append(heading,p,time);document.querySelector('main').append(article);if(row.chunk_id){const a=document.createElement('audio');a.controls=true;a.src='/audio?id='+encodeURIComponent(row.chunk_id);article.append(a);}}});</script></body></html>''', 'text/html')
            if path == '/audio':
                cid = parse_qs(u.query).get('id', [''])[0]
                rows = panel_verdict_log.read_rows(ROOT / 'voice.jsonl')
                row = next((r for r in rows if r.get('chunk_id') == cid), None)
                if not row:
                    return self.send(404, {'error': 'Audio unavailable.'})
                p = Path(row['audio'])
                return self.send(200, p.read_bytes(), row['audio_type'])
            if path == '/media':
                value = parse_qs(u.query).get('p', [''])[0]
                allowed = {m for p in (ROOT / 'queue').glob('*.json') for m in source(p.stem)['media']}
                if value not in allowed:
                    return self.send(404, {'error': 'Attachment unavailable.'})
                p = media_path(value)
                return self.file(p)
            names = {'/': 'review-next.html', '/next': 'review-next.html', '/manifest.webmanifest': 'pwa/manifest.webmanifest', '/sw.js': 'pwa/sw.js', '/apple-touch-icon.png': 'pwa/apple-touch-icon.png', '/favicon.ico': 'pwa/review-favicon.svg'}
            name = names.get(path)
            if name is None and path in {'/panel.js', '/mic-outbox.js', '/review-presentation.js', '/review-presentation.css'}:
                name = path[1:]
            if name is None and path == '/pwa/' + Path(path).name and Path(path).name in {p.name for p in (APP / 'pwa').iterdir()}:
                name = path[1:]
            if name:
                return self.file(APP / name)
            self.send(404, {'error': 'Not found.'})
        except (ValueError, OSError) as e:
            self.send(409, {'error': str(e)})

    def file(self, path):
        raw = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        match = re.fullmatch(r'bytes=(\d+)-(\d*)', self.headers.get('Range', ''))
        if match:
            start = int(match[1]); end = min(int(match[2]) if match[2] else len(raw)-1, len(raw)-1)
            if start > end:
                return self.send(416, b'', mime, {'Content-Range': f'bytes */{len(raw)}'})
            return self.send(206, raw[start:end+1], mime, {'Content-Range': f'bytes {start}-{end}/{len(raw)}', 'Accept-Ranges': 'bytes'})
        return self.send(200, raw, mime, {'Accept-Ranges': 'bytes'})

    def do_POST(self):
        try:
            # Same-origin JSON writes only; authentication also applies to voice.
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                return self.send(403, {'error': 'Cross-origin write refused.'})
            if self.path == '/voice/activity':
                return self.send(200 if self.authenticated() else 401, {'ok': self.authenticated()})
            body = self.body()
            if self.path == '/login':
                if not TOKEN or not hmac.compare_digest(str(body.get('token', '')), TOKEN):
                    return self.send(403, {'error': 'Token refused.'})
                value = hmac.new(TOKEN.encode(), b'review-panel-session', hashlib.sha256).hexdigest()
                secure = '; Secure' if os.environ.get('PANEL_SECURE_COOKIE') == '1' else ''
                return self.send(200, {'ok': True}, extra={'Set-Cookie': f'panel={value}; HttpOnly; SameSite=Strict; Path=/{secure}'})
            if not self.authenticated():
                return self.send(401, {'error': 'Enter the access token.'})
            if self.path not in {'/verdict', '/voice'}:
                return self.send(404, {'error': 'Not found.'})
            with LOCK, (ROOT / '.write.lock').open('a') as guard:
                fcntl.flock(guard, fcntl.LOCK_EX)
                cid = body.get('id') if self.path == '/verdict' else body.get('card')
                card, identities = shown(source(cid))
                if body.get('shown_sha256') != card['card_sha256']:
                    return self.send(409, {'error': 'Card or media changed. Reload and review again.'})
                retained = snapshot(card, identities)
                if self.path == '/voice':
                    store = panel_audio.CaptureStore(ROOT / 'voice.jsonl')
                    row = {'card': cid, 'text': '', 'ts': now()}
                    result = store.save(body, row)
                    if result.get('audio_durable') is not True:
                        raise ValueError('Audio was not retained.')
                    entry = store.preferred / body['chunk_id']
                    if not entry.exists():
                        entry = store.fallback / body['chunk_id']
                    manifest = json.loads((entry / 'manifest.json').read_text())
                    panel_verdict_log.append(ROOT / 'voice.jsonl', {'id': cid, 'card': cid, 'ts': now(), 'chunk_id': body['chunk_id'], 'request_id': body['chunk_id'], 'card_sha256': card['card_sha256'], 'audio': str(entry / manifest['audio_filename']), 'audio_type': body['audio_type'], 'snapshot': retained}, request_id=body['chunk_id'])
                    result.update(durable=True, feedback_status='durable')
                    return self.send(200, result)
                verdict = body.get('verdict')
                note = body.get('note', '')
                if verdict not in card['options'] + ['NOTE', 'UNCLEAR'] or not isinstance(note, str) or len(note) > 8000:
                    raise ValueError('Invalid answer or note.')
                settled = [r for r in history() if r['id'] == cid and r.get('verdict') != 'NOTE']
                if settled:
                    old = settled[-1]
                    if old['verdict'] == verdict and old['note'] == note and old['card_sha256'] == card['card_sha256']:
                        return self.send(200, {'ok': True, 'row': old})
                    return self.send(409, {'error': 'This card is answered. Publish a new identity for another decision.'})
                row = {'id': cid, 'verdict': verdict, 'note': note, 'ts': now(), 'card_sha256': card['card_sha256'], 'snapshot': retained}
                for key in ('clip_index', 't_seconds', 'elapsed_ms'):
                    if key in body:
                        row[key] = body[key]
                row = panel_verdict_log.append(ROOT / 'verdicts.jsonl', row)
                self.send(200, {'ok': True, 'row': row})
        except panel_audio.SaveError as e:
            self.send(e.status, e.response())
        except (ValueError, OSError, KeyError, TypeError) as e:
            self.send(409, {'error': str(e)})


def setup():
    ROOT.mkdir(parents=True, exist_ok=True)
    queue = ROOT / 'queue'
    if not queue.exists():
        shutil.copytree(APP / 'examples/queue', queue)
        shutil.copytree(APP / 'examples/media', ROOT / 'media', dirs_exist_ok=True)
    skipped = []
    ready = cards(skipped)
    for item in skipped:
        print(f"Skipped queue/{item['id']}.json: {item['error']}", flush=True)
    return ready


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    if args.host not in {'127.0.0.1', 'localhost', '::1'} and len(TOKEN) < 24:
        parser.error('Set PANEL_TOKEN to at least 24 characters before binding beyond localhost.')
    setup()
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as e:
        parser.exit(1, f'Port {args.port} is not available ({e.strerror}). Try: python3 server.py --port 8090\n')
    print(f'Review panel: http://{args.host}:{args.port}  (Control-C stops it)', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()

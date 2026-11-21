"""Optional real-browser check. Runs against an isolated sample queue."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

APP = Path(__file__).resolve().parents[1]


def run():
    with tempfile.TemporaryDirectory(prefix='panel-phone-') as temp:
        root = Path(temp) / 'data'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        env = dict(os.environ, PANEL_ROOT=str(root))
        for key in ('PANEL_TOKEN', 'MEDIA_ROOTS', 'PANEL_SECURE_COOKIE'):
            env.pop(key, None)
        proc = subprocess.Popen([sys.executable, 'server.py', '--port', str(port)], cwd=APP, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        base = f'http://127.0.0.1:{port}'
        try:
            for _ in range(100):
                try:
                    urlopen(base + '/review.json', timeout=.5).close(); break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('Server did not start.')
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'])
                context = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=1, permissions=['microphone'])
                page = context.new_page(); page.set_default_timeout(10000); errors = []
                page.add_init_script('''navigator.mediaDevices.getUserMedia=async () => {
                    const context=new AudioContext(), tone=context.createOscillator(), destination=context.createMediaStreamDestination();
                    tone.connect(destination);tone.start();await context.resume();return destination.stream;
                };''')
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('response', lambda r: errors.append(f'{r.status}: {r.url}') if r.status >= 400 else None)
                page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
                page.goto(base); page.locator('[data-card="garden-clips"]').wait_for()
                page.wait_for_function("() => document.querySelector('video').readyState>=2")
                page.locator('[data-media-btn="1"]').click()
                page.wait_for_function("() => document.querySelector('[data-media-btn=\"1\"]').classList.contains('on')")
                page.wait_for_function("() => document.querySelectorAll('video')[1].readyState>=2")
                assert page.locator('video').first.evaluate('(v)=>v.paused')
                screenshot_dir = Path(os.environ.get('PANEL_SCREENSHOTS', temp))
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshot_dir / '01-clips.png'), full_page=False)
                note = 'Use this clip. Keep the welcome easy to read.'
                page.locator('textarea[data-note]').fill(note)
                page.wait_for_function("() => document.querySelector('[data-nmsg]').textContent.startsWith('Saved')")
                # Reload proves a saved note is restored from the real log.
                page.reload(); page.locator('textarea[data-note]').wait_for()
                assert page.locator('textarea[data-note]').input_value() == note
                page.locator('[data-pick="Use clip B"]').click()
                page.locator('[data-card="garden-sign"]').wait_for()
                rows = [json.loads(x) for x in (root / 'verdicts.jsonl').read_text().splitlines()]
                answer = next(r for r in rows if r.get('verdict') == 'Use clip B')
                assert answer['id'] == 'garden-clips' and answer['note'] == note
                assert (root / answer['snapshot'] / 'card.json').is_file()
                page.locator('[data-media-btn="1"]').click()
                page.wait_for_function("() => document.querySelectorAll('.slide img')[1].complete && document.querySelectorAll('.slide img')[1].naturalWidth>0")
                page.wait_for_function("() => Math.abs(document.querySelector('#slides').scrollLeft-document.querySelector('#slides').clientWidth)<1")
                assert page.locator('.slide').nth(1).evaluate('(s)=>Math.abs(s.getBoundingClientRect().x)<1')
                page.screenshot(path=str(screenshot_dir / '02-sign.png'), full_page=False)
                page.locator('.slide').nth(1).locator('[data-fs]').click()
                page.locator('#lb.on img').wait_for()
                assert page.locator('#lb.on img').evaluate('(img)=>img.complete && img.naturalWidth>0')
                page.locator('#lb .close').click()
                # The required stranger decision above used only a typed note.
                # Separately exercise native MediaRecorder with a synthetic audio stream.
                # No physical microphone is used by this automated check.
                page.locator('[data-mic]').click()
                page.wait_for_timeout(1200)
                page.wait_for_function("() => document.querySelector('[data-mic]').textContent==='Stop and add' || document.querySelector('[data-nmsg]').textContent.startsWith('Not saved')")
                assert page.locator('[data-mic]').inner_text() == 'Stop and add', (page.locator('[data-nmsg]').inner_text(), errors)
                page.wait_for_timeout(500)
                page.locator('[data-mic]').click()
                page.wait_for_function("() => document.querySelector('[data-nmsg]').textContent==='Voice note saved.'")
                page.wait_for_function('() => window.MicOutbox.pending.size===0')
                voice = [json.loads(x) for x in (root / 'voice.jsonl').read_text().splitlines()]
                assert len(voice) == 1 and Path(voice[0]['audio']).stat().st_size > 0
                page.locator('[data-pick="Use sign B"]').click()
                page.locator('.done').wait_for()
                page.goto(base + '/history'); page.get_by_text('Use clip B / garden clips', exact=False).wait_for()
                page.screenshot(path=str(screenshot_dir / '03-history.png'), full_page=False)
                assert page.locator('audio').count() == 1
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors, errors
                browser.close()
                result = {'status': 'PASS', 'viewport': 390, 'console_errors': errors, 'typed_note': note, 'verdict': answer, 'audio_recordings': len(voice), 'microphone_source': 'synthetic Web Audio stream; no physical device claim'}
                print(json.dumps(result))
                if os.environ.get('PANEL_SCREENSHOTS'):
                    (screenshot_dir / 'phone-check.json').write_text(json.dumps(result, indent=2) + '\n')
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()


if __name__ == '__main__': run()

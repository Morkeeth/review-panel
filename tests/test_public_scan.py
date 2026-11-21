"""The push gate refuses a leak and lets a clean tree through, through a real git push."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]
SCANNER = APP / 'scripts' / 'public_scan.py'
# Built at runtime so this file never carries the strings it plants.
PLANTED_PATH = '/' + 'Users' + '/someone/notes/plan.md'
PLANTED_ID = 'garden-' + 'secret-card-ninth'


class PushGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='push-gate-'))
        queue = self.tmp / 'queue'; queue.mkdir()
        (queue / f'{PLANTED_ID}.json').write_text('{}')
        (queue / 'garden-another-card-tenth.json').write_text('{}')
        self.deny = self.tmp / 'deny.json'
        self.deny.write_text(json.dumps({
            'patterns': {'email': ['someone@example\\.org']},
            'card_id_sources': {'queue_dirs': [str(queue)], 'min_length': 12, 'min_count': 2},
        }))
        self.remote = self.tmp / 'remote.git'
        self.git('init', '-q', '--bare', str(self.remote), cwd=self.tmp)
        self.work = self.tmp / 'work'
        self.git('init', '-q', '-b', 'main', str(self.work), cwd=self.tmp)
        (self.work / 'README.md').write_text('An invented garden demo.\n')
        self.commit('Add readme')
        hooks = self.work / '.git' / 'hooks'
        shutil.copy(SCANNER, hooks / 'pre-push'); (hooks / 'pre-push').chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args, cwd=None, env=None):
        return subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', *args],
                              cwd=cwd or self.work, env=env, capture_output=True, text=True)

    def commit(self, message):
        self.git('add', '-A'); r = self.git('commit', '-q', '-m', message); self.assertEqual(r.returncode, 0, r.stderr)

    def push(self, deny=None):
        env = dict(os.environ, PUBLIC_SCAN_DENY=str(deny or self.deny))
        return self.git('push', str(self.remote), 'main:main', env=env)

    def test_clean_tree_passes(self):
        r = self.push()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('CLEAN', r.stderr)

    def test_home_path_and_card_id_are_refused_with_counts_only(self):
        (self.work / 'notes.md').write_text(f'See {PLANTED_PATH} and the card {PLANTED_ID}.\n')
        self.commit('Add notes')
        r = self.push()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('REFUSED', r.stderr)
        self.assertRegex(r.stderr, r'home_path: [1-9]')
        self.assertRegex(r.stderr, r'real_card_ids: [1-9]')
        self.assertNotIn(PLANTED_ID, r.stderr + r.stdout)
        self.assertNotIn('/someone/', r.stderr + r.stdout)
        self.assertEqual(self.git('ls-remote', str(self.remote)).stdout.strip(), '')

    def test_leak_removed_in_a_later_commit_is_still_refused(self):
        (self.work / 'notes.md').write_text(f'card {PLANTED_ID}\n'); self.commit('Add notes')
        (self.work / 'notes.md').unlink(); self.commit('Remove notes')
        r = self.push()
        self.assertNotEqual(r.returncode, 0)
        self.assertRegex(r.stderr, r'real_card_ids: [1-9]')

    def test_key_shape_and_long_number_are_refused(self):
        (self.work / 'k.txt').write_text('ghp_' + 'A' * 24 + '\ntotal ' + '12' + '34567' + ' tokens\n'); self.commit('Add k')
        r = self.push()
        self.assertNotEqual(r.returncode, 0)
        self.assertRegex(r.stderr, r'keys: [1-9]')
        self.assertRegex(r.stderr, r'long_numbers: [1-9]')

    def test_card_id_inside_a_longer_name_is_not_a_hit(self):
        (self.work / 'n.md').write_text(f'folder ./my-{PLANTED_ID}-copy here\n'); self.commit('Add n')
        r = self.push()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_missing_private_patterns_fail_closed(self):
        r = self.push(deny=self.tmp / 'absent.json')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('REFUSED', r.stderr)

    def test_too_few_card_ids_fail_closed(self):
        data = json.loads(self.deny.read_text()); data['card_id_sources']['min_count'] = 50
        self.deny.write_text(json.dumps(data))
        r = self.push()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('card ids', r.stderr)


if __name__ == '__main__':
    unittest.main()

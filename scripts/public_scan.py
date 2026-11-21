#!/usr/bin/env python3
"""Leak scan and pre-push gate for a public copy of this repository.

It prints counts only, never the matched text. The engine carries generic checks
(home paths, key shapes, long numbers, cached Python files). Your private patterns
(email, names, company words, real card identities) live in a JSON file outside the
repository, so the public copy never contains the list it guards against.

  python3 scripts/public_scan.py [REF]          scan REF (default HEAD) and its history
  cp scripts/public_scan.py .git/hooks/pre-push   install it as the push gate

The private file comes from PUBLIC_SCAN_DENY, or from `git config publicscan.deny`.
The gate refuses the push when that file is missing, unreadable, or yields too few
card identities, so a scan that cannot see is never reported as clean.

Private file format (every key is optional):
  {"patterns": {"email": ["me@example\\.org"], "names": ["\\bmy-company\\b"]},
   "pattern_sources": [{"label": "terms", "file": "~/x.py", "extract": "^RX = r'(.+)'$", "min": 1}],
   "card_id_sources": {"queue_dirs": ["~/panel/queue"], "verdict_logs": ["~/panel/verdicts.jsonl"],
                       "id_files": ["~/ids.json"], "min_length": 12, "min_count": 1},
   "strip_before_count": ["^allowed trailer line$"]}
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ZERO = '0' * 40
BINARY = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.mp4', '.webm', '.woff', '.woff2')
# Assembled so that this file does not match its own check.
HOME = re.compile('/' + 'Users/|/home/[a-z]')
KEYS = re.compile('|'.join([
    r'sk-[A-Za-z0-9_-]{16,}', r'ghp_[A-Za-z0-9]{20,}', 'github' + '_pat_', r'AKIA[0-9A-Z]{16}',
    r'BEGIN [A-Z ]*PRIVATE KEY', r'xox[abpr]-', r'PANEL_TOKEN=[\'"]?[A-Za-z0-9_-]{24,}',
]))
LONG_NUMBER = re.compile(r'(?<![0-9a-fA-F.])\d{1,3}(?:[,_]\d{3}){2,}(?![0-9])|(?<![0-9a-fA-F.])\d{7,}(?![0-9a-fA-F])')
# Audio size cap, max z-index, request size cap, a digit charset and CSS white in the shipped code.
ALLOWED_NUMBERS = {'5592408', '2147483647', '6000000', '0123456789', '255,255,255'}


class Blind(Exception):
    """The scan cannot see what it must check."""


def git(repo, *args, binary=False):
    r = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=not binary,
                       **({} if binary else {'errors': 'replace'}))
    if r.returncode:
        raise Blind('git ' + args[0] + ' failed')
    return r.stdout


def expand(p):
    return Path(os.path.expanduser(p))


def deny_path(repo):
    env = os.environ.get('PUBLIC_SCAN_DENY')
    if env:
        return expand(env)
    r = subprocess.run(['git', '-C', str(repo), 'config', '--get', 'publicscan.deny'], capture_output=True, text=True)
    if r.stdout.strip():
        return expand(r.stdout.strip())
    raise Blind('no private pattern file: set PUBLIC_SCAN_DENY or git config publicscan.deny')


def load_rules(repo):
    path = deny_path(repo)
    try:
        cfg = json.loads(path.read_text())
    except (OSError, ValueError):
        raise Blind('private pattern file is missing or unreadable')
    checks = {'home_path': [HOME], 'keys': [KEYS]}
    for label, rxs in cfg.get('patterns', {}).items():
        checks[label] = [re.compile(x, re.I) for x in rxs]
    for src in cfg.get('pattern_sources', []):
        try:
            text = expand(src['file']).read_text()
        except OSError:
            raise Blind(src['label'] + ' source is unreadable')
        found = [re.compile(m.group(1), re.I) for m in re.finditer(src['extract'], text, re.M)]
        if len(found) < src.get('min', 1):
            raise Blind(src['label'] + ' source yielded too few patterns')
        checks[src['label']] = found
    ids = set()
    ids_cfg = cfg.get('card_id_sources', {})
    shortest = ids_cfg.get('min_length', 12)
    for d in ids_cfg.get('queue_dirs', []):
        ids.update(p.stem for p in expand(d).glob('*.json'))
    for log in ids_cfg.get('verdict_logs', []):
        try:
            for line in expand(log).read_text(errors='replace').splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get('id'), str):
                    ids.add(row['id'])
        except OSError:
            raise Blind('a verdict log is unreadable')
    for f in ids_cfg.get('id_files', []):
        try:
            ids.update(x for x in json.loads(expand(f).read_text()).get('ids', []) if isinstance(x, str))
        except (OSError, ValueError):
            raise Blind('an identity file is unreadable')
    # A plain word such as a log category is not a card identity; real ids carry a hyphen, underscore or digit.
    ids = sorted(i for i in ids if len(i) >= shortest and re.search(r'[-_0-9]', i))
    if len(ids) < ids_cfg.get('min_count', 1):
        raise Blind(f'only {len(ids)} card ids loaded, fewer than the required {ids_cfg.get("min_count", 1)}')
    if ids:
        # Whole identities only: an id inside a longer hyphenated name is a different word.
        checks['real_card_ids'] = [re.compile(r'(?<![\w-])(?:' + '|'.join(map(re.escape, ids)) + r')(?![\w-])')]
    strip = [re.compile(x, re.M) for x in cfg.get('strip_before_count', [])]
    return checks, strip, len(ids)


def count(text, checks, strip, binary=False):
    for rx in strip:
        text = rx.sub('', text)
    out = {}
    for label, rxs in checks.items():
        out[label] = sum(len(rx.findall(text)) for rx in rxs)
    if not binary:
        lines = '\n'.join(l for l in text.split('\n') if not l.startswith('index '))
        out['long_numbers'] = sum(1 for m in LONG_NUMBER.finditer(lines) if m.group(0) not in ALLOWED_NUMBERS)
    return out


def scan(repo, ref, base=None):
    """Scan the tree at ref, every commit message, author and patch reachable from ref, and binaries."""
    checks, strip, n_ids = load_rules(repo)
    span = [ref] if base is None else [f'{base}..{ref}']
    files = [f for f in git(repo, 'ls-tree', '-r', '--name-only', ref).split('\n') if f]
    surfaces = {'filenames': '\n'.join(files)}
    surfaces['tree_text'] = '\n'.join(git(repo, 'show', f'{ref}:{f}') for f in files if not f.lower().endswith(BINARY))
    surfaces['history'] = git(repo, 'log', *span, '-p', '--format=%H%n%an <%ae>%n%cn <%ce>%n%B')
    raw = []
    for line in git(repo, 'rev-list', '--objects', *span).split('\n'):
        parts = line.split(' ', 1)
        if len(parts) == 2 and parts[1].lower().endswith(BINARY):
            raw.append(git(repo, 'cat-file', 'blob', parts[0], binary=True).decode('latin-1'))
    surfaces['binaries'] = '\n'.join(raw)
    hits = {s: count(t, checks, strip, binary=(s == 'binaries')) for s, t in surfaces.items()}
    cached = sum(1 for f in files if f.endswith('.pyc') or '__pycache__' in f)
    totals = {}
    for per in hits.values():
        for k, v in per.items():
            totals[k] = totals.get(k, 0) + v
    totals['cached_python'] = cached
    return {'ref': ref, 'commits': len(git(repo, 'rev-list', *span).split()), 'tracked_files': len(files),
            'card_ids_checked': n_ids, 'patterns_checked': sum(len(v) for v in checks.values()),
            'totals': totals, 'total': sum(totals.values())}


def report(result, out=sys.stderr):
    print(f"public scan {result['ref'][:12]}: {result['commits']} commits, {result['tracked_files']} files, "
          f"{result['card_ids_checked']} card ids, {result['patterns_checked']} patterns", file=out)
    for k, v in sorted(result['totals'].items()):
        print(f'  {k}: {v}', file=out)
    print('  CLEAN' if result['total'] == 0 else f"  REFUSED: {result['total']} hits (counts only; run scripts/public_scan.py locally)", file=out)


def pre_push(repo):
    worst = 0
    for line in sys.stdin.read().splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO:
            continue  # a branch deletion carries nothing
        # Scan the whole history of what is pushed: a leak added then removed is still public.
        result = scan(repo, parts[1])
        report(result)
        worst = max(worst, result['total'])
    return 1 if worst else 0


def main():
    repo = Path('.').resolve()  # git runs hooks from the top of the working tree
    hook = Path(sys.argv[0]).name == 'pre-push'
    try:
        if hook:
            return pre_push(repo)
        ref = sys.argv[1] if len(sys.argv) > 1 else 'HEAD'
        result = scan(repo, ref)
        report(result, out=sys.stdout)
        return 1 if result['total'] else 0
    except Blind as e:
        print(f'public scan REFUSED: {e}. The scan cannot see, so nothing is called clean.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

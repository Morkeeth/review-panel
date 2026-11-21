"""Trimmed publisher checks from the working panel's card contract."""
import argparse
import json
import re
from pathlib import Path

KEYS = ('id', 'title', 'project', 'what', 'why', 'question', 'options', 'media', 'created', 'lane', 'evidence', 'recommendation')


def errors(card, previous=()):
    if not isinstance(card, dict):
        return ['Card must be an object.']
    result = [f'Missing {k}.' for k in KEYS if k not in card]
    for key in ('id', 'title', 'project', 'what', 'why', 'question', 'created', 'lane'):
        if not isinstance(card.get(key), str) or not card[key].strip():
            result.append(f'{key} must be nonempty text.')
    if not re.fullmatch(r'[A-Za-z0-9-]{1,120}', str(card.get('id', ''))):
        result.append('Invalid identity.')
    question = str(card.get('question', ''))
    if question.count('?') != 1 or not question.endswith('?') or re.search(r'\b(?:also|and|then decide|second question)\b', question, re.I):
        result.append('Ask one plain decision in a question.')
    if len(str(card.get('what', '')).split()) < 8:
        result.append('Explain the result before the choices.')
    if len(str(card.get('why', '')).split()) < 5:
        result.append('Explain what changes for the reviewer.')
    opts = card.get('options')
    if not isinstance(opts, list) or not 1 <= len(opts) <= 4 or any(not isinstance(o, str) or not 1 <= len(o.split()) <= 3 for o in opts):
        result.append('Use one to four options, each at most three words.')
    elif len(set(opts)) != len(opts) or any(o in {'NOTE', 'UNCLEAR'} for o in opts):
        result.append('Use distinct option labels other than reserved actions.')
    media = card.get('media')
    if not isinstance(media, list) or not media or any(not isinstance(m, str) for m in media):
        result.append('Attach real media paths as a nonempty list.')
    for key in ('context', 'evidence'):
        if key in card and (not isinstance(card[key], list) or any(not isinstance(x, str) for x in card[key])):
            result.append(f'{key} must be a list of text.')
    if any(str(old.get('what', '')).strip().casefold() == str(card.get('what', '')).strip().casefold() for old in previous):
        result.append('This repeats an existing card. Supply a new result.')
    return result


def assert_card(card, previous=()):
    issues = errors(card, previous)
    if issues:
        raise ValueError('; '.join(issues))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('source', type=Path); a = p.parse_args()
    issues = errors(json.loads(a.source.read_text()))
    print(json.dumps({'status': 'RED' if issues else 'GREEN', 'issues': issues}))
    raise SystemExit(bool(issues))

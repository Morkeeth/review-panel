#!/usr/bin/env python3
"""Validate and publish an unused identity into the configured panel queue."""
import argparse
import fcntl
import json
from pathlib import Path
import server
from card_gate import assert_card


def publish(source, write=False):
    server.setup()
    card = json.loads(source.read_text())
    queue = server.ROOT / 'queue'
    with (server.ROOT / '.write.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = [server.source(p.stem) for p in queue.glob('*.json')]
        assert_card(card, previous)
        server.validate(card)
        if (queue / (card['id'] + '.json')).exists() or any(r['id'] == card['id'] for r in server.history()):
            raise ValueError('Identity already exists. Preserve the answer and publish a new id.')
        if write:
            server.immutable(queue / (card['id'] + '.json'), server.encoded(card))
    return {'status': 'GREEN', 'published': write, 'id': card['id']}


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('source', type=Path); p.add_argument('--publish', action='store_true'); a = p.parse_args()
    try:
        print(json.dumps(publish(a.source, a.publish)))
    except (ValueError, OSError) as e:
        p.exit(1, str(e) + '\n')

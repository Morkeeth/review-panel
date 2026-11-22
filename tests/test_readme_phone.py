"""The documented Tailscale command must run before the foreground server."""

import re
import unittest
from pathlib import Path

README = Path(__file__).resolve().parents[1] / 'README.md'


class ReadmePhoneTest(unittest.TestCase):
    def test_tailscale_serve_before_server(self):
        blocks = re.findall(r'```sh\n(.*?)```', README.read_text(), re.DOTALL)
        block, = (body for body in blocks if 'tailscale serve' in body)
        serve = re.search(r'^tailscale serve --bg\b', block, re.MULTILINE)
        server = re.search(r'\bpython3 server\.py\b', block)
        self.assertIsNotNone(serve)
        self.assertIsNotNone(server)
        self.assertLess(serve.start(), server.start())

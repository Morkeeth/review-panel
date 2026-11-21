"""Login page HTML carries phone-friendly input and viewport settings."""
import re
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / 'server.py'


def login_html():
    text = SERVER.read_text()
    match = re.search(
        r"if path == '/login':\s*\n\s*return self\.send\(200, b'''(.+?)''', 'text/html'\)",
        text,
        re.DOTALL,
    )
    assert match, 'login HTML literal not found in server.py'
    return match.group(1)


class LoginPageTest(unittest.TestCase):
    def test_input_and_viewport(self):
        html = login_html()
        self.assertIn('initial-scale=1', html)
        self.assertRegex(html, r'input[^>]*autocapitalize="off"')
        input_block = re.search(r'input\[type=password\]\{[^}]+\}', html)
        self.assertIsNotNone(input_block, 'password input CSS rule missing')
        self.assertRegex(input_block.group(0), r'font-size:\s*(1[6-9]|[2-9]\d)px')


if __name__ == '__main__':
    unittest.main()
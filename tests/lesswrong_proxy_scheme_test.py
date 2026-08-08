"""The LessWrong browser warm-up must hand Chromium a proxy scheme it accepts.

Context / why this exists
-------------------------
2026-08-05 through 08-08: LessWrong collected 0 posts every day (normally
18-22), and on Saturday 08-08 that took the whole research category to 3 items.
Nothing failed loudly — collection_status stayed "success" all four days.

The chain: LessWrong's direct GraphQL fallback started answering 429 to CI's
Mullvad exit (it had been passing on the FIRST try before 08-05, so the
Playwright warm-up path was never exercised). The warm-up then died instantly
with `net::ERR_NO_SUPPORTED_PROXIES`, because the CI tunnel step exports
PIPELINE_PROXY_URL=socks5h://10.64.0.1:1080 and Chromium does not recognise the
`socks5h` scheme — the trailing `h` (resolve DNS at the proxy) is a
curl/requests spelling. Chromium accepts only `socks5://` and already resolves
DNS remotely for SOCKS5, so the correct move is to normalise the scheme for the
browser while leaving it untouched for requests.

The proxy URL had carried socks5h since the Mullvad step was added; the bug was
latent the whole time and only surfaced when the direct path stopped working.

Stdlib-only: lesswrong_cookie_fetch imports requests and playwright at module
level, so both are stubbed before import — this test only exercises the pure
URL-rewriting classmethod.
"""

import importlib.util
import sys
import types
import unittest
import unittest.mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _stub(name: str, **attrs) -> None:
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod


# lesswrong_cookie_fetch does `import requests` and two playwright imports at
# module level. None are needed by _playwright_proxy_config.
_stub("requests", Response=object)
_stub("playwright")
_stub("playwright.sync_api", TimeoutError=TimeoutError, sync_playwright=None)
sys.modules["playwright"].sync_api = sys.modules["playwright.sync_api"]

_spec = importlib.util.spec_from_file_location(
    "_lesswrong_cookie_fetch", REPO_ROOT / "scripts" / "lesswrong_cookie_fetch.py"
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)
LessWrongClient = _mod.LessWrongClient


class PlaywrightProxySchemeTest(unittest.TestCase):
    PROXY_VARS = (
        "LESSWRONG_PROXY_URL", "PIPELINE_PROXY_URL",
        "HTTPS_PROXY", "https_proxy",
        "HTTP_PROXY", "http_proxy",
        "ALL_PROXY", "all_proxy",
    )

    def _config_for(self, url):
        with unittest.mock.patch.dict("os.environ") as env:
            # Higher-priority vars would shadow the one under test.
            for name in self.PROXY_VARS:
                env.pop(name, None)
            env["PIPELINE_PROXY_URL"] = url
            return LessWrongClient._playwright_proxy_config()

    def test_socks5h_is_rewritten_for_chromium(self):
        """The exact URL the CI Mullvad step exports."""
        config = self._config_for("socks5h://10.64.0.1:1080")
        self.assertEqual({"server": "socks5://10.64.0.1:1080"}, config)

    def test_plain_socks5_passes_through(self):
        config = self._config_for("socks5://1.2.3.4:1080")
        self.assertEqual({"server": "socks5://1.2.3.4:1080"}, config)

    def test_http_proxy_with_credentials_is_split_not_rewritten(self):
        config = self._config_for("http://user:pw@proxy.example:8080")
        self.assertEqual(
            {"server": "http://proxy.example:8080", "username": "user", "password": "pw"},
            config,
        )

    def test_no_proxy_env_means_no_config(self):
        with unittest.mock.patch.dict("os.environ") as env:
            for name in self.PROXY_VARS:
                env.pop(name, None)
            self.assertIsNone(LessWrongClient._playwright_proxy_config())


if __name__ == "__main__":
    unittest.main()

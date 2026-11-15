"""Run the daily pipeline's real Mullvad step against stubbed network tools.

On 2026-11-15 the Mullvad account had expired: every relay came up but carried
no traffic, and the step exported the dead SOCKS proxy anyway. News, research
and social all collected 0 items and the hero image timed out. A tunnel that
never returns an external IP must leave the pipeline running direct.
"""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

STUBS = {
    "apt-get": "exit 0\n",
    # The step edits relay configs with GNU `sed -i`; skip it so the test also
    # runs on BSD sed. Everything else runs as the caller.
    "sudo": 'if [[ "$1" == sed ]]; then exit 0; fi\nexec "$@"\n',
    "wg-quick": 'echo "$@" >> "$STUB_LOG"\n',
    "python": """\
case "$*" in
  "-m pip"*) exit 0 ;;
  *wg-mullvad.py*)
    out=""; prev=""
    for arg in "$@"; do
      [[ "$prev" == --output-dir ]] && out="$arg"
      prev="$arg"
    done
    mkdir -p "$out"
    for relay in us-atl-wg-401 us-atl-wg-402 us-atl-wg-403; do
      printf '[Interface]\\n' > "$out/$relay.conf"
    done
    ;;
  *probe_lesswrong.py*) exit "${PROBE_RC:-1}" ;;
  *) exit 1 ;;
esac
""",
    "curl": """\
prev=""
for arg in "$@"; do
  if [[ "$prev" == -o ]]; then : > "$arg"; exit 0; fi
  prev="$arg"
done
if [[ "$*" == *am.i.mullvad.net* ]]; then
  if [[ -n "${CURL_IP:-}" ]]; then echo "$CURL_IP"; exit 0; fi
  echo "curl: (28) Connection timed out" >&2
  exit 28
fi
exit 1
""",
}


def _mullvad_step_script() -> str:
    workflow = yaml.safe_load((ROOT / ".github/workflows/daily-pipeline.yml").read_text())
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name", "").startswith("Start Mullvad tunnel"):
                return step["run"]
    raise AssertionError("Mullvad step not found in daily-pipeline.yml")


class MullvadTunnelStepTests(unittest.TestCase):
    def _run(self, curl_ip: str, probe_rc: int):
        script = _mullvad_step_script()
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            bin_dir = work / "bin"
            bin_dir.mkdir()
            # The step calls the tunnel helper as `python` and the LessWrong
            # probe as `python3`; both go to the same stub.
            for name, body in {**STUBS, "python3": STUBS["python"]}.items():
                path = bin_dir / name
                path.write_text("#!/usr/bin/env bash\n" + body)
                path.chmod(path.stat().st_mode | stat.S_IXUSR)
            github_env = work / "github_env"
            github_env.touch()
            stub_log = work / "stub_log"
            stub_log.touch()
            run_dir = work / "repo"
            run_dir.mkdir()
            env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "GITHUB_ENV": str(github_env),
                "STUB_LOG": str(stub_log),
                "MULLVAD_ACCOUNT": "0000000000000000",
                "MULLVAD_RELAY_FILTER": "us-atl",
                "MULLVAD_WG_PRIVATE_KEY": "",
                "CURL_IP": curl_ip,
                "PROBE_RC": str(probe_rc),
            }
            result = subprocess.run(
                ["bash", "-c", script], cwd=run_dir, env=env,
                capture_output=True, text=True, timeout=60,
            )
            known_good = run_dir / ".mullvad-state" / "known_good_relay"
            return (
                result,
                github_env.read_text(),
                stub_log.read_text(),
                known_good.read_text().strip() if known_good.exists() else None,
            )

    def test_dead_tunnel_runs_direct_without_exporting_the_proxy(self):
        result, github_env, stub_log, known_good = self._run(curl_ip="", probe_rc=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("PIPELINE_PROXY_URL", github_env)
        self.assertNotIn("REDDIT_PROXY_URL", github_env)
        self.assertIn("::error::", result.stdout)
        self.assertIn("Running this pipeline WITHOUT the proxy", result.stdout)
        # Every relay that was brought up was taken down again.
        ups = [l for l in stub_log.splitlines() if l.startswith("up ")]
        downs = [l for l in stub_log.splitlines() if l.startswith("down ")]
        self.assertEqual(len(ups), len(downs), stub_log)
        self.assertIsNone(known_good)

    def test_lesswrong_blocked_but_live_tunnel_keeps_the_proxy(self):
        result, github_env, stub_log, known_good = self._run(curl_ip="198.51.100.7", probe_rc=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PIPELINE_PROXY_URL=socks5h://10.64.0.1:1080", github_env)
        self.assertIn("No Mullvad relay passed the LessWrong probe", result.stdout)
        self.assertIn("198.51.100.7", result.stdout)
        # The relay left up is one that proved connectivity.
        self.assertTrue(stub_log.splitlines()[-1].startswith("up "), stub_log)
        self.assertIsNone(known_good)

    def test_relay_passing_the_probe_is_kept_and_cached(self):
        result, github_env, _, known_good = self._run(curl_ip="198.51.100.7", probe_rc=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PIPELINE_PROXY_URL=socks5h://10.64.0.1:1080", github_env)
        self.assertEqual(known_good, "us-atl-wg-401")


if __name__ == "__main__":
    unittest.main()

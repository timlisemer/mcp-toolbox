"""Run startup scripts against a private fixture; never use the installed container."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class LifecycleTests(unittest.TestCase):
    def run_script(self, script, exit_code):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "tools/astral-ai/bin").mkdir(parents=True)
            (root / "config/servers.json").write_text('{"tools":{"astral-ai":{"enabled":true}}}')
            executable = root / "tools/astral-ai/bin/astral-ai"
            executable.write_text(f'#!/usr/bin/env bash\nexit {exit_code}\n')
            executable.chmod(0o755)
            source = (ROOT / "scripts" / script).read_text().replace("/app/", f"{root}/")
            return subprocess.run(["bash", "-c", source], capture_output=True, timeout=5)

    def test_health_failure_is_visible(self):
        self.assertEqual(self.run_script("health-toolbox.sh", 78).returncode, 78)

    def test_failed_owner_setup_does_not_continue(self):
        self.assertEqual(self.run_script("prepare-astral-ai.sh", 78).returncode, 78)

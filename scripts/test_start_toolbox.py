"""Test startup and shutdown without Docker or an AI account."""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "servers.json"
        self.service = self.root / "astral-ai"
        self.events = self.root / "events"
        self.log = self.root / "stderr"
        self.script = self.root / "start-toolbox.sh"
        # Map container paths into a test directory. Keep the startup logic intact.
        source = Path(__file__).with_name("start-toolbox.sh").read_text()
        for original, replacement in {
            "/app/config/servers.json": str(self.config),
            "/app/tools/astral-ai/bin/astral-ai": str(self.service),
            "/run/current-system/sw/bin/tail": shutil.which("tail"),
        }.items():
            source = source.replace(original, replacement)
        self.script.write_text(source)

    def start(self, enabled, service_body=None):
        self.config.write_text(json.dumps({"tools": {"astral-ai": {"enabled": enabled}}}))
        if service_body is not None:
            self.service.write_text(
                f"#!{sys.executable}\n"
                "import json, os, signal, sys, time\n"
                "from pathlib import Path\n"
                f"events = Path({str(self.events)!r})\n"
                + service_body
            )
            self.service.chmod(0o755)
        with self.log.open("w") as log:
            self.process = subprocess.Popen(
                ["bash", str(self.script)], stderr=log, start_new_session=True
            )
        self.addCleanup(self.stop)

    def stop(self):
        # Clean up the whole test process group even when an assertion fails.
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait(timeout=3)

    def wait_for(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.01)
        self.fail("Timed out while waiting for the startup script")

    def assert_tail(self):
        def running_tail():
            try:
                args = Path(f"/proc/{self.process.pid}/cmdline").read_bytes().split(b"\0")
                return args[:3] == [os.fsencode(shutil.which("tail")), b"-f", b"/dev/null"]
            except FileNotFoundError:
                return False

        self.wait_for(running_tail)
        self.assertIsNone(self.process.poll())

    def test_disabled_needs_no_ai_files(self):
        self.start(False)
        self.assert_tail()
        self.assertFalse(self.service.exists())
        self.assertFalse(self.events.exists())
        self.assertEqual(self.log.read_text(), "")

    def test_enabled_starts_once_and_waits_for_shutdown(self):
        self.start(
            True,
            "def shutdown(signum, frame):\n"
            "    time.sleep(0.15)\n"
            "    with events.open('a') as output: output.write('stopped\\n')\n"
            "    sys.exit(0)\n"
            "signal.signal(signal.SIGTERM, shutdown)\n"
            "with events.open('a') as output:\n"
            "    output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "while True: time.sleep(0.01)\n",
        )
        self.wait_for(self.events.exists)
        self.wait_for(lambda: bool(self.events.read_text()))
        self.assertEqual(
            json.loads(self.events.read_text()),
            ["serve", "--settings", "/app/config/astral-settings.toml",
             "--mode", "docker", "--control-dir", "/var/lib/astral-ai/instances/docker/control",
             "--data-dir", "/var/lib/astral-ai/data", "--socket",
             "/var/lib/astral-ai/data/service.sock"],
        )
        self.assertIsNone(self.process.poll())
        self.process.terminate()
        self.assertEqual(self.process.wait(timeout=3), 0)
        self.assertEqual(len(self.events.read_text().splitlines()), 2)
        self.assertTrue(self.events.read_text().endswith("stopped\n"))

    def test_service_failure_stops_toolbox(self):
        self.start(True, "sys.exit(7)\n")
        self.assertEqual(self.process.wait(timeout=3), 7)

    def test_missing_binary_stops_toolbox(self):
        self.start(True)
        self.assertEqual(self.process.wait(timeout=3), 127)


if __name__ == "__main__":
    unittest.main()

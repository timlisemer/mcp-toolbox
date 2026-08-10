"""Tests for the adapter-independent MCP path bridge."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mcp_path_bridge import (
    BridgeConfiguration,
    BridgeError,
    HookFields,
    HookToolCall,
    PathMap,
    ServerPathProfile,
    build_client_command,
    translate_hook_request,
    translate_mcp_request,
    translate_mcp_response,
)


class PathMapTests(unittest.TestCase):
    """Verify the mapping contract in both directions."""

    def setUp(self) -> None:
        self.path_map = PathMap(
            [
                {"windows_prefix": "C:\\", "linux_prefix": "/mnt/c"},
                {"windows_prefix": "D:\\repo", "linux_prefix": "/srv/repo"},
                {
                    "windows_prefix": "D:\\repo\\nested",
                    "linux_prefix": "/srv/nested",
                },
                {
                    "windows_prefix": "\\\\wsl.localhost\\nixos\\home",
                    "linux_prefix": "/home",
                },
            ]
        )

    def test_drive_paths_accept_both_separators_and_ignore_prefix_case(self) -> None:
        self.assertEqual(
            self.path_map.to_linux(r"c:/Users/Tim/file.txt"),
            "/mnt/c/Users/Tim/file.txt",
        )
        self.assertEqual(
            self.path_map.to_linux(r"d:\REPO\source\main.py"),
            "/srv/repo/source/main.py",
        )

    def test_longest_prefix_and_component_boundary_are_required(self) -> None:
        self.assertEqual(
            self.path_map.to_linux(r"D:\repo\nested\file"),
            "/srv/nested/file",
        )
        with self.assertRaisesRegex(BridgeError, "no mapping"):
            self.path_map.to_linux(r"D:\repository-other\file")

    def test_relative_windows_paths_convert_separators_and_components(self) -> None:
        self.assertEqual(self.path_map.to_linux(r"relative\file.txt"), "relative/file.txt")
        self.assertEqual(self.path_map.to_linux(r"plans\draft\..\plan.md"), "plans/plan.md")
        self.assertEqual(self.path_map.to_linux("notes/C:/text"), "notes/C:/text")

    def test_absolute_paths_normalize_before_prefix_selection(self) -> None:
        self.assertEqual(
            self.path_map.to_linux(r"D:\repo\nested\..\source\main.py"),
            "/srv/repo/source/main.py",
        )
        with self.assertRaisesRegex(BridgeError, "no mapping"):
            self.path_map.to_linux(r"D:\repo\..\outside\file")

    def test_ambiguous_windows_paths_are_rejected(self) -> None:
        for path_value in (r"C:relative\file", r"\rooted\file"):
            with self.subTest(path_value=path_value):
                with self.assertRaisesRegex(BridgeError, "ambiguous"):
                    self.path_map.to_linux(path_value)

    def test_unmapped_absolute_and_device_paths_are_rejected(self) -> None:
        with self.assertRaisesRegex(BridgeError, "no mapping"):
            self.path_map.to_linux(r"E:\repo\file")
        with self.assertRaisesRegex(BridgeError, "device"):
            self.path_map.to_linux(r"\\.\PhysicalDrive0")
        with self.assertRaisesRegex(BridgeError, "device"):
            self.path_map.to_linux(r"\\?\C:\repo")

    def test_unc_paths_require_an_explicit_mapping(self) -> None:
        self.assertEqual(
            self.path_map.to_linux(r"\\wsl.localhost\nixos\home\tim\Coding"),
            "/home/tim/Coding",
        )
        with self.assertRaisesRegex(BridgeError, "no mapping"):
            self.path_map.to_linux(r"\\server\share\file")

    def test_linux_results_convert_back_to_windows(self) -> None:
        self.assertEqual(
            self.path_map.to_windows("/srv/nested/source/main.py"),
            r"D:\repo\nested\source\main.py",
        )
        self.assertEqual(self.path_map.to_windows("relative/file"), "relative/file")
        with self.assertRaisesRegex(BridgeError, "no mapping"):
            self.path_map.to_windows("/unmapped/file")


class MessageTranslationTests(unittest.TestCase):
    """Verify that only declared structured fields change."""

    def setUp(self) -> None:
        self.path_map = PathMap(
            [{"windows_prefix": "D:\\repo", "linux_prefix": "/srv/repo"}]
        )
        self.profile = ServerPathProfile(
            server="agent-framework",
            request_fields=("/working_dir", "/transcript_path"),
            result_fields=("/structuredContent/path", "/paths/*/location"),
            hook=HookFields(
                request_fields=("/transcript_path",),
                result_fields=("/result/path",),
                tool_calls=(
                    HookToolCall(
                        "/tool_name",
                        "/tool_input",
                        ("mcp__agent-framework__*",),
                    ),
                    HookToolCall(
                        "/request/input/tool_name",
                        "/request/input/tool_input",
                        ("mcp__agent-framework__*",),
                    ),
                ),
            ),
        )

    def test_mcp_request_changes_only_declared_argument_fields(self) -> None:
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "check",
                "arguments": {
                    "working_dir": r"D:\repo\project",
                    "message": r"Do not change D:\repo\project",
                    "relative": r"source\file.py",
                },
            },
        }
        translated = translate_mcp_request(message, self.profile, self.path_map)
        arguments = translated["params"]["arguments"]
        self.assertEqual(arguments["working_dir"], "/srv/repo/project")
        self.assertEqual(arguments["message"], r"Do not change D:\repo\project")
        self.assertEqual(arguments["relative"], r"source\file.py")

    def test_structured_results_convert_with_wildcards_but_text_does_not(self) -> None:
        message = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "structuredContent": {"path": "/srv/repo/plan.md"},
                "paths": [
                    {"location": "/srv/repo/a"},
                    {"location": "/srv/repo/b"},
                ],
                "content": [
                    {"type": "text", "text": "Linux path /srv/repo/plan.md"}
                ],
            },
        }
        translated = translate_mcp_response(message, self.profile, self.path_map)
        result = translated["result"]
        self.assertEqual(result["structuredContent"]["path"], r"D:\repo\plan.md")
        self.assertEqual(result["paths"][1]["location"], r"D:\repo\b")
        self.assertEqual(
            result["content"][0]["text"], "Linux path /srv/repo/plan.md"
        )

    def test_generic_hook_envelopes_translate_root_and_matching_tool_calls(self) -> None:
        direct = {
            "transcript_path": r"D:\repo\.codex\session.jsonl",
            "tool_name": "mcp__agent-framework__check",
            "tool_input": {"working_dir": r"D:\repo\project"},
            "message": r"Keep D:\repo\project",
        }
        translated_direct = translate_hook_request(direct, self.profile, self.path_map)
        self.assertEqual(
            translated_direct["transcript_path"], "/srv/repo/.codex/session.jsonl"
        )
        self.assertEqual(
            translated_direct["tool_input"]["working_dir"], "/srv/repo/project"
        )
        self.assertEqual(translated_direct["message"], r"Keep D:\repo\project")

        nested = {
            "request": {
                "input": {
                    "tool_name": "mcp__agent-framework__check",
                    "tool_input": {"working_dir": r"D:\repo\nested"},
                }
            }
        }
        translated_nested = translate_hook_request(nested, self.profile, self.path_map)
        self.assertEqual(
            translated_nested["request"]["input"]["tool_input"]["working_dir"],
            "/srv/repo/nested",
        )

    def test_unmatched_hook_tool_does_not_receive_server_rules(self) -> None:
        message = {
            "tool_name": "mcp__blender__render",
            "tool_input": {"working_dir": r"D:\repo\project"},
        }
        translated = translate_hook_request(message, self.profile, self.path_map)
        self.assertEqual(
            translated["tool_input"]["working_dir"], r"D:\repo\project"
        )


class ConfigurationTests(unittest.TestCase):
    """Verify conservative profile selection and transport command output."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        directory = Path(self.temporary.name)
        path_profile = directory / "agent-framework-paths.json"
        path_profile.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "server": "agent-framework",
                    "request_fields": ["/working_dir"],
                    "result_fields": [],
                    "hook": {
                        "request_fields": ["/transcript_path"],
                        "result_fields": [],
                        "tool_calls": [],
                    },
                }
            ),
            encoding="utf-8",
        )
        self.configuration_path = directory / "bridge.json"
        self.configuration_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "profiles": {
                        "wsl": {
                            "kind": "wsl",
                            "wsl_distro": "nixos",
                            "mappings": [
                                {
                                    "windows_prefix": "C:\\",
                                    "linux_prefix": "/mnt/c",
                                }
                            ],
                        },
                        "windows-remote": {
                            "kind": "windows-remote",
                            "ssh": {"host": "linux-host", "port": 2222},
                            "mappings": [
                                {
                                    "windows_prefix": "D:\\work",
                                    "linux_prefix": "/srv/work",
                                }
                            ],
                        },
                    },
                    "servers": {
                        "agent-framework": {
                            "enabled": True,
                            "hook_bridge": True,
                            "path_profile": path_profile.name,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.configuration = BridgeConfiguration(self.configuration_path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_explicit_and_environment_wsl_selection_validate_distribution(self) -> None:
        explicit = self.configuration.resolve_runtime_profile(
            "wsl", {"WSL_DISTRO_NAME": "NixOS"}
        )
        self.assertEqual(explicit.name, "wsl")
        fallback = self.configuration.resolve_runtime_profile(
            None, {"WSL": "1", "WSL_DISTRO_NAME": "nixos", "WSL_INTEROP": "socket"}
        )
        self.assertEqual(fallback.name, "wsl")
        with self.assertRaisesRegex(BridgeError, "does not match"):
            self.configuration.resolve_runtime_profile(
                "wsl", {"WSL_DISTRO_NAME": "Ubuntu"}
            )

    def test_remote_profile_does_not_detect_wsl(self) -> None:
        remote = self.configuration.resolve_runtime_profile(
            "windows-remote",
            {"WSL": "1", "WSL_DISTRO_NAME": "wrong", "WSL_INTEROP": "socket"},
        )
        self.assertEqual(remote.kind, "windows-remote")

    def test_missing_selection_does_not_guess(self) -> None:
        with self.assertRaisesRegex(BridgeError, "select a bridge profile"):
            self.configuration.resolve_runtime_profile(None, {})

    def test_client_commands_cover_both_transports_and_boundary_modes(self) -> None:
        wsl_command = build_client_command(
            self.configuration,
            "wsl",
            "mcp-stdio",
            "agent-framework",
            "/run/current-system/sw/bin/mcp-path-bridge",
            ["/opt/agent-framework/bin/agent-framework-mcp"],
        )
        self.assertEqual(wsl_command["command"], "wsl.exe")
        self.assertIn("mcp-stdio", wsl_command["args"])
        remote_command = build_client_command(
            self.configuration,
            "windows-remote",
            "hook",
            "agent-framework",
            "/run/current-system/sw/bin/mcp-path-bridge",
            ["/opt/agent-framework/bin/agent-framework-tool-policy-hook"],
        )
        self.assertEqual(remote_command["command"], "ssh")
        self.assertEqual(remote_command["args"][:2], ["-p", "2222"])
        self.assertIn(" hook ", remote_command["args"][-1])

    def test_mcp_proxy_translates_before_the_child_reads_the_request(self) -> None:
        bridge_script = Path(__file__).with_name("mcp_path_bridge.py")
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "check",
                "arguments": {"working_dir": r"C:\work\project"},
            },
        }
        child_program = (
            "import sys\n"
            "for input_line in sys.stdin.buffer:\n"
            "    sys.stdout.buffer.write(input_line)\n"
            "    sys.stdout.buffer.flush()\n"
        )
        environment = os.environ.copy()
        environment.update({"WSL": "1", "WSL_DISTRO_NAME": "nixos"})
        completed = subprocess.run(
            [
                sys.executable,
                str(bridge_script),
                "mcp-stdio",
                "--config",
                str(self.configuration_path),
                "--profile",
                "wsl",
                "--server",
                "agent-framework",
                "--",
                sys.executable,
                "-c",
                child_program,
            ],
            input=(json.dumps(request) + "\n").encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        observed = json.loads(completed.stdout)
        self.assertEqual(
            observed["params"]["arguments"]["working_dir"], "/mnt/c/work/project"
        )

    def test_hook_proxy_is_provider_neutral_and_translates_before_child_access(self) -> None:
        bridge_script = Path(__file__).with_name("mcp_path_bridge.py")
        request = {
            "transcript_path": r"D:\work\sessions\thread.jsonl",
            "tool_name": "provider_specific_name",
            "tool_input": {},
        }
        child_program = (
            "import json, sys\n"
            "request = json.load(sys.stdin)\n"
            "json.dump({'observed_path': request['transcript_path']}, sys.stdout)\n"
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(bridge_script),
                "hook",
                "--config",
                str(self.configuration_path),
                "--profile",
                "windows-remote",
                "--server",
                "agent-framework",
                "--",
                sys.executable,
                "-c",
                child_program,
            ],
            input=json.dumps(request).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        observed = json.loads(completed.stdout)
        self.assertEqual(observed["observed_path"], "/srv/work/sessions/thread.jsonl")

    def test_mcp_proxy_exits_when_child_fails_with_client_input_open(self) -> None:
        bridge_script = Path(__file__).with_name("mcp_path_bridge.py")
        process = subprocess.Popen(
            [
                sys.executable,
                str(bridge_script),
                "mcp-stdio",
                "--config",
                str(self.configuration_path),
                "--profile",
                "windows-remote",
                "--server",
                "agent-framework",
                "--",
                sys.executable,
                "-c",
                "raise SystemExit(7)",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            self.assertEqual(process.wait(timeout=3), 7)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()

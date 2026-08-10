#!/usr/bin/env python3
"""Translate declared path fields at MCP and JSON hook boundaries."""

from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import ntpath
import os
import posixpath
import re
import selectors
import shlex
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping, Sequence


MAXIMUM_MESSAGE_BYTES = 16 * 1024 * 1024
PROFILE_ENVIRONMENT_KEY = "MCP_TOOLBOX_BRIDGE_PROFILE"
REQUEST_THREAD_SHUTDOWN_SECONDS = 1.0
SUPPORTED_SCHEMA_VERSION = 1
WINDOWS_COMMAND_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    $jsonBytes = [Convert]::FromBase64String($env:MCP_TOOLBOX_COMMAND_REQUEST)
    $request = ConvertFrom-Json ([Text.Encoding]::UTF8.GetString($jsonBytes))
    if ($request.environment_mode -eq 'replace') {
        Get-ChildItem Env: | ForEach-Object {
            Remove-Item -LiteralPath ("Env:" + $_.Name) -ErrorAction SilentlyContinue
        }
    }
    foreach ($name in @($request.remove_environment)) {
        Remove-Item -LiteralPath ("Env:" + [string]$name) -ErrorAction SilentlyContinue
    }
    foreach ($entry in @($request.environment)) {
        Set-Item -LiteralPath ("Env:" + [string]$entry.name) -Value ([string]$entry.value)
    }
    Set-Location -LiteralPath ([string]$request.working_directory)
    $commandName = [string]$request.executable
    $resolved = @(Get-Command -Name $commandName -CommandType Application -ErrorAction SilentlyContinue)[0]
    if ($null -eq $resolved) {
        $resolved = @(Get-Command -Name $commandName -CommandType ExternalScript -ErrorAction SilentlyContinue)[0]
    }
    if ($null -eq $resolved) {
        [Console]::Error.WriteLine("Windows executable was not found: " + $commandName)
        exit 127
    }
    $commandArguments = @($request.arguments | ForEach-Object { [string]$_ })
    $global:LASTEXITCODE = $null
    & $resolved.Source @commandArguments
    if ($null -ne $LASTEXITCODE) {
        exit [int]$LASTEXITCODE
    }
    if ($?) {
        exit 0
    }
    exit 1
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 127
}
""".strip()


class BridgeError(Exception):
    """A bridge configuration, message, or path is invalid."""


class ChildExit(BridgeError):
    """The proxied process returned a nonzero status."""

    def __init__(self, return_code: int) -> None:
        super().__init__(f"proxied command exited with status {return_code}")
        self.return_code = return_code


@dataclass(frozen=True)
class PathRule:
    """One validated Windows-to-Linux prefix mapping."""

    windows_prefix: str
    linux_prefix: str
    execution_host: str


class PathMap:
    """Convert paths with longest-prefix, component-boundary matching."""

    def __init__(self, mappings: Sequence[Mapping[str, Any]]) -> None:
        if not mappings:
            raise BridgeError("a bridge profile must contain at least one path mapping")
        rules: list[PathRule] = []
        windows_prefixes: set[str] = set()
        linux_prefixes: set[str] = set()
        for index, mapping in enumerate(mappings):
            _require_keys(
                mapping,
                required={"windows_prefix", "linux_prefix", "execution_host"},
                optional=set(),
                context=f"mapping {index}",
            )
            windows_value = _require_string(
                mapping["windows_prefix"], f"mapping {index} windows_prefix"
            )
            linux_value = _require_string(
                mapping["linux_prefix"], f"mapping {index} linux_prefix"
            )
            execution_host = _require_string(
                mapping["execution_host"], f"mapping {index} execution_host"
            )
            if execution_host not in {"linux", "windows"}:
                raise BridgeError(
                    f"mapping {index} execution_host must be 'linux' or 'windows'"
                )
            windows_kind = _windows_path_kind(windows_value)
            if windows_kind == "device":
                raise BridgeError(
                    f"mapping {index} uses a forbidden Windows device path"
                )
            if windows_kind not in {"drive", "unc"}:
                raise BridgeError(
                    f"mapping {index} windows_prefix must be an absolute Windows path"
                )
            windows_prefix = _normalize_windows_absolute(windows_value)
            linux_prefix = _normalize_linux_absolute(linux_value)
            folded_windows = windows_prefix.casefold()
            if folded_windows in windows_prefixes:
                raise BridgeError(
                    f"mapping {index} duplicates Windows prefix {windows_prefix!r}"
                )
            if linux_prefix in linux_prefixes:
                raise BridgeError(
                    f"mapping {index} duplicates Linux prefix {linux_prefix!r}"
                )
            windows_prefixes.add(folded_windows)
            linux_prefixes.add(linux_prefix)
            rules.append(PathRule(windows_prefix, linux_prefix, execution_host))
        self._rules = tuple(rules)

    def to_linux(self, value: str) -> str:
        """Convert one absolute Windows path or keep one relative path unchanged."""

        path_kind = _windows_path_kind(value)
        if path_kind == "device":
            raise BridgeError(f"Windows device paths are not permitted: {value!r}")
        if path_kind in {"drive-relative", "rooted-relative"}:
            raise BridgeError(f"ambiguous Windows path is not permitted: {value!r}")
        if path_kind == "linux":
            return value
        if path_kind == "relative":
            return _normalize_windows_relative(value)
        normalized_path = _normalize_windows_absolute(value)
        selected = self._longest_windows_rule(normalized_path)
        if selected is None:
            raise BridgeError(f"no mapping exists for absolute Windows path {value!r}")
        remainder = normalized_path[len(selected.windows_prefix) :].lstrip("\\")
        if not remainder:
            return selected.linux_prefix
        converted_remainder = remainder.replace("\\", "/")
        if selected.linux_prefix == "/":
            return f"/{converted_remainder}"
        return f"{selected.linux_prefix}/{converted_remainder}"

    def to_windows(self, value: str) -> str:
        """Convert one absolute Linux path or keep one relative path unchanged."""

        if not value.startswith("/"):
            return value
        normalized_path = posixpath.normpath(value)
        selected = self._longest_linux_rule(normalized_path)
        if selected is None:
            raise BridgeError(f"no mapping exists for absolute Linux path {value!r}")
        remainder = normalized_path[len(selected.linux_prefix) :].lstrip("/")
        if not remainder:
            return selected.windows_prefix
        converted_remainder = remainder.replace("/", "\\")
        if selected.windows_prefix.endswith("\\"):
            return f"{selected.windows_prefix}{converted_remainder}"
        return f"{selected.windows_prefix}\\{converted_remainder}"

    def execution_host(self, linux_path: str) -> str:
        """Return the declared execution host for one absolute Linux path."""

        normalized_path = _normalize_linux_absolute(linux_path)
        selected = self._longest_linux_rule(normalized_path)
        if selected is None:
            raise BridgeError(
                f"no execution-host mapping exists for Linux path {linux_path!r}"
            )
        return selected.execution_host

    def _longest_windows_rule(self, path: str) -> PathRule | None:
        matching = [
            rule
            for rule in self._rules
            if _windows_prefix_matches(path, rule.windows_prefix)
        ]
        return max(matching, key=lambda rule: len(rule.windows_prefix), default=None)

    def _longest_linux_rule(self, path: str) -> PathRule | None:
        matching = [
            rule
            for rule in self._rules
            if _linux_prefix_matches(path, rule.linux_prefix)
        ]
        return max(matching, key=lambda rule: len(rule.linux_prefix), default=None)


@dataclass(frozen=True)
class HookToolCall:
    """Selectors for one provider hook tool-call envelope."""

    name_field: str
    arguments_field: str
    tool_name_patterns: tuple[str, ...]


@dataclass(frozen=True)
class HookFields:
    """Declared hook path fields and provider-neutral call envelopes."""

    request_fields: tuple[str, ...]
    result_fields: tuple[str, ...]
    tool_calls: tuple[HookToolCall, ...]


@dataclass(frozen=True)
class CommandBridgeProfile:
    """Server-owned command-bridge protocol settings."""

    environment_variable: str
    protocol_version: int


@dataclass(frozen=True)
class ServerPathProfile:
    """Path fields owned by one MCP server."""

    server: str
    request_fields: tuple[str, ...]
    result_fields: tuple[str, ...]
    hook: HookFields
    command_bridge: CommandBridgeProfile | None = None

    @classmethod
    def load(cls, path: Path) -> ServerPathProfile:
        """Load and validate one server-owned path profile."""

        data = _load_json_object(path, "server path profile")
        _require_keys(
            data,
            required={"schema_version", "server", "request_fields", "result_fields"},
            optional={"hook", "command_bridge"},
            context="server path profile",
        )
        _require_schema_version(data["schema_version"], "server path profile")
        server = _require_string(data["server"], "server path profile server")
        request_fields = _validate_selectors(
            data["request_fields"], "server request_fields"
        )
        result_fields = _validate_selectors(
            data["result_fields"], "server result_fields"
        )
        hook_data = data.get("hook", {})
        if not isinstance(hook_data, dict):
            raise BridgeError("server path profile hook must be an object")
        _require_keys(
            hook_data,
            required=set(),
            optional={"request_fields", "result_fields", "tool_calls"},
            context="server path profile hook",
        )
        hook_request_fields = _validate_selectors(
            hook_data.get("request_fields", []), "hook request_fields"
        )
        hook_result_fields = _validate_selectors(
            hook_data.get("result_fields", []), "hook result_fields"
        )
        call_data = hook_data.get("tool_calls", [])
        if not isinstance(call_data, list):
            raise BridgeError("hook tool_calls must be an array")
        tool_calls = tuple(
            _load_hook_tool_call(item, index) for index, item in enumerate(call_data)
        )
        command_bridge = _load_command_bridge_profile(data.get("command_bridge"))
        return cls(
            server=server,
            request_fields=request_fields,
            result_fields=result_fields,
            hook=HookFields(
                request_fields=hook_request_fields,
                result_fields=hook_result_fields,
                tool_calls=tool_calls,
            ),
            command_bridge=command_bridge,
        )


@dataclass(frozen=True)
class TransportProfile:
    """One selected Windows execution profile."""

    name: str
    kind: str
    path_map: PathMap
    wsl_distro: str | None = None
    ssh_host: str | None = None
    ssh_port: int | None = None
    ssh_identity_file: str | None = None


@dataclass(frozen=True)
class ServerConfiguration:
    """Per-server bridge enablement and profile location."""

    enabled: bool
    hook_bridge: bool
    path_profile: Path


class BridgeConfiguration:
    """Validated bridge profiles and per-server settings."""

    def __init__(self, path: Path) -> None:
        data = _load_json_object(path, "bridge configuration")
        _require_keys(
            data,
            required={"schema_version", "profiles", "servers"},
            optional=set(),
            context="bridge configuration",
        )
        _require_schema_version(data["schema_version"], "bridge configuration")
        profile_data = data["profiles"]
        server_data = data["servers"]
        if not isinstance(profile_data, dict) or not profile_data:
            raise BridgeError("bridge configuration profiles must be a nonempty object")
        if not isinstance(server_data, dict):
            raise BridgeError("bridge configuration servers must be an object")
        self.path = path.resolve()
        self.profiles = {
            name: _load_transport_profile(name, value)
            for name, value in profile_data.items()
        }
        self.servers = {
            name: _load_server_configuration(name, value, self.path.parent)
            for name, value in server_data.items()
        }

    def server_profile(self, name: str, require_hook: bool = False) -> ServerPathProfile:
        """Return one enabled server profile."""

        configuration = self.servers.get(name)
        if configuration is None:
            raise BridgeError(f"server {name!r} has no bridge configuration")
        if not configuration.enabled:
            raise BridgeError(f"server {name!r} bridge is disabled")
        if require_hook and not configuration.hook_bridge:
            raise BridgeError(f"server {name!r} hook bridge is disabled")
        profile = ServerPathProfile.load(configuration.path_profile)
        if profile.server != name:
            raise BridgeError(
                f"server profile declares {profile.server!r}, expected {name!r}"
            )
        return profile

    def resolve_runtime_profile(
        self,
        explicit_name: str | None,
        environment: Mapping[str, str] | None = None,
    ) -> TransportProfile:
        """Resolve an explicit profile or the conservative WSL fallback."""

        values = os.environ if environment is None else environment
        selected_name = explicit_name or values.get(PROFILE_ENVIRONMENT_KEY)
        if selected_name is None and values.get("WSL") == "1":
            wsl_profiles = [
                profile for profile in self.profiles.values() if profile.kind == "wsl"
            ]
            if len(wsl_profiles) != 1:
                raise BridgeError(
                    "WSL fallback requires exactly one configured WSL profile"
                )
            selected_name = wsl_profiles[0].name
        if selected_name is None:
            raise BridgeError(
                f"select a bridge profile explicitly with --profile or {PROFILE_ENVIRONMENT_KEY}"
            )
        profile = self.profiles.get(selected_name)
        if profile is None:
            raise BridgeError(f"bridge profile {selected_name!r} does not exist")
        if profile.kind == "wsl":
            actual_distro = values.get("WSL_DISTRO_NAME")
            if actual_distro is None:
                interop = "set" if values.get("WSL_INTEROP") else "not set"
                raise BridgeError(
                    "WSL_DISTRO_NAME is required for a WSL profile "
                    f"(WSL_INTEROP is {interop})"
                )
            if actual_distro.casefold() != str(profile.wsl_distro).casefold():
                raise BridgeError(
                    f"WSL distribution {actual_distro!r} does not match "
                    f"configured distribution {profile.wsl_distro!r}"
                )
        return profile


def translate_mcp_request(
    message: Any, profile: ServerPathProfile, path_map: PathMap
) -> Any:
    """Translate declared request fields in one JSON-RPC message or batch."""

    if isinstance(message, list):
        return [translate_mcp_request(item, profile, path_map) for item in message]
    if not isinstance(message, dict) or message.get("method") != "tools/call":
        return message
    params = message.get("params")
    if not isinstance(params, dict):
        return message
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        return message
    params["arguments"] = _apply_selectors(
        arguments, profile.request_fields, path_map.to_linux
    )
    return message


def translate_mcp_response(
    message: Any, profile: ServerPathProfile, path_map: PathMap
) -> Any:
    """Translate declared structured result fields in one response or batch."""

    if isinstance(message, list):
        return [translate_mcp_response(item, profile, path_map) for item in message]
    if not isinstance(message, dict) or "result" not in message:
        return message
    message["result"] = _apply_selectors(
        message["result"], profile.result_fields, path_map.to_windows
    )
    return message


def translate_hook_request(
    message: Any, profile: ServerPathProfile, path_map: PathMap
) -> Any:
    """Translate declared fields in any configured JSON hook envelope."""

    message = _apply_selectors(
        message, profile.hook.request_fields, path_map.to_linux
    )
    for call in profile.hook.tool_calls:
        tool_name = _resolve_pointer(message, call.name_field)
        arguments = _resolve_pointer(message, call.arguments_field)
        if not isinstance(tool_name, str) or not isinstance(arguments, dict):
            continue
        if not any(
            fnmatch.fnmatchcase(tool_name, pattern)
            for pattern in call.tool_name_patterns
        ):
            continue
        translated = _apply_selectors(
            arguments, profile.request_fields, path_map.to_linux
        )
        message = _replace_pointer(message, call.arguments_field, translated)
    return message


def translate_hook_response(
    message: Any, profile: ServerPathProfile, path_map: PathMap
) -> Any:
    """Translate declared structured fields in a hook result."""

    return _apply_selectors(message, profile.hook.result_fields, path_map.to_windows)


def run_mcp_proxy(
    command: Sequence[str],
    profile: ServerPathProfile,
    transport: TransportProfile,
    configuration_path: Path,
) -> None:
    """Run a newline-delimited MCP proxy until both standard streams close."""

    child_environment = os.environ.copy()
    if profile.command_bridge is not None:
        bridge_configuration = {
            "protocol_version": profile.command_bridge.protocol_version,
            "command": [
                sys.executable,
                str(Path(__file__).resolve()),
                "command",
                "--config",
                str(configuration_path),
                "--profile",
                transport.name,
            ],
        }
        child_environment[profile.command_bridge.environment_variable] = json.dumps(
            bridge_configuration,
            separators=(",", ":"),
        )
    process = _spawn(command, child_environment)
    if process.stdin is None or process.stdout is None:
        raise BridgeError("could not open proxied MCP standard streams")
    request_errors: list[Exception] = []
    request_stop = threading.Event()

    def forward_requests() -> None:
        try:
            _forward_json_input(
                sys.stdin.buffer,
                process.stdin,
                lambda value: translate_mcp_request(
                    value, profile, transport.path_map
                ),
                request_stop,
            )
        except Exception as error:  # Forward the original worker failure.
            request_errors.append(error)
            _terminate(process)
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    request_thread = threading.Thread(
        target=forward_requests,
        name="mcp-path-requests",
    )
    request_thread.start()
    response_error: Exception | None = None
    try:
        _forward_json_lines(
            process.stdout,
            sys.stdout.buffer,
            lambda value: translate_mcp_response(
                value, profile, transport.path_map
            ),
        )
    except Exception as error:
        response_error = error
        _terminate(process)
    finally:
        process.stdout.close()
    return_code = process.wait()
    request_stop.set()
    request_thread.join(REQUEST_THREAD_SHUTDOWN_SECONDS)
    if request_thread.is_alive():
        raise BridgeError("could not stop the MCP request reader")
    if request_errors:
        raise BridgeError(f"could not forward MCP request: {request_errors[0]}")
    if response_error is not None:
        raise BridgeError(f"could not forward MCP response: {response_error}")
    if return_code != 0:
        raise ChildExit(return_code)


def run_hook_proxy(
    command: Sequence[str], profile: ServerPathProfile, path_map: PathMap
) -> None:
    """Run one bounded JSON hook request and response."""

    request_bytes = sys.stdin.buffer.read(MAXIMUM_MESSAGE_BYTES + 1)
    if len(request_bytes) > MAXIMUM_MESSAGE_BYTES:
        raise BridgeError(
            f"hook input exceeded the {MAXIMUM_MESSAGE_BYTES}-byte boundary"
        )
    request = _decode_json(request_bytes, "hook request")
    translated_request = translate_hook_request(request, profile, path_map)
    encoded_request = _encode_json(translated_request)
    process = subprocess.run(
        list(command),
        input=encoded_request,
        stdout=subprocess.PIPE,
        stderr=None,
        check=False,
    )
    if process.returncode != 0:
        if process.stdout:
            sys.stdout.buffer.write(process.stdout)
            sys.stdout.buffer.flush()
        raise ChildExit(process.returncode)
    if not process.stdout:
        return
    if len(process.stdout) > MAXIMUM_MESSAGE_BYTES:
        raise BridgeError(
            f"hook output exceeded the {MAXIMUM_MESSAGE_BYTES}-byte boundary"
        )
    response = _decode_json(process.stdout, "hook response")
    translated_response = translate_hook_response(response, profile, path_map)
    sys.stdout.buffer.write(_encode_json(translated_response))
    sys.stdout.buffer.flush()


def run_command_proxy(
    command: Sequence[str],
    transport: TransportProfile,
    environment_mode: str,
    environment_entries: Sequence[Sequence[str]],
    removed_environment: Sequence[str],
) -> None:
    """Run one child command on the host that owns its working directory."""

    if not command:
        raise BridgeError("a bridged host command is required after --")
    working_directory = os.getcwd()
    execution_host = transport.path_map.execution_host(working_directory)
    if execution_host == "linux":
        target_environment = {} if environment_mode == "replace" else os.environ.copy()
        for variable_name in removed_environment:
            target_environment.pop(variable_name, None)
        for variable_name, variable_value in environment_entries:
            target_environment[variable_name] = variable_value
        os.execvpe(command[0], list(command), target_environment)
    if transport.kind != "wsl":
        raise BridgeError(
            "Windows command execution requires a WSL transport profile"
        )
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        raise BridgeError("powershell.exe is not available through WSL interop")

    translated_entries = [
        {
            "name": name,
            "value": _translate_command_path(value, transport.path_map),
        }
        for name, value in environment_entries
    ]
    payload = {
        "working_directory": transport.path_map.to_windows(working_directory),
        "executable": _translate_command_path(command[0], transport.path_map),
        "arguments": [
            _translate_command_path(argument, transport.path_map)
            for argument in command[1:]
        ],
        "environment_mode": environment_mode,
        "environment": translated_entries,
        "remove_environment": list(removed_environment),
    }
    encoded_payload = base64.b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    windows_environment = os.environ.copy()
    windows_environment["MCP_TOOLBOX_COMMAND_REQUEST"] = encoded_payload
    inherited_wslenv = windows_environment.get("WSLENV", "")
    windows_environment["WSLENV"] = ":".join(
        item
        for item in [inherited_wslenv, "MCP_TOOLBOX_COMMAND_REQUEST"]
        if item
    )
    encoded_script = base64.b64encode(
        WINDOWS_COMMAND_SCRIPT.encode("utf-16-le")
    ).decode("ascii")
    os.execve(
        powershell,
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-OutputFormat",
            "Text",
            "-EncodedCommand",
            encoded_script,
        ],
        windows_environment,
    )


def _translate_command_path(value: str, path_map: PathMap) -> str:
    if value.startswith("/"):
        try:
            return path_map.to_windows(value)
        except BridgeError:
            return value
    return value


def build_client_command(
    configuration: BridgeConfiguration,
    profile_name: str,
    mode: str,
    server_name: str,
    bridge_command: str,
    server_command: Sequence[str],
) -> dict[str, Any]:
    """Build a Windows launch command for any MCP or hook-capable client."""

    profile = configuration.profiles.get(profile_name)
    if profile is None:
        raise BridgeError(f"bridge profile {profile_name!r} does not exist")
    configuration.server_profile(server_name, require_hook=mode == "hook")
    inner_arguments = [
        bridge_command,
        mode,
        "--config",
        str(configuration.path),
        "--profile",
        profile_name,
        "--server",
        server_name,
        "--",
        *server_command,
    ]
    if profile.kind == "wsl":
        command = "wsl.exe"
        arguments = ["-d", str(profile.wsl_distro), "--", *inner_arguments]
    else:
        command = "ssh"
        arguments = []
        if profile.ssh_port is not None:
            arguments.extend(["-p", str(profile.ssh_port)])
        if profile.ssh_identity_file is not None:
            arguments.extend(["-i", profile.ssh_identity_file])
        arguments.extend(
            [str(profile.ssh_host), shlex.join(["exec", *inner_arguments])]
        )
    return {
        "profile": profile_name,
        "transport": profile.kind,
        "mode": mode,
        "command": command,
        "args": arguments,
    }


def _forward_json_lines(
    source: BinaryIO, destination: BinaryIO, transform: Callable[[Any], Any]
) -> None:
    while True:
        line = source.readline(MAXIMUM_MESSAGE_BYTES + 1)
        if not line:
            break
        if len(line) > MAXIMUM_MESSAGE_BYTES:
            raise BridgeError(
                f"MCP message exceeded the {MAXIMUM_MESSAGE_BYTES}-byte boundary"
            )
        if not line.strip():
            continue
        destination.write(_encode_json(transform(_decode_json(line, "MCP message"))))
        destination.flush()


def _forward_json_input(
    source: BinaryIO,
    destination: BinaryIO,
    transform: Callable[[Any], Any],
    stop_event: threading.Event,
) -> None:
    selector = selectors.DefaultSelector()
    selector.register(source.fileno(), selectors.EVENT_READ)
    buffered = bytearray()
    try:
        while not stop_event.is_set():
            if not selector.select(timeout=0.1):
                continue
            chunk = os.read(source.fileno(), 64 * 1024)
            if not chunk:
                if buffered.strip():
                    _forward_encoded_json(bytes(buffered), destination, transform)
                return
            buffered.extend(chunk)
            while True:
                newline_index = buffered.find(b"\n")
                if newline_index < 0:
                    break
                encoded = bytes(buffered[: newline_index + 1])
                del buffered[: newline_index + 1]
                if encoded.strip():
                    _forward_encoded_json(encoded, destination, transform)
            if len(buffered) > MAXIMUM_MESSAGE_BYTES:
                raise BridgeError(
                    f"MCP message exceeded the {MAXIMUM_MESSAGE_BYTES}-byte boundary"
                )
    finally:
        selector.close()


def _forward_encoded_json(
    encoded: bytes, destination: BinaryIO, transform: Callable[[Any], Any]
) -> None:
    if len(encoded) > MAXIMUM_MESSAGE_BYTES:
        raise BridgeError(
            f"MCP message exceeded the {MAXIMUM_MESSAGE_BYTES}-byte boundary"
        )
    destination.write(_encode_json(transform(_decode_json(encoded, "MCP message"))))
    destination.flush()


def _spawn(
    command: Sequence[str], environment: Mapping[str, str] | None = None
) -> subprocess.Popen[bytes]:
    if not command:
        raise BridgeError("a proxied command is required after --")
    return subprocess.Popen(
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        env=environment,
    )


def _terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        process.terminate()
    except ProcessLookupError:
        pass


def _decode_json(encoded: bytes, context: str) -> Any:
    try:
        return json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BridgeError(f"{context} is not valid UTF-8 JSON: {error}") from error


def _encode_json(value: Any) -> bytes:
    try:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        ) + b"\n"
    except (TypeError, ValueError) as error:
        raise BridgeError(f"could not encode translated JSON: {error}") from error


def _apply_selectors(
    value: Any, selectors: Sequence[str], conversion: Callable[[str], str]
) -> Any:
    transformed = value
    for selector in selectors:
        transformed = _transform_selected(
            transformed, _selector_segments(selector), conversion, selector
        )
    return transformed


def _transform_selected(
    value: Any,
    segments: Sequence[str],
    conversion: Callable[[str], str],
    selector: str,
) -> Any:
    if not segments:
        if value is None:
            return value
        if not isinstance(value, str):
            raise BridgeError(f"declared path field {selector!r} must be a string or null")
        return conversion(value)
    head, *tail = segments
    if head == "*":
        if isinstance(value, dict):
            return {
                key: _transform_selected(item, tail, conversion, selector)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                _transform_selected(item, tail, conversion, selector) for item in value
            ]
        return value
    if isinstance(value, dict) and head in value:
        value[head] = _transform_selected(value[head], tail, conversion, selector)
    elif isinstance(value, list) and head.isdigit():
        index = int(head)
        if index < len(value):
            value[index] = _transform_selected(value[index], tail, conversion, selector)
    return value


def _resolve_pointer(value: Any, selector: str) -> Any | None:
    current = value
    for segment in _selector_segments(selector):
        if segment == "*":
            raise BridgeError(f"envelope selector {selector!r} cannot contain a wildcard")
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            return None
    return current


def _replace_pointer(value: Any, selector: str, replacement: Any) -> Any:
    segments = _selector_segments(selector)
    if not segments:
        return replacement
    current = value
    for segment in segments[:-1]:
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            return value
    final = segments[-1]
    if isinstance(current, dict) and final in current:
        current[final] = replacement
    elif isinstance(current, list) and final.isdigit() and int(final) < len(current):
        current[int(final)] = replacement
    return value


def _selector_segments(selector: str) -> tuple[str, ...]:
    return tuple(
        segment.replace("~1", "/").replace("~0", "~")
        for segment in selector[1:].split("/")
    )


def _load_hook_tool_call(value: Any, index: int) -> HookToolCall:
    if not isinstance(value, dict):
        raise BridgeError(f"hook tool_calls item {index} must be an object")
    _require_keys(
        value,
        required={"name_field", "arguments_field", "tool_name_patterns"},
        optional=set(),
        context=f"hook tool_calls item {index}",
    )
    name_field = _validate_selectors(
        [value["name_field"]], f"hook tool_calls item {index} name_field"
    )[0]
    arguments_field = _validate_selectors(
        [value["arguments_field"]],
        f"hook tool_calls item {index} arguments_field",
    )[0]
    if "*" in _selector_segments(name_field) or "*" in _selector_segments(
        arguments_field
    ):
        raise BridgeError("hook tool-call envelope selectors cannot contain wildcards")
    patterns_value = value["tool_name_patterns"]
    if not isinstance(patterns_value, list) or not patterns_value:
        raise BridgeError(
            f"hook tool_calls item {index} tool_name_patterns must be a nonempty array"
        )
    patterns = tuple(
        _require_string(pattern, f"hook tool_calls item {index} pattern")
        for pattern in patterns_value
    )
    return HookToolCall(name_field, arguments_field, patterns)


def _load_command_bridge_profile(value: Any) -> CommandBridgeProfile | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise BridgeError("server path profile command_bridge must be an object")
    _require_keys(
        value,
        required={"environment_variable", "protocol_version"},
        optional=set(),
        context="server path profile command_bridge",
    )
    environment_variable = _require_string(
        value["environment_variable"],
        "server path profile command_bridge environment_variable",
    )
    if "=" in environment_variable:
        raise BridgeError("command bridge environment_variable must not contain '='")
    protocol_version = value["protocol_version"]
    if isinstance(protocol_version, bool) or not isinstance(protocol_version, int):
        raise BridgeError("command bridge protocol_version must be an integer")
    if protocol_version < 1:
        raise BridgeError("command bridge protocol_version must be positive")
    return CommandBridgeProfile(environment_variable, protocol_version)


def _validate_selectors(value: Any, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise BridgeError(f"{context} must be an array")
    selectors: list[str] = []
    for item in value:
        selector = _require_string(item, context)
        if not selector.startswith("/") or selector == "/":
            raise BridgeError(f"{context} selector {selector!r} must be a JSON pointer")
        if selector in selectors:
            raise BridgeError(f"{context} duplicates selector {selector!r}")
        selectors.append(selector)
    return tuple(selectors)


def _load_transport_profile(name: Any, value: Any) -> TransportProfile:
    profile_name = _require_string(name, "bridge profile name")
    if not isinstance(value, dict):
        raise BridgeError(f"bridge profile {profile_name!r} must be an object")
    kind = value.get("kind")
    if kind == "wsl":
        _require_keys(
            value,
            required={"kind", "wsl_distro", "mappings"},
            optional=set(),
            context=f"bridge profile {profile_name!r}",
        )
        return TransportProfile(
            name=profile_name,
            kind="wsl",
            path_map=PathMap(_require_mapping_array(value["mappings"], profile_name)),
            wsl_distro=_require_string(
                value["wsl_distro"], f"bridge profile {profile_name!r} wsl_distro"
            ),
        )
    if kind == "windows-remote":
        _require_keys(
            value,
            required={"kind", "ssh", "mappings"},
            optional=set(),
            context=f"bridge profile {profile_name!r}",
        )
        ssh = value["ssh"]
        if not isinstance(ssh, dict):
            raise BridgeError(f"bridge profile {profile_name!r} ssh must be an object")
        _require_keys(
            ssh,
            required={"host"},
            optional={"port", "identity_file"},
            context=f"bridge profile {profile_name!r} ssh",
        )
        host = _require_string(ssh["host"], f"bridge profile {profile_name!r} ssh host")
        if host.startswith("-"):
            raise BridgeError("SSH host must not begin with a hyphen")
        port = ssh.get("port")
        if port is not None and (
            isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535
        ):
            raise BridgeError("SSH port must be an integer from 1 through 65535")
        identity = ssh.get("identity_file")
        if identity is not None:
            identity = _require_string(
                identity, f"bridge profile {profile_name!r} ssh identity_file"
            )
        return TransportProfile(
            name=profile_name,
            kind="windows-remote",
            path_map=PathMap(_require_mapping_array(value["mappings"], profile_name)),
            ssh_host=host,
            ssh_port=port,
            ssh_identity_file=identity,
        )
    raise BridgeError(
        f"bridge profile {profile_name!r} kind must be 'wsl' or 'windows-remote'"
    )


def _load_server_configuration(
    name: Any, value: Any, configuration_directory: Path
) -> ServerConfiguration:
    server_name = _require_string(name, "bridge server name")
    if not isinstance(value, dict):
        raise BridgeError(f"bridge server {server_name!r} must be an object")
    _require_keys(
        value,
        required={"enabled", "hook_bridge", "path_profile"},
        optional=set(),
        context=f"bridge server {server_name!r}",
    )
    enabled = _require_boolean(value["enabled"], f"bridge server {server_name!r} enabled")
    hook_bridge = _require_boolean(
        value["hook_bridge"], f"bridge server {server_name!r} hook_bridge"
    )
    profile_value = Path(
        _require_string(
            value["path_profile"], f"bridge server {server_name!r} path_profile"
        )
    )
    if not profile_value.is_absolute():
        profile_value = configuration_directory / profile_value
    return ServerConfiguration(enabled, hook_bridge, profile_value.resolve())


def _require_mapping_array(value: Any, profile_name: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise BridgeError(f"bridge profile {profile_name!r} mappings must be an array")
    if not all(isinstance(item, dict) for item in value):
        raise BridgeError(f"bridge profile {profile_name!r} mappings must contain objects")
    return value


def _windows_path_kind(value: str) -> str:
    if value.startswith("/") and not value.startswith("//"):
        return "linux"
    normalized = value.replace("/", "\\")
    if normalized.startswith("\\\\.\\") or normalized.startswith("\\\\?\\"):
        return "device"
    if re.match(r"^[A-Za-z]:\\", normalized):
        return "drive"
    if re.match(r"^[A-Za-z]:", normalized):
        return "drive-relative"
    if normalized.startswith("\\\\"):
        return "unc"
    if normalized.startswith("\\"):
        return "rooted-relative"
    return "relative"


def _normalize_windows_absolute(value: str) -> str:
    normalized = ntpath.normpath(value.replace("/", "\\"))
    kind = _windows_path_kind(normalized)
    if kind == "drive":
        drive = normalized[:2]
        components = [part for part in normalized[3:].split("\\") if part]
        if not components:
            return f"{drive}\\"
        return f"{drive}\\" + "\\".join(components)
    if kind == "unc":
        components = [part for part in normalized[2:].split("\\") if part]
        if len(components) < 2:
            raise BridgeError(f"UNC path must include a server and share: {value!r}")
        return "\\\\" + "\\".join(components)
    raise BridgeError(f"path is not an absolute Windows path: {value!r}")


def _normalize_windows_relative(value: str) -> str:
    normalized = ntpath.normpath(value.replace("/", "\\"))
    return normalized.replace("\\", "/")


def _normalize_linux_absolute(value: str) -> str:
    if not value.startswith("/"):
        raise BridgeError(f"Linux prefix must be absolute: {value!r}")
    return posixpath.normpath(value)


def _windows_prefix_matches(path: str, prefix: str) -> bool:
    folded_path = path.casefold()
    folded_prefix = prefix.casefold()
    if prefix.endswith("\\"):
        return folded_path.startswith(folded_prefix)
    return folded_path == folded_prefix or folded_path.startswith(f"{folded_prefix}\\")


def _linux_prefix_matches(path: str, prefix: str) -> bool:
    if prefix == "/":
        return path.startswith("/")
    return path == prefix or path.startswith(f"{prefix}/")


def _load_json_object(path: Path, context: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BridgeError(f"could not load {context} {path}: {error}") from error
    if not isinstance(value, dict):
        raise BridgeError(f"{context} {path} must contain a JSON object")
    return value


def _require_schema_version(value: Any, context: str) -> None:
    if value != SUPPORTED_SCHEMA_VERSION:
        raise BridgeError(
            f"{context} schema_version must equal {SUPPORTED_SCHEMA_VERSION}"
        )


def _require_keys(
    value: Mapping[str, Any],
    required: set[str],
    optional: set[str],
    context: str,
) -> None:
    missing = required.difference(value)
    unknown = set(value).difference(required | optional)
    if missing:
        raise BridgeError(f"{context} is missing keys: {', '.join(sorted(missing))}")
    if unknown:
        raise BridgeError(f"{context} has unknown keys: {', '.join(sorted(unknown))}")


def _require_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BridgeError(f"{context} must be a nonblank string")
    return value


def _require_boolean(value: Any, context: str) -> bool:
    if not isinstance(value, bool):
        raise BridgeError(f"{context} must be a boolean")
    return value


def _command_arguments(arguments: argparse.Namespace) -> list[str]:
    command = list(arguments.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise BridgeError("a proxied command is required after --")
    return command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Translate declared path fields at MCP and JSON hook boundaries."
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for boundary_mode in ("mcp-stdio", "hook"):
        proxy = subparsers.add_parser(boundary_mode)
        proxy.add_argument("--config", required=True, type=Path)
        proxy.add_argument("--profile")
        proxy.add_argument("--server", required=True)
        proxy.add_argument("command", nargs=argparse.REMAINDER)
        proxy.set_defaults(boundary_mode=boundary_mode)
    client = subparsers.add_parser("client-command")
    client.add_argument("--config", required=True, type=Path)
    client.add_argument("--profile", required=True)
    client.add_argument("--server", required=True)
    client.add_argument(
        "--mode",
        dest="boundary_mode",
        required=True,
        choices=("mcp-stdio", "hook"),
    )
    client.add_argument("--bridge-command", required=True)
    client.add_argument("command", nargs=argparse.REMAINDER)
    command_proxy = subparsers.add_parser("command")
    command_proxy.add_argument("--config", required=True, type=Path)
    command_proxy.add_argument("--profile")
    command_proxy.add_argument(
        "--environment-mode",
        required=True,
        choices=("overlay", "replace"),
    )
    command_proxy.add_argument(
        "--environment",
        action="append",
        default=[],
        nargs=2,
        metavar=("NAME", "VALUE"),
    )
    command_proxy.add_argument(
        "--remove-environment",
        action="append",
        default=[],
        metavar="NAME",
    )
    command_proxy.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Run one bridge mode."""

    parsed = _parser().parse_args(arguments)
    try:
        configuration = BridgeConfiguration(parsed.config)
        command = _command_arguments(parsed)
        if parsed.operation == "client-command":
            rendered = build_client_command(
                configuration,
                parsed.profile,
                parsed.boundary_mode,
                parsed.server,
                parsed.bridge_command,
                command,
            )
            json.dump(rendered, sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 0
        transport = configuration.resolve_runtime_profile(parsed.profile)
        if parsed.operation == "command":
            run_command_proxy(
                command,
                transport,
                parsed.environment_mode,
                parsed.environment,
                parsed.remove_environment,
            )
            return 0
        server_profile = configuration.server_profile(
            parsed.server, require_hook=parsed.boundary_mode == "hook"
        )
        if parsed.boundary_mode == "mcp-stdio":
            run_mcp_proxy(
                command,
                server_profile,
                transport,
                configuration.path,
            )
        else:
            run_hook_proxy(command, server_profile, transport.path_map)
        return 0
    except ChildExit as error:
        print(f"mcp-path-bridge: {error}", file=sys.stderr)
        return error.return_code if 0 < error.return_code < 126 else 1
    except (BridgeError, OSError) as error:
        print(f"mcp-path-bridge: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

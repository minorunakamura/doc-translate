"""Authentication through the public CLI and external helper process boundary."""

import json
import os
import pty
import selectors
import shutil
import signal
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

pytest_plugins = ("test_ai_runtime_cli",)
ROOT = Path(__file__).resolve().parents[1]


def test_logout_removes_only_selected_provider_and_returns_non_secret_json(runtime_cli):
    run, _, _, home = runtime_cli
    assert run("setup-ai", "--package-manager", "npm").returncode == 0
    path = home / ".config/doc-translate/credentials.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "anthropic": {"type": "api_key", "key": "ARTIFICIAL_KEY"},
                "openai": {"type": "api_key", "key": "KEEP"},
            }
        )
    )
    result = run(
        "logout",
        "--provider",
        "anthropic",
        extra_env={"XDG_CONFIG_HOME": str(home / ".config")},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"operation": "logout", "provider": "anthropic"}
    assert json.loads(path.read_text()) == {
        "openai": {"type": "api_key", "key": "KEEP"}
    }
    assert "ARTIFICIAL" not in result.stdout + result.stderr


def environment(binaries, home, **extra):
    return {
        **os.environ,
        "HOME": str(home),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "PATH": str(binaries),
        **extra,
    }


def fake_node(binaries):
    node = binaries / "node"
    node.unlink()
    node.write_text(
        f"#!{sys.executable}\n"
        + (ROOT / "tests/fixtures/auth_helper_process.py").read_text()
    )
    node.chmod(0o755)


def terminal_login(env, *, next_reader=False):
    master, slave = pty.openpty()

    executable = shutil.which("doc-translate")
    assert executable is not None
    command = [
        executable,
        "login",
        "--provider",
        "anthropic",
        "--method",
        "api_key",
    ]
    if next_reader:
        # Keep the PTY session alive after the CLI exits, as an interactive shell does.
        command = [
            sys.executable,
            "-c",
            (
                "import os, signal, subprocess, sys\n"
                "child = subprocess.Popen(sys.argv[1:])\n"
                "signal.signal(signal.SIGTERM, lambda *_: child.terminate())\n"
                "assert child.wait() == 1\n"
                "print('next reader ready', flush=True)\n"
                "assert os.read(0, 4096) == b'\\n'\n"
                "print('input queue clean', flush=True)\n"
            ),
            *command,
        ]
    child = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "tests/fixtures/auth_terminal_process.py"),
            *command,
        ],
        env=env,
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    return child, master, slave


def read_terminal(master, expected):
    data = b""
    with selectors.DefaultSelector() as selector:
        selector.register(master, selectors.EVENT_READ)
        deadline = time.monotonic() + 5
        while expected not in data and time.monotonic() < deadline:
            if selector.select(timeout=0.1):
                data += os.read(master, 65536)
    assert expected in data, data
    return data


def finish_terminal(child, master, slave, *, success=True):
    # macOS waits for unread PTY output when its session leader exits.
    stream = child.stdout if success else child.stderr
    first = stream.readline()
    assert first
    assert termios.tcgetattr(slave)[3] & termios.ECHO
    terminal = read_terminal(master, b"\n")
    stdout, stderr = child.communicate(timeout=5)
    return (
        (first + stdout, stderr, terminal)
        if success
        else (stdout, first + stderr, terminal)
    )


def test_login_artificial_builtin_key_is_non_echo_and_stdout_is_only_non_secret_json(
    runtime_cli,
):
    run, binaries, _, home = runtime_cli
    assert run("setup-ai", "--package-manager", "npm").returncode == 0
    child, master, slave = terminal_login(environment(binaries, home))
    try:
        terminal = read_terminal(master, b"Enter Anthropic API key")
        assert not termios.tcgetattr(slave)[3] & termios.ECHO
        os.write(master, b"ARTIFICIAL_KEY\n")
        stdout, stderr, ending = finish_terminal(child, master, slave)
        assert child.returncode == 0, stderr
        assert json.loads(stdout) == {
            "operation": "login",
            "provider": "anthropic",
            "method": "api_key",
        }
        assert "ARTIFICIAL_KEY" not in (stdout + stderr + terminal + ending).decode()
        path = home / ".config/doc-translate/credentials.json"
        assert json.loads(path.read_text()) == {
            "anthropic": {"type": "api_key", "key": "ARTIFICIAL_KEY"}
        }
        assert not path.with_name("installation-id").exists()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize(
    "arguments",
    [
        ["login", "--provider", "unknown", "--method", "oauth"],
        ["login", "--provider", "google", "--method", "oauth"],
        ["login", "--provider", "openai", "--method", "other"],
        ["logout", "--provider", "unknown"],
    ],
)
def test_unknown_provider_or_unsupported_login_method_is_input_error(
    runtime_cli, arguments
):
    run, _, _, _ = runtime_cli
    assert run("setup-ai", "--package-manager", "npm").returncode == 0
    result = run(*arguments)
    assert result.returncode == 2, result.stderr


def test_piped_secret_input_is_refused_without_echo_fallback(runtime_cli):
    run, binaries, _, home = runtime_cli
    assert run("setup-ai", "--package-manager", "npm").returncode == 0
    executable = shutil.which("doc-translate")
    assert executable is not None
    result = subprocess.run(
        [
            executable,
            "login",
            "--provider",
            "anthropic",
            "--method",
            "api_key",
        ],
        env=environment(binaries, home),
        input=b"ARTIFICIAL_PIPE_SECRET\n",
        capture_output=True,
        start_new_session=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 1
    assert b"requires a terminal" in result.stderr
    assert b"ARTIFICIAL_PIPE_SECRET" not in result.stdout + result.stderr
    assert not (home / ".config/doc-translate/credentials.json").exists()


@pytest.mark.parametrize(
    "mode", ["error", "exit", "incomplete", "wrong-id", "secret-result"]
)
def test_auth_process_failures_are_not_success_or_secret_error_echo(runtime_cli, mode):
    run, binaries, _, _home = runtime_cli
    assert run("setup-ai", "--package-manager", "npm").returncode == 0
    fake_node(binaries)
    result = run(
        "login",
        "--provider",
        "anthropic",
        "--method",
        "api_key",
        extra_env={"AUTH_PROCESS_MODE": mode},
    )
    assert result.returncode == 1, result.stderr
    assert result.stdout == ""
    assert "ARTIFICIAL_SECRET" not in result.stderr


def test_callback_cancels_pending_terminal_prompt_without_hanging(runtime_cli):
    run, binaries, _, home = runtime_cli
    assert run("setup-ai", "--package-manager", "npm").returncode == 0
    fake_node(binaries)
    child, master, slave = terminal_login(
        environment(binaries, home, AUTH_PROCESS_MODE="callback")
    )
    try:
        terminal = read_terminal(master, b"Artificial prompt")
        stdout, stderr, _ = finish_terminal(child, master, slave)
        assert child.returncode == 0, stderr
        assert json.loads(stdout)["operation"] == "login"
        assert b"ARTIFICIAL_DEVICE_CODE" in terminal
        assert b"ARTIFICIAL_DEVICE_CODE" not in stdout + stderr
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize("cancel", [signal.SIGINT, signal.SIGTERM, "eof"])
def test_cancellation_reaps_child_after_storage_grace_and_restores_echo(
    runtime_cli, cancel
):
    run, binaries, _, home = runtime_cli
    assert run("setup-ai", "--package-manager", "npm").returncode == 0
    fake_node(binaries)
    marker = home / "child-pid"
    child, master, slave = terminal_login(
        environment(
            binaries, home, AUTH_PROCESS_MODE="cancel", AUTH_PROCESS_MARKER=str(marker)
        )
    )
    try:
        read_terminal(master, b"Artificial prompt")
        if cancel == "eof":
            os.write(master, b"\x04")
        else:
            child.send_signal(cancel)
        stdout, stderr, _ = finish_terminal(child, master, slave, success=False)
        assert child.returncode == 1, stderr
        assert stdout == b""
        assert marker.with_suffix(".drained").read_text() == "saved"
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        os.close(master)
        os.close(slave)


def test_cancelled_login_reports_storage_failure_after_helper_drain(runtime_cli):
    run, binaries, _, home = runtime_cli
    assert run("setup-ai", "--package-manager", "npm").returncode == 0
    fake_node(binaries)
    marker = home / "child-pid"
    child, master, slave = terminal_login(
        environment(
            binaries,
            home,
            AUTH_PROCESS_MODE="cancel-storage-failure",
            AUTH_PROCESS_MARKER=str(marker),
        )
    )
    try:
        read_terminal(master, b"Artificial prompt")
        child.send_signal(signal.SIGTERM)
        stdout, stderr, ending = finish_terminal(child, master, slave, success=False)
        assert child.returncode == 1, stderr
        assert stdout == b""
        assert b"Credential storage failed" in stderr
        assert b"re-login" in stderr
        assert b"ARTIFICIAL_SECRET" not in stdout + stderr + ending
        assert marker.with_suffix(".drained").read_text() == "saved"
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        os.close(master)
        os.close(slave)


def test_sigterm_discards_unsubmitted_secret_before_the_next_terminal_reader(
    runtime_cli,
):
    run, binaries, _, home = runtime_cli
    assert run("setup-ai", "--package-manager", "npm").returncode == 0
    fake_node(binaries)
    child, master, slave = terminal_login(
        environment(
            binaries,
            home,
            AUTH_PROCESS_MODE="cancel",
            AUTH_PROCESS_MARKER=str(home / "child-pid"),
        ),
        next_reader=True,
    )
    try:
        read_terminal(master, b"Artificial prompt")
        os.write(master, b"ARTIFICIAL_UNSUBMITTED_SECRET")
        child.send_signal(signal.SIGTERM)
        assert child.stderr.readline() == b"doc-translate: Authentication cancelled.\n"
        assert child.stdout.readline() == b"next reader ready\n"
        assert termios.tcgetattr(slave)[3] & termios.ECHO
        ending = read_terminal(master, b"\n")
        assert b"ARTIFICIAL_UNSUBMITTED_SECRET" not in ending
        # The next reader (e.g. the shell) must receive only its own new line.
        os.write(master, b"\n")
        read_terminal(master, b"\n")
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode == 0, stderr
        assert stdout == b"input queue clean\n"
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        os.close(master)
        os.close(slave)


def test_logout_storage_failure_is_not_success_and_never_echoes_corrupt_secrets(
    runtime_cli,
):
    run, _, _, home = runtime_cli
    assert run("setup-ai", "--package-manager", "npm").returncode == 0
    path = home / ".config/doc-translate/credentials.json"
    path.parent.mkdir(parents=True)
    path.write_text("ARTIFICIAL_CORRUPT_SECRET")
    result = run(
        "logout",
        "--provider",
        "anthropic",
        extra_env={"XDG_CONFIG_HOME": str(home / ".config")},
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "ARTIFICIAL_CORRUPT_SECRET" not in result.stderr
    assert path.read_text() == "ARTIFICIAL_CORRUPT_SECRET"

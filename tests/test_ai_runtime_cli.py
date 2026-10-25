"""CLI -> runtime observations; only the external package manager is faked."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runtime_cli(tmp_path):
    executable = shutil.which("doc-translate")
    node = shutil.which("node")
    assert executable and node
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "node").symlink_to(node)
    log = tmp_path / "manager.jsonl"
    for manager in ("npm", "pnpm"):
        script = binaries / manager
        script.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    print('11.19.0' if pathlib.Path(sys.argv[0]).name == 'npm' else '12.10.1')\n"
            "    sys.exit(0)\n"
            f"with open({str(log)!r}, 'a') as log:\n"
            "    log.write(json.dumps({'manager': pathlib.Path(sys.argv[0]).name, 'args': sys.argv[1:], 'cwd': os.getcwd(), 'corepack_network': os.environ.get('COREPACK_ENABLE_NETWORK'), 'manage_versions': os.environ.get('npm_config_manage_package_manager_versions')}) + '\\n')\n"
            "if os.environ.get('FAIL_INSTALL'):\n"
            "    sys.exit(7)\n"
            f"pathlib.Path('node_modules').symlink_to({str(ROOT / 'ai/node_modules')!r}, target_is_directory=True)\n"
        )
        script.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "PATH": str(binaries),
    }
    env.pop("NODE_OPTIONS", None)
    env.pop("NODE_PATH", None)

    def run(*arguments, extra_env=None):
        return subprocess.run(
            [executable, *arguments],
            env={**env, **(extra_env or {})},
            capture_output=True,
            text=True,
            check=False,
        )

    return run, binaries, log, tmp_path


def test_setup_installs_selected_locks_in_separate_runtimes_then_needs_only_node(
    runtime_cli,
):
    run, binaries, log, tmp_path = runtime_cli
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    document = workspace / "original.epub"
    document.write_text("keep document")
    credential = tmp_path / ".config/doc-translate/credentials.json"
    credential.parent.mkdir(parents=True)
    credential.write_text("keep credential")
    npm = run("setup-ai", "--package-manager", "npm")
    assert npm.returncode == 0, npm.stderr
    first = json.loads(npm.stdout)
    assert first["package_manager"] == "npm"
    assert first["library_version"] == "1.4.0"
    assert first["helper_version"] == "0.1.0"
    assert len(first["lock_sha256"]) == 64
    pnpm = run("setup-ai", "--package-manager", "pnpm")
    assert pnpm.returncode == 0, pnpm.stderr
    second = json.loads(pnpm.stdout)
    assert second["runtime"] != first["runtime"]
    assert Path(first["runtime"]).is_dir()
    installs = [json.loads(line) for line in log.read_text().splitlines()]
    assert installs[0]["args"] == [
        "ci",
        "--omit=dev",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
    ]
    assert installs[1]["corepack_network"] == "0"
    assert installs[1]["manage_versions"] == "false"
    assert installs[1]["args"] == [
        "install",
        "--prod",
        "--frozen-lockfile",
        "--ignore-scripts",
    ]
    assert Path(installs[0]["cwd"]).is_relative_to(tmp_path / "data/doc-translate/ai")
    assert document.read_text() == "keep document"
    assert credential.read_text() == "keep credential"
    (binaries / "npm").unlink()
    (binaries / "pnpm").unlink()
    providers = run("providers")
    assert providers.returncode == 0, providers.stderr
    assert {p["id"] for p in json.loads(providers.stdout)["providers"]} >= {
        "openai",
        "anthropic",
        "google",
    }
    models = run("models", "--provider", "anthropic")
    assert models.returncode == 0, models.stderr
    assert any(
        m["image_input"] and m["reasoning"] for m in json.loads(models.stdout)["models"]
    )
    assert len(log.read_text().splitlines()) == 2


@pytest.mark.parametrize(
    "arguments",
    [
        ["setup-ai"],
        ["setup-ai", "--package-manager", "yarn"],
        ["models"],
        ["models", "--provider", "openai", "--refresh"],
    ],
)
def test_cli_rejects_missing_or_unsupported_arguments(runtime_cli, arguments):
    run, _, log, _ = runtime_cli
    result = run(*arguments)
    assert result.returncode == 2
    assert not log.exists()


@pytest.mark.parametrize("missing", ["node", "npm"])
def test_setup_requires_installed_node_and_selected_manager(runtime_cli, missing):
    run, binaries, log, _ = runtime_cli
    (binaries / missing).unlink()
    result = run("setup-ai", "--package-manager", "npm")
    assert result.returncode == 2
    assert "setup-ai --package-manager" in result.stderr
    assert not log.exists()


def test_failed_setup_keeps_previous_runtime_usable(runtime_cli):
    run, _, log, _ = runtime_cli
    first = run("setup-ai", "--package-manager", "npm")
    assert first.returncode == 0, first.stderr
    failed = run(
        "setup-ai", "--package-manager", "pnpm", extra_env={"FAIL_INSTALL": "1"}
    )
    assert failed.returncode == 1
    assert "exit 7" in failed.stderr
    assert "setup-ai --package-manager" in failed.stderr
    assert run("providers").returncode == 0
    assert len(log.read_text().splitlines()) == 2


@pytest.mark.parametrize("mismatch", ["record", "helper", "dependency", "lock"])
def test_metadata_stops_on_runtime_mismatch_without_install(runtime_cli, mismatch):
    run, _, log, _ = runtime_cli
    setup = run("setup-ai", "--package-manager", "npm")
    assert setup.returncode == 0, setup.stderr
    runtime = Path(json.loads(setup.stdout)["runtime"])
    if mismatch == "record":
        record = json.loads((runtime / "runtime.json").read_text())
        record["library_version"] = "0.0.0"
        (runtime / "runtime.json").write_text(json.dumps(record))
    elif mismatch == "helper":
        (runtime / "dist/main.js").write_text("throw new Error('broken')")
    elif mismatch == "lock":
        (runtime / "package-lock.json").write_text("{}")
    else:
        (runtime / "node_modules").unlink()
        dependency = runtime / "node_modules/@earendil-works/pi-ai"
        dependency.mkdir(parents=True)
        (dependency / "package.json").write_text('{"version":"0.0.0"}')
    result = run("providers")
    assert result.returncode == 2
    assert "setup-ai --package-manager" in result.stderr
    assert len(log.read_text().splitlines()) == 1


def test_unknown_provider_is_an_input_error(runtime_cli):
    run, _, log, _ = runtime_cli
    assert run("setup-ai", "--package-manager", "npm").returncode == 0
    result = run("models", "--provider", "not-a-provider")
    assert result.returncode == 2
    assert "Unknown built-in provider" in result.stderr
    assert len(log.read_text().splitlines()) == 1


@pytest.mark.parametrize(
    "failure", ["exit", "incomplete", "wrong-id", "invalid-payload"]
)
def test_invalid_or_failed_helper_is_never_success(runtime_cli, failure):
    run, binaries, _, _ = runtime_cli
    assert run("setup-ai", "--package-manager", "npm").returncode == 0
    (binaries / "node").unlink()
    (binaries / "node").write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('v24.21.0')\n"
        "    sys.exit(0)\n"
        "request = json.load(sys.stdin)\n"
        "request.update(type='result', payload={'helper_version':'0.1.0', 'library_version':'1.4.0', 'providers': [{'id':'openai', 'name':'OpenAI'}]})\n"
        f"if {failure!r} == 'invalid-payload': request['payload']['providers'] = [42]\n"
        f"if {failure!r} == 'wrong-id': request['request_id'] = 'wrong'\n"
        f"print(json.dumps(request), end='' if {failure!r} == 'incomplete' else '\\n')\n"
        "print('SECRET_HELPER_STDERR', file=sys.stderr)\n"
        f"sys.exit(1 if {failure!r} == 'exit' else 0)\n"
    )
    (binaries / "node").chmod(0o755)
    result = run("providers")
    assert result.returncode == 1
    assert "setup-ai --package-manager" in result.stderr
    assert "SECRET_HELPER_STDERR" not in result.stderr + result.stdout


@pytest.mark.parametrize("node_output", ["v22.19.0", "not-a-version"])
def test_setup_rejects_unsupported_node_before_install(runtime_cli, node_output):
    run, binaries, log, _ = runtime_cli
    (binaries / "node").unlink()
    (binaries / "node").write_text(f"#!{sys.executable}\nprint({node_output!r})\n")
    (binaries / "node").chmod(0o755)
    result = run("setup-ai", "--package-manager", "npm")
    assert result.returncode == 2
    assert "Node >=24.21.0" in result.stderr
    assert "setup-ai --package-manager" in result.stderr
    assert not log.exists()


def test_setup_guides_when_node_cannot_execute(runtime_cli):
    run, binaries, log, _ = runtime_cli
    (binaries / "node").unlink()
    (binaries / "node").write_text("not an executable format")
    (binaries / "node").chmod(0o755)
    result = run("setup-ai", "--package-manager", "npm")
    assert result.returncode == 2
    assert "setup-ai --package-manager" in result.stderr
    assert not log.exists()


def test_setup_keeps_runtime_and_installed_executables_resolvable_after_chdir(
    runtime_cli,
):
    run, binaries, _, tmp_path = runtime_cli
    result = run(
        "setup-ai",
        "--package-manager",
        "npm",
        extra_env={
            "PATH": os.path.relpath(binaries),
            "XDG_DATA_HOME": os.path.relpath(tmp_path / "data"),
        },
    )
    assert result.returncode == 0, result.stderr
    assert Path(json.loads(result.stdout)["runtime"]).is_absolute()

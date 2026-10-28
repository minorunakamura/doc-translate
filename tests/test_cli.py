import os
import shutil
import subprocess

import pytest


def cli(*arguments, env=None):
    executable = shutil.which("doc-translate")
    assert executable is not None
    return subprocess.run(
        [executable, *arguments],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("command", [[], ["setup-ai"], ["providers"], ["models"]])
def test_cli_help_is_available_without_pdf_or_node(tmp_path, command):
    result = cli(*command, "--help", env={**os.environ, "PATH": str(tmp_path)})
    assert result.returncode == 0
    assert "usage: doc-translate" in result.stdout
    assert "setup-ai" in result.stdout
    assert "JSON" in result.stdout


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("revise", ["Offline", "candidate_sha256", "CAS", "inference", "exit 2"]),
        ("resume", ["candidate", "before preparation/translation", "exits 1"]),
        ("render", ["adopted source H", "pending adoption", "exits 1"]),
        ("status", ["adopted source H", "pending candidate C", "exits 0"]),
    ],
)
def test_cli_revision_and_candidate_help_is_available_offline(
    tmp_path, command, expected
):
    result = cli(command, "--help", env={**os.environ, "PATH": str(tmp_path)})
    assert result.returncode == 0
    output = " ".join(result.stdout.split())
    assert all(text in output for text in expected)
    if command == "resume":
        assert "validated data, never code" in output


def test_cli_workflow_does_not_pretend_to_succeed(tmp_path):
    workspace = tmp_path / "workspace"
    result = cli("run", "artificial.epub", "--workspace", str(workspace))
    assert result.returncode == 2
    assert not workspace.exists()


def test_metadata_without_setup_stops_with_setup_guidance(tmp_path):
    result = cli(
        "providers",
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "XDG_DATA_HOME": str(tmp_path / "data"),
        },
    )
    assert result.returncode == 2
    assert "setup-ai --package-manager" in result.stderr
    assert not (tmp_path / "data").exists()

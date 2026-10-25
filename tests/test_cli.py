import os
import shutil
import subprocess


def test_cli_help_is_available_without_pdf_or_node(tmp_path):
    executable = shutil.which("doc-translate")
    assert executable is not None
    result = subprocess.run(
        [executable, "--help"],
        env={**os.environ, "PATH": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "usage: doc-translate" in result.stdout
    assert "not implemented" in result.stdout


def test_cli_workflow_does_not_pretend_to_succeed(tmp_path):
    workspace = tmp_path / "workspace"
    result = subprocess.run(
        ["doc-translate", "run", "artificial.epub", "--workspace", str(workspace)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "not implemented" in result.stderr
    assert not workspace.exists()

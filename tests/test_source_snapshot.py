import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from doc_translate.snapshot import (
    PDFLocation,
    SourceSnapshot,
    load_source_snapshot,
    save_source_snapshot,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_epub_snapshot_round_trip_preserves_source_structure_and_uncertainty(tmp_path):
    snapshot = SourceSnapshot.model_validate_json(
        (FIXTURES / "source-snapshot.epub.json").read_text()
    )
    digest = save_source_snapshot(tmp_path, snapshot)
    restored = load_source_snapshot(tmp_path)
    assert restored == snapshot
    assert load_source_snapshot(tmp_path, digest) == snapshot
    assert restored.blocks[1].text == "if ready:\n    print('hello')\n"
    assert restored.blocks[0].structure["inline"] == [
        {"kind": "emphasis", "text": "small"}
    ]
    assert restored.uncertainties[0].origin == "original"
    assert restored.processing.status == "completed"
    assert restored.content_reviews == []


def test_pdf_snapshot_preserves_table_cells_evidence_and_limited_content_review(
    tmp_path,
):
    snapshot = SourceSnapshot.model_validate_json(
        (FIXTURES / "source-snapshot.pdf.json").read_text()
    )
    save_source_snapshot(tmp_path, snapshot)
    restored = load_source_snapshot(tmp_path)
    assert restored == snapshot
    location = restored.blocks[0].locations[0]
    assert isinstance(location, PDFLocation)
    assert location.page == 1
    cells = restored.blocks[0].structure["cells"]
    assert isinstance(cells, list)
    assert cells[1] == {
        "cell_id": "cell-b",
        "row": 1,
        "column": 0,
        "row_span": 1,
        "column_span": 1,
        "text": "",
    }
    assert restored.content_reviews[0].target_ids == ["caption-a"]
    assert restored.uncertainties == []


def test_source_revision_keeps_old_snapshot_and_change_reason(tmp_path):
    original = SourceSnapshot.model_validate_json(
        (FIXTURES / "source-snapshot.epub.json").read_text()
    )
    previous = save_source_snapshot(tmp_path, original)
    data = original.model_dump(mode="json")
    data.update(
        snapshot_id="source-epub-2",
        previous_snapshot_sha256=previous,
        change_reason="Corrected transcription after inspecting the original.",
    )
    data["blocks"][0]["text"] = "A tiny example with emphasis."
    revised = SourceSnapshot.model_validate(data)
    current = save_source_snapshot(tmp_path, revised)
    assert current != previous
    assert load_source_snapshot(tmp_path, previous) == original
    assert load_source_snapshot(tmp_path) == revised
    assert load_source_snapshot(tmp_path).previous_snapshot_sha256 == previous


def test_load_checks_artifact_hash_before_accepting_content(tmp_path):
    snapshot = SourceSnapshot.model_validate_json(
        (FIXTURES / "source-snapshot.epub.json").read_text()
    )
    digest = save_source_snapshot(tmp_path, snapshot)
    artifact = tmp_path / "snapshots" / "source" / f"{digest}.json"
    artifact.write_text(artifact.read_text().replace("small", "false"))
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_source_snapshot(tmp_path)


def test_save_does_not_overwrite_corrupted_existing_artifact(tmp_path):
    snapshot = SourceSnapshot.model_validate_json(
        (FIXTURES / "source-snapshot.epub.json").read_text()
    )
    digest = save_source_snapshot(tmp_path, snapshot)
    artifact = tmp_path / "snapshots" / "source" / f"{digest}.json"
    artifact.write_bytes(b"damaged artifact retained for inspection")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        save_source_snapshot(tmp_path, snapshot)
    assert artifact.read_bytes() == b"damaged artifact retained for inspection"


@pytest.mark.parametrize("defect", ["unknown_version", "non_json_number"])
def test_load_validates_schema_even_when_artifact_hash_matches(tmp_path, defect):
    data = (FIXTURES / "source-snapshot.epub.json").read_bytes()
    if defect == "unknown_version":
        data = data.replace(b'"schema_version": 1', b'"schema_version": 999')
    else:
        data = data.replace(b'"language": "python"', b'"invalid": NaN')
    digest = hashlib.sha256(data).hexdigest()
    directory = tmp_path / "snapshots" / "source"
    directory.mkdir(parents=True)
    (directory / f"{digest}.json").write_bytes(data)
    with pytest.raises(ValidationError):
        load_source_snapshot(tmp_path, digest)


def test_save_rejects_non_json_numbers_without_losing_previous_snapshot(tmp_path):
    snapshot = SourceSnapshot.model_validate_json(
        (FIXTURES / "source-snapshot.epub.json").read_text()
    )
    save_source_snapshot(tmp_path, snapshot)
    snapshot.blocks[0].structure["invalid"] = float("nan")
    with pytest.raises(ValidationError):
        save_source_snapshot(tmp_path, snapshot)
    assert "invalid" not in load_source_snapshot(tmp_path).blocks[0].structure


def test_failed_write_does_not_adopt_partial_snapshot_or_lose_previous(tmp_path):
    original = SourceSnapshot.model_validate_json(
        (FIXTURES / "source-snapshot.epub.json").read_text()
    )
    previous = save_source_snapshot(tmp_path, original)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import resource, signal, sys; from pathlib import Path; "
                "from doc_translate.snapshot import load_source_snapshot, save_source_snapshot; "
                "workspace = Path(sys.argv[1]); source = load_source_snapshot(workspace); "
                "source.blocks[0].text = 'x' * 100000; "
                "signal.signal(signal.SIGXFSZ, signal.SIG_IGN); "
                "resource.setrlimit(resource.RLIMIT_FSIZE, (1024, 1024)); "
                "save_source_snapshot(workspace, source)"
            ),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "File too large" in result.stderr
    assert load_source_snapshot(tmp_path) == original
    assert load_source_snapshot(tmp_path, previous) == original


def test_failed_reference_switch_keeps_adopted_snapshot_and_retains_new_artifact(
    tmp_path,
):
    original = SourceSnapshot.model_validate_json(
        (FIXTURES / "source-snapshot.epub.json").read_text()
    )
    previous = save_source_snapshot(tmp_path, original)
    revised = original.model_copy(deep=True)
    revised.blocks[0].text = "An edited example."
    mode = tmp_path.stat().st_mode
    try:
        tmp_path.chmod(0o500)
        with pytest.raises(PermissionError):
            save_source_snapshot(tmp_path, revised)
        assert load_source_snapshot(tmp_path) == original
        assert load_source_snapshot(tmp_path, previous) == original
        assert len(list((tmp_path / "snapshots" / "source").glob("*.json"))) == 2
    finally:
        tmp_path.chmod(mode)


def test_source_revision_requires_change_reason():
    data = json.loads((FIXTURES / "source-snapshot.epub.json").read_text())
    data["previous_snapshot_sha256"] = "a" * 64
    with pytest.raises(ValidationError, match="change_reason"):
        SourceSnapshot.model_validate(data)


def test_duplicate_writer_is_rejected_and_crashed_owner_releases_os_lock(tmp_path):
    from doc_translate.workspace import WorkspaceLockedError, workspace_lock

    owner = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import sys, time; "
                "from doc_translate.workspace import workspace_lock; "
                "\nwith workspace_lock(Path(sys.argv[1])):\n"
                " print('locked', flush=True)\n time.sleep(60)"
            ),
            str(tmp_path),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert owner.stdout is not None
        assert owner.stdout.readline().strip() == "locked"
        with pytest.raises(WorkspaceLockedError), workspace_lock(tmp_path):
            pytest.fail("A second writer entered the workspace")
        snapshot = SourceSnapshot.model_validate_json(
            (FIXTURES / "source-snapshot.epub.json").read_text()
        )
        with pytest.raises(WorkspaceLockedError):
            save_source_snapshot(tmp_path, snapshot)
    finally:
        owner.kill()
        owner.wait(timeout=5)
        if owner.stdout is not None:
            owner.stdout.close()
    save_source_snapshot(tmp_path, snapshot)
    assert load_source_snapshot(tmp_path) == snapshot


@pytest.mark.parametrize(
    "location",
    [
        {"format": "epub", "resource_path": "EPUB/chapter.xhtml"},
        {"format": "epub", "resource_path": "EPUB/chapter.xhtml", "node_path": "/p[1]"},
        {
            "format": "epub",
            "resource_path": "EPUB/chapter.xhtml",
            "element_id": "p1",
            "page": 1,
        },
    ],
)
def test_epub_snapshot_requires_real_element_or_snapshot_bound_node(location):
    data = json.loads((FIXTURES / "source-snapshot.epub.json").read_text())
    data["blocks"][0]["locations"] = [location]
    with pytest.raises(ValidationError):
        SourceSnapshot.model_validate(data)


@pytest.mark.parametrize(
    "defect",
    ["duplicate_id", "wrong_format", "unbound_ref", "zero_page", "missing_evidence"],
)
def test_source_snapshot_rejects_ambiguous_identity_or_provenance(defect):
    data = json.loads((FIXTURES / "source-snapshot.pdf.json").read_text())
    if defect == "duplicate_id":
        data["blocks"][1]["block_id"] = "table-a"
    elif defect == "wrong_format":
        data["document"]["format"] = "epub"
    elif defect == "unbound_ref":
        del data["blocks"][0]["locations"][0]["extraction_snapshot"]
    elif defect == "zero_page":
        data["blocks"][0]["locations"][0]["page"] = 0
    else:
        del data["blocks"][1]["locations"][0]["evidence"]
    with pytest.raises(ValidationError):
        SourceSnapshot.model_validate(data)

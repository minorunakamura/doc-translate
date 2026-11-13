"""Explicit offline preparation self-checks, outside the production CI test set."""

import json
from pathlib import Path

import pytest
from pdf_source_feasibility import correspond, inventory, new_directory, synthetic
from PIL import Image, ImageChops


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    pytest.importorskip("docling")
    parent = tmp_path_factory.mktemp("source-samples")
    output = synthetic(parent / "evidence")
    return output


def test_pdf_source_samples_use_public_acquire_correction_assembly(samples):
    from doc_translate.pdf import load_pdf_draft
    from doc_translate.pdf_correction import load_pdf_correction
    from doc_translate.pdf_source import assemble_pdf_source

    rows = json.loads((samples / "review-only/proposed-comparisons.json").read_bytes())
    assert len(rows) == 8
    for row in rows:
        workspace = samples / "synthetic-inputs" / row["sample"]
        before = inventory(workspace)
        draft = load_pdf_draft(workspace, row["draft_sha256"])
        source = assemble_pdf_source(
            workspace=workspace,
            chunk_sha256s=row["chunk_sha256s"],
            snapshot_id="self-check",
        )
        for digest in row["chunk_sha256s"]:
            chunk = load_pdf_correction(workspace, digest)
            assert [item.text for item in chunk.exclusions] == [
                f"Self-authored {row['family']} / page {page}"
                for page in chunk.owned_pages
            ]
            assert [
                item.evidence[0].page for item in chunk.exclusions
            ] == chunk.owned_pages
            assert all(
                len(item.evidence) == 1
                and item.evidence[0].ref is None
                and item.evidence[0].position == "page header at (60,40)"
                and item.reason
                == "Page header identifying synthetic document and page; not body content"
                for item in chunk.exclusions
            )
        assert not (workspace / "source-snapshot.json").exists()
        assert not (workspace / "run.json").exists()
        assert not list(workspace.rglob("credentials"))
        assert inventory(workspace) == before
        for page in draft.pages:
            with Image.open(workspace / page.image.artifact) as image:
                assert image.size == (1800, 900)
                assert ImageChops.difference(
                    image, Image.new("RGB", image.size, "white")
                ).getbbox()
        control = row["artificial_variant"] == "control"
        texts = [block.text for block in source.blocks]
        if row["family"] == "history-layout":
            assert any(
                not check.passed and check.name == "charspans" for check in draft.checks
            )
            assert any(
                not warning.target_ids and "charspan" in warning.reason
                for warning in source.uncertainties
            )
            page_two = [
                block
                for block in source.blocks
                if any(
                    location.format == "pdf" and location.page == 2
                    for location in block.locations
                )
            ]
            assert [block.text for block in page_two] == ["Measurement:"]
            original_uncertainties = [
                warning
                for warning in source.uncertainties
                if warning.origin == "original"
            ]
            assert len(original_uncertainties) == 1
            value_warning = original_uncertainties[0]
            assert value_warning.target_ids == []
            assert (
                value_warning.reason
                == "Unreadable obliterated measurement value; readable label retained"
            )
            assert len(value_warning.locations) == 1
            location = value_warning.locations[0]
            assert location.format == "pdf" and location.page == 2
            assert location.evidence is not None
            assert json.loads(location.evidence)["position"] == (
                "measurement value: black rectangle (420,190)-(900,265)"
            )
            assert ("Record the pressure before opening." in texts) == control
            assert ("Open the red valve immediately." in texts) != control
            assert ("Limit: 72 kPa." in texts) == control
            assert ("retry_count = 3" in texts) == control
            table = next(block for block in source.blocks if block.kind == "table")
            assert table.model_dump()["structure"]["cells"][1]["text"] == (
                "72" if control else "27"
            )
        elif row["family"] == "figure":
            picture = next(block for block in source.blocks if block.kind == "picture")
            assert picture.text == ""
            assert picture.model_dump()["structure"]["region"]["right"] == (
                1550 if control else 850
            )
            assert ("OUTLET" in texts) != control
        elif row["family"] == "body-connection":
            if control:
                assert texts == ["The blue valve must remain closed."]
                assert len(source.blocks[0].model_dump()["structure"]["fragments"]) == 2
                assert source.uncertainties == []
            else:
                assert texts == ["The blue valve", "remain closed."]
                assert any(
                    warning.stage == "pdf_connection"
                    for warning in source.uncertainties
                )
        else:
            assert texts == [
                "if ready:\n    send(72)\n    close()"
                if control
                else "if ready:\n    close()"
            ]
            assert len(source.blocks[0].model_dump()["structure"]["fragments"]) == 2
            assert source.uncertainties == []
        assert row["root_image_review"] == "pending"
        assert row["expected_adoption"] is None
    manifest = json.loads((samples / "hashes.json").read_bytes())
    assert all((samples / path).is_file() for path in manifest)


def test_correspondence_rejects_disappearing_history_and_changed_content(samples):
    from doc_translate.snapshot import SourceSnapshot

    original = SourceSnapshot.model_validate_json(
        (samples / "synthetic-inputs/sample-01/current-source.json").read_bytes()
    )
    selected = original.model_copy(deep=True)
    assert correspond(original, selected, {1, 2})
    selected.uncertainties.pop()
    with pytest.raises(ValueError, match="warning multiset"):
        correspond(original, selected, {1, 2})
    selected = original.model_copy(deep=True)
    selected.blocks[0].text = "Changed"
    with pytest.raises(ValueError, match="content/locations"):
        correspond(original, selected, {1, 2})
    selected = original.model_copy(deep=True)
    selected.blocks.pop()
    with pytest.raises(ValueError, match="fragment series"):
        correspond(original, selected, {1, 2})
    altered = original.model_dump()
    altered["blocks"][0]["structure"]["fragments"][0]["block"]["outgoing"] = {
        "target": {"page": 99},
        "mode": "space",
    }
    with pytest.raises(ValueError, match="outside fixed scopes"):
        correspond(original, SourceSnapshot.model_validate(altered), {1, 2})


@pytest.mark.parametrize(
    "case",
    ["existing", "same", "inside", "contains", "symlink", "workspace", "public-parent"],
)
def test_preparation_refuses_unsafe_output_without_changing_old_bytes(tmp_path, case):
    source = tmp_path / "source"
    source.mkdir()
    (source / "sentinel").write_bytes(b"keep")
    output = tmp_path / "output"
    if case == "existing":
        output.mkdir()
        (output / "old").write_bytes(b"keep result")
    elif case == "same":
        output = source
    elif case == "inside":
        output = source / "new"
    elif case == "contains":
        output = tmp_path
    elif case == "symlink":
        link = tmp_path / "link"
        link.symlink_to(source, target_is_directory=True)
        output = link / "new"
    elif case == "workspace":
        (tmp_path / "run.json").write_text("{}")
    else:
        public = tmp_path / "public"
        public.mkdir(mode=0o755)
        public.chmod(0o755)
        output = public / "new"
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises((ValueError, FileExistsError)):
        new_directory(output, source)
    assert all(path.read_bytes() == data for path, data in before.items())
    assert not (source / "new").exists()


def test_private_output_is_exclusive_and_not_a_workspace(tmp_path):
    output = new_directory(tmp_path / "new")
    assert output.stat().st_mode & 0o777 == 0o700
    assert list(output.iterdir()) == []
    with pytest.raises(FileExistsError):
        new_directory(output)
    assert Path(output).is_dir()

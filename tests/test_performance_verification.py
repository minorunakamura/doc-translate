"""Small checks for Issue #54's verification-only recipes, not timing thresholds."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / ".pi/skills/verify-project/scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baseline = load("performance_baseline")
operation = load("performance_operation")


@pytest.mark.parametrize("adopted", [False, True])
def test_preparation_baseline_public_reuse_separates_search_from_load(
    tmp_path, monkeypatch, adopted
):
    from doc_translate import preparation
    from doc_translate.snapshot import load_source_snapshot
    from doc_translate.units import generate_translation_units

    monkeypatch.syspath_prepend(str(SCRIPTS))
    recipe = load("preparation_baseline")
    owned, seed = tmp_path / "owned", tmp_path / "seed"
    owned.mkdir()
    recipe.create_seed(owned, seed, 2)
    snapshot = load_source_snapshot(seed)
    units = generate_translation_units(snapshot)
    assert [unit.kind for unit in units] == ["heading", "body"] * 2
    for unit, block_id in zip(units[1::2], ["body-0", "body-2"], strict=True):
        heading_path = unit.context["heading_path"]
        assert isinstance(heading_path, list)
        heading = heading_path[-1]
        assert isinstance(heading, dict) and heading["block_id"] == block_id
    fixture = recipe.import_module("test_preparation")
    previous = recipe.files(seed)
    sent = (owned / "requests.jsonl").read_bytes()
    if not adopted:
        (seed / "preparation-snapshot.json").unlink()
    with operation.preparation_measurement(seed, diagnostic=True) as metrics:
        preparation.prepare_document(
            workspace=seed,
            book="Artificial book",
            policy=fixture.POLICY,
            config=fixture.CONFIG,
            process_command=["must-not-launch-on-reuse"],
        )
    assert recipe.files(seed) == previous
    assert (owned / "requests.jsonl").read_bytes() == sent
    assert metrics["search_calls"] == 3
    assert metrics["public_load_calls"] == int(adopted)
    counts = metrics["attempt_read_hash_counts"]
    for scope, expected in (("search_call", 3), ("public_load", 3 if adopted else 0)):
        reads = {
            k.rsplit("/", 1)[-1]: v
            for k, v in counts.items()
            if k.startswith(f"{scope}/read/")
        }
        hashes = {
            k.rsplit("/", 1)[-1]: v
            for k, v in counts.items()
            if k.startswith(f"{scope}/hash/")
        }
        assert reads == hashes and sum(reads.values()) == expected
        assert all(count == 1 for count in reads.values())
    with operation.preparation_measurement(seed, diagnostic=True) as loaded:
        assert preparation.load_preparation(seed).glossary.terms == {"queue": "キュー"}
    assert loaded["search_calls"] == 0 and loaded["public_load_calls"] == 1
    assert (
        sum(
            v
            for k, v in loaded["attempt_read_hash_counts"].items()
            if k.startswith("public_load/read/")
        )
        == 3
    )


def test_preparation_baseline_seed_keeps_three_independent_collect_requests(
    tmp_path, monkeypatch
):
    from doc_translate.preparation import load_preparation
    from doc_translate.snapshot import load_source_snapshot
    from doc_translate.units import generate_translation_units

    monkeypatch.syspath_prepend(str(SCRIPTS))
    recipe = load("preparation_baseline")
    owned, seed = tmp_path / "owned", tmp_path / "seed"
    owned.mkdir()
    _, log = recipe.create_seed(owned, seed, 3)
    adopted = load_preparation(seed)
    units = generate_translation_units(load_source_snapshot(seed))
    headings = [unit.source_text for unit in units if unit.kind == "heading"]
    assert headings == [f"Section group{index}" for index in range(3)]
    requests = [
        json.loads(json.loads(line)["messages"][1]["content"][0]["text"])
        for line in log.read_text().splitlines()
    ]
    assert [request["context"]["phase"] for request in requests] == [
        "collect",
        "collect",
        "collect",
        "organize",
    ]
    assert [
        json.loads(request["text"])[0]["source_text"] for request in requests[:3]
    ] == headings
    assert len({request["text"] for request in requests[:3]}) == 3
    records = {path.stem for path in (seed / "attempts/preparation").glob("*.json")}
    assert len(records) == len(adopted.runs) == len(set(adopted.runs)) == 4
    assert records == set(adopted.runs)


def test_performance_baseline_distribution_keeps_raw_order():
    assert baseline.distribution([3, 1, 2]) == {
        "raw": [3, 1, 2],
        "median": 2,
        "min": 1,
        "max": 3,
    }


@pytest.mark.parametrize("format", ["pdf", "epub"])
def test_performance_operation_render_recipe_uses_public_save_and_render(
    tmp_path, format
):
    from doc_translate.render import render_document
    from doc_translate.snapshot import load_source_snapshot, save_source_snapshot
    from doc_translate.units import generate_translation_units

    source = operation.render_source(format, 3)
    save_source_snapshot(tmp_path, source)
    assert load_source_snapshot(tmp_path) == source
    assert len(generate_translation_units(source)) == 3
    summary = render_document(tmp_path)
    qa = json.loads((tmp_path / summary["artifacts"]["qa"]).read_bytes())
    assert summary["processing"]["status"] == "partial"
    assert (
        sum(
            issue["reason"] == "Self-authored uncertainty."
            for issue in qa["uncertainties"]
        )
        == 3
    )
    assert len(qa["uncertainties"]) == 6  # Includes the three untranslated units.


def test_performance_operation_observe_delegates_reads_and_atomic_writes(tmp_path):
    from doc_translate.snapshot import _atomic_write

    output = tmp_path / "diagnostic.json"
    path = tmp_path / "snapshots/own.json"
    path.parent.mkdir()
    with operation.observe(tmp_path, output):
        _atomic_write(path, b"abc")
        assert path.read_bytes() == b"abc"
        assert path.read_bytes() == b"abc"
    diagnostic = json.loads(output.read_bytes())
    assert diagnostic["counts"]["logical_read_bytes"] == 6
    assert diagnostic["counts"]["replace_snapshots_bytes"] == 3
    assert diagnostic["writes"] == {"snapshots/own.json": 1}
    assert diagnostic["reads"] == {"snapshots/own.json": 2}
    atomic_write = next(
        row for row in diagnostic["profile"] if row["function"] == "_atomic_write"
    )
    assert atomic_write["calls"] == atomic_write["primitive_calls"] == 1
    assert 0 <= atomic_write["self_seconds"] <= atomic_write["inclusive_seconds"]


def test_performance_baseline_multiple_resources_are_real_epub_boundaries(tmp_path):
    from doc_translate.epub import read_epub

    source = baseline.epub_source(tmp_path / "input", 2, multiple=True)
    book = read_epub(source)
    assert len(book.spine) == 2
    assert [item.resolved_id for item in book.spine] == ["ch0", "ch1"]
    assert all(f"OPS/section-{i}.xhtml" in book.resources for i in range(2))


@pytest.mark.parametrize(
    "interruption", [KeyboardInterrupt, baseline.subprocess.TimeoutExpired]
)
def test_performance_baseline_interruption_bounds_drain_and_reaps_owned_group(
    tmp_path, monkeypatch, interruption
):
    import traceback
    from unittest.mock import Mock

    initial = (
        interruption("command", 1)
        if interruption is baseline.subprocess.TimeoutExpired
        else interruption("synthetic private exception detail")
    )
    process = Mock(pid=12345, returncode=-9)
    process.communicate.side_effect = [
        initial,
        baseline.subprocess.TimeoutExpired("command", 45),
        ("{}", "interrupted"),
    ]
    signals = []
    monkeypatch.setattr(baseline.subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(
        baseline.os, "killpg", lambda pid, sig: signals.append((pid, sig))
    )
    with pytest.raises(
        KeyboardInterrupt if interruption is KeyboardInterrupt else AssertionError
    ) as raised:
        baseline.invoke(["owned-command"], {}, tmp_path, 1)
    assert signals == [
        (12345, baseline.signal.SIGINT),
        (12345, baseline.signal.SIGKILL),
    ]
    assert process.communicate.call_args_list[0].kwargs == {"timeout": 1}
    assert process.communicate.call_args_list[1].kwargs == {"timeout": 45}
    assert process.communicate.call_count == 3
    assert (tmp_path / "stdout.json").read_text() == "{}"
    assert (tmp_path / "stderr.txt").read_text() == "interrupted"
    record = json.loads((tmp_path / "command.json").read_text())
    assert record["command"] == ["owned-command"]
    assert record["exit"] == -9
    assert record["timed_out"] is (interruption is baseline.subprocess.TimeoutExpired)
    assert record["external_process_wall_seconds"] >= 0
    if interruption is KeyboardInterrupt:
        assert raised.value is initial
        frames = traceback.extract_tb(raised.value.__traceback__)
        assert sum(frame.name == "invoke" for frame in frames) == 1
        assert frames[-1].name == "_execute_mock_call"
    assert all(
        "synthetic private exception detail" not in path.read_text()
        for path in tmp_path.iterdir()
    )


def test_performance_baseline_saves_both_documents_and_exact_local_assets(tmp_path):
    workspace, output = tmp_path / "workspace", tmp_path / "evidence"
    output.mkdir()
    rendered = workspace / "renders/own"
    (rendered / "assets").mkdir(parents=True)
    artifacts = {}
    for key in ("qa", "source_map", "summary", "japanese", "english"):
        suffix = ".md" if key in ("japanese", "english") else ".json"
        path = rendered / f"{key}{suffix}"
        path.write_bytes(f"self-authored {key}".encode())
        artifacts[key] = str(path.relative_to(workspace))
    (rendered / "assets/own.png").write_bytes(b"self-authored asset")
    baseline.save_render_artifacts(workspace, {"artifacts": artifacts}, output)
    for key, relative in artifacts.items():
        path = workspace / relative
        assert (output / f"saved-{key}{path.suffix}").read_bytes() == path.read_bytes()
    assert (output / "saved-assets/own.png").read_bytes() == b"self-authored asset"


@pytest.mark.parametrize("adopted", [False, True])
def test_translation_baseline_pre_job_boundary_and_public_load_are_separate(
    tmp_path, monkeypatch, adopted
):
    from doc_translate import translation

    monkeypatch.syspath_prepend(str(SCRIPTS))
    recipe = load("translation_baseline")
    owned, seed = tmp_path / "owned", tmp_path / "seed"
    owned.mkdir()
    recipe.create_seed(owned, seed, 2)
    previous = recipe.immutable(seed)
    sent = (owned / "translation-requests.jsonl").read_bytes()
    assert len(sent.splitlines()) == 2
    fixture = recipe.import_module("test_translation")
    if not adopted:
        (seed / "translation-snapshot.json").unlink()
    with recipe.translation_measurement(seed, diagnostic=True) as metrics:
        translation.translate_document(
            workspace=seed,
            config=fixture.CONFIG,
            process_command=["must-not-launch-on-reuse"],
        )
    assert all(recipe.digest(seed / p) == h for p, h in previous.items())
    assert (owned / "translation-requests.jsonl").read_bytes() == sent
    assert metrics["search_intervals"] == 1
    assert metrics["worker_submissions"] == 0
    assert metrics["public_load_calls"] == int(adopted)
    counts = metrics["attempt_diagnostic_counts"]
    for scope, expected in (
        ("operation_search", 2),
        ("public_load", 4 if adopted else 0),
    ):
        actual = {
            kind: {
                k.rsplit("/", 1)[-1]: v
                for k, v in counts.items()
                if k.startswith(f"{scope}/{kind}/")
            }
            for kind in ("read", "hash", "parse")
        }
        assert actual["read"] == actual["hash"] == actual["parse"]
        assert sum(actual["read"].values()) == expected
    assert counts["operation_search/request"] >= 2
    assert counts["operation_search/matches_request"] >= 2
    assert not any(k.startswith("store/read/") for k in counts)
    with recipe.translation_measurement(seed, diagnostic=True) as loaded:
        recipe.check_translation(translation.load_translation(seed), 2)
    assert loaded["search_intervals"] == loaded["worker_submissions"] == 0
    assert loaded["public_load_calls"] == 1
    assert (
        sum(
            v
            for k, v in loaded["attempt_diagnostic_counts"].items()
            if k.startswith("public_load/read/")
        )
        == 4
    )


def test_performance_revision_checks_real_import_path(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    revision = load("performance_revision")
    root = Path(__file__).resolve().parents[1] / "src"
    imported = revision.imported_sources(root)
    assert set(imported) == {"preparation", "translation", "render", "run", "snapshot"}
    assert all(Path(item["path"]).is_relative_to(root) for item in imported.values())
    assert all(len(item["sha256"]) == 64 for item in imported.values())
    with pytest.raises(AssertionError):
        revision.imported_sources(tmp_path)


def test_performance_comparison_preserves_qa_targets_and_order(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    comparison = load("performance_comparison")
    before, after = tmp_path / "before", tmp_path / "after"
    for directory, digest in ((before, "a" * 64), (after, "b" * 64)):
        directory.mkdir()
        for name in ("japanese", "english"):
            (directory / f"saved-{name}.md").write_text("Self-authored queue.")
        (directory / "saved-source_map.json").write_text(
            json.dumps(
                {"units": [{"source_snapshot_sha256": digest, "unit_id": "unit-1"}]}
            )
        )
        (directory / "saved-qa.json").write_text(
            json.dumps(
                {
                    "checks": [
                        {
                            "name": "pdf_chunk_ownership",
                            "details": f"chunk={digest}; owned_pages=[1]",
                            "passed": True,
                            "target_ids": ["block-1", "block-2"],
                        }
                    ]
                }
            )
        )
    assert comparison.compare_saved_outputs(before, after, independent=True)["equal"]
    with pytest.raises(AssertionError):
        comparison.compare_saved_outputs(before, after)
    qa = json.loads((after / "saved-qa.json").read_text())
    qa["checks"][0]["target_ids"].reverse()
    (after / "saved-qa.json").write_text(json.dumps(qa))
    with pytest.raises(AssertionError):
        comparison.compare_saved_outputs(before, after, independent=True)


def test_performance_comparison_rejects_epub_source_snapshot_digest_difference(
    tmp_path, monkeypatch
):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    comparison = load("performance_comparison")
    before, after = tmp_path / "before", tmp_path / "after"
    for directory in (before, after):
        directory.mkdir()
        for name in ("japanese", "english"):
            (directory / f"saved-{name}.md").write_text("Self-authored EPUB queue.")
        (directory / "saved-qa.json").write_text(json.dumps({"checks": []}))
        (directory / "saved-source_map.json").write_text(
            json.dumps(
                {
                    "units": [
                        {
                            "source_snapshot_sha256": "a" * 64,
                            "unit_id": "unit-1",
                            "locations": [
                                {"format": "epub", "resource_path": "own.xhtml"}
                            ],
                        }
                    ]
                }
            )
        )
    assert comparison.compare_saved_outputs(before, after)["equal"]
    mapping = json.loads((after / "saved-source_map.json").read_text())
    mapping["units"][0]["source_snapshot_sha256"] = "b" * 64
    (after / "saved-source_map.json").write_text(json.dumps(mapping))
    with pytest.raises(AssertionError, match="saved-source_map"):
        comparison.compare_saved_outputs(before, after)

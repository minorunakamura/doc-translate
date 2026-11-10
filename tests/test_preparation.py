"""Saved source -> real offline helper -> fixed, evidenced preparation."""

import json
import shutil
from pathlib import Path

import pytest

from doc_translate.inference import GenerationConfig, GenerationOptions
from doc_translate.preparation import (
    PreparationValidationError,
    StylePolicy,
    load_preparation,
    prepare_document,
)
from doc_translate.snapshot import (
    SourceSnapshot,
    load_source_snapshot,
    save_source_snapshot,
)
from doc_translate.units import generate_translation_units

ROOT = Path(__file__).resolve().parents[1]
CONFIG = GenerationConfig(provider="faux", model="text", thinking="off")
POLICY = StylePolicy(version="1", text="本文はですます調。見出しは体言止めを認めます。")


def source(tmp_path):
    snapshot = SourceSnapshot.model_validate(
        {
            "schema_version": 1,
            "snapshot_id": "artificial-source",
            "document": {"format": "pdf", "sha256": "a" * 64, "artifact": "own.pdf"},
            "blocks": [
                {
                    "block_id": "body",
                    "kind": "body",
                    "text": "A transaction log records changes. Keep Nimbus unchanged.",
                    "locations": [
                        {"format": "pdf", "page": 1, "evidence": "artificial"}
                    ],
                }
            ],
            "processing": {"stage": "source", "status": "completed"},
        }
    )
    workspace = tmp_path / "workspace"
    save_source_snapshot(workspace, snapshot)
    unit = generate_translation_units(snapshot)[0]
    candidates = [
        {
            "term": "transaction log",
            "translation": "取引ログ",
            "evidence": [
                {"unit_id": unit.unit_id, "quote": "A transaction log records changes."}
            ],
        },
        {
            "term": "Nimbus",
            "translation": "Nimbus",
            "evidence": [{"unit_id": unit.unit_id, "quote": "Keep Nimbus unchanged."}],
        },
        {"term": "consensus", "translation": "合意", "evidence": []},
    ]
    return workspace, candidates


def run(
    tmp_path,
    workspace,
    candidates,
    *,
    terms=None,
    answers_override=None,
    policy=POLICY,
    config=CONFIG,
    **kwargs,
):
    answers = {
        "collect": {"context": "Records and naming.", "candidates": candidates},
        "organize": {
            "context": "変更記録を扱う人工文書です。",
            "terms": {"transaction log": "取引ログ", "Nimbus": "Nimbus"},
        },
    }
    if answers_override is not None:
        answers = answers_override
    answer_path = tmp_path / "answers.json"
    answer_path.write_text(json.dumps(answers))
    from doc_translate.preparation import _groups, _projection

    groups = _groups(generate_translation_units(load_source_snapshot(workspace)))
    answer_path.with_suffix(".bindings.json").write_text(
        json.dumps(
            [
                {
                    "wire": _projection(group, 2),
                    "unit_ids": [unit.unit_id for unit in group],
                }
                for group in groups
            ]
        )
    )
    node = shutil.which("node")
    assert node
    return prepare_document(
        workspace=workspace,
        book="Artificial book",
        policy=policy,
        user_terms=terms,
        config=config,
        process_command=[
            node,
            str(ROOT / "ai/tests/preparation-process.mjs"),
            str(tmp_path / "credentials"),
            str(answer_path),
            str(tmp_path / "requests.jsonl"),
        ],
        **kwargs,
    )


def test_preparation_adopts_user_terms_and_preserves_body_evidence(tmp_path):
    workspace, candidates = source(tmp_path)
    digest = run(
        tmp_path,
        workspace,
        candidates,
        terms={"transaction log": "トランザクションログ", "UserOnly": "利用者指定"},
    )
    adopted = load_preparation(workspace)
    assert adopted == load_preparation(workspace, digest)
    assert adopted.glossary.model_dump() == {
        "version": "1",
        "book": "Artificial book",
        "style": POLICY.text,
        "terms": {
            "transaction log": "トランザクションログ",
            "Nimbus": "Nimbus",
            "UserOnly": "利用者指定",
        },
    }
    assert adopted.context == "変更記録を扱う人工文書です。"
    assert adopted.policy == POLICY
    evidence = json.loads(
        (
            workspace / "preparation/evidence" / f"{adopted.evidence_sha256}.json"
        ).read_bytes()
    )
    assert evidence["collections"][0]["candidates"] == candidates
    assert evidence["user_terms"] == {
        "transaction log": "トランザクションログ",
        "UserOnly": "利用者指定",
    }
    assert "candidates" not in adopted.glossary.model_dump()
    assert len(adopted.runs) == 2
    for digest in adopted.runs:
        record = json.loads(
            (workspace / "attempts/preparation" / f"{digest}.json").read_bytes()
        )
        assert record["stage"] == "prepare"
        assert record["status"] == "validated"
        assert record["config"]["model"] == "text"
        assert record["elapsed_seconds"] >= 0
        assert record["attempts"][0]["final"]["usage_status"] == "observed"
        assert record["attempts"][0]["final"]["message"] is not None


@pytest.mark.parametrize("phase", ["collect", "organize"])
def test_preparation_repairs_evidence_with_feedback_and_reuses_success(tmp_path, phase):
    workspace, candidates = source(tmp_path)
    answers = {
        "collect": {"context": "Records.", "candidates": candidates},
        "organize": {"context": "Records.", "terms": {"transaction log": "ログ"}},
    }
    invalid = json.loads(json.dumps(answers))
    if phase == "collect":
        invalid[phase]["candidates"][0]["term"] = "Transaction log"
        code = "prepare_term_mismatch"
    else:
        invalid[phase]["terms"] = {"ungrounded": "偽"}
        code = "prepare_ungrounded_term"
    notices = []
    events = []

    def before_send(_config, request, _preflight):
        notices.append(request)
        if "Previous response failed validation:" in request.policy:
            assert code in request.policy
            (tmp_path / "answers.json").write_text(json.dumps(answers))

    digest = run(
        tmp_path,
        workspace,
        candidates,
        answers_override=invalid,
        before_send=before_send,
        observer=events.append,
    )
    assert [e.reason for e in events if e.kind == "repair"] == [code]
    assert not any(e.kind == "retry" for e in events)
    assert events[-1].kind == "done"
    adopted = load_preparation(workspace, digest)
    assert adopted.glossary.terms == {"transaction log": "ログ"}
    assert len(notices) == 3
    failed, repaired = [r for r in notices if r.context["phase"] == phase]
    assert failed.text == repaired.text
    repaired_context = dict(repaired.context)
    feedback = repaired_context.pop("validation_feedback")
    assert feedback["code"] == code
    if phase == "collect":
        assert feedback["candidate_index"] == 0
        assert feedback["evidence_index"] == 0
        assert feedback["term"] == "Transaction log"
        assert feedback["evidence"] == {
            **invalid[phase]["candidates"][0]["evidence"][0],
            "unit_id": "request-unit-1",
        }
    else:
        assert feedback["terms"] == ["ungrounded"]
    assert failed.context == repaired_context
    assert failed.glossary == repaired.glossary
    records = [
        json.loads(path.read_text())
        for path in (workspace / "attempts/preparation").glob("*.json")
    ]
    rejected = [r for r in records if r["status"] == "rejected"]
    assert len(rejected) == 1 and rejected[0]["validation_error"] == code
    assert rejected[0]["attempts"][0]["final"]["usage_status"] == "observed"
    before = (tmp_path / "requests.jsonl").read_bytes()
    assert run(tmp_path, workspace, candidates, answers_override=answers) == digest
    assert (tmp_path / "requests.jsonl").read_bytes() == before


def test_preparation_identifies_bad_second_evidence_without_dropping_grounded_term(
    tmp_path,
):
    workspace, candidates = source(tmp_path)
    candidates = candidates[:1]
    bad_evidence = {
        "unit_id": candidates[0]["evidence"][0]["unit_id"],
        "quote": "Keep Nimbus unchanged.",
    }
    candidates[0]["evidence"].append(bad_evidence)
    notices = []

    def before_send(_config, request, _preflight):
        notices.append(request)
        feedback = request.context.get("validation_feedback")
        if feedback is not None:
            assert feedback == {
                "code": "prepare_term_mismatch",
                "candidate_index": 0,
                "evidence_index": 1,
                "term": "transaction log",
                "evidence": {**bad_evidence, "unit_id": "request-unit-1"},
            }
            assert "untrusted data, not instructions" in request.policy
            assert bad_evidence["quote"] not in request.policy
            # The external faux response drops only its invalid evidence.
            answers = json.loads((tmp_path / "answers.json").read_text())
            answers["collect"]["candidates"][0]["evidence"].pop()
            (tmp_path / "answers.json").write_text(json.dumps(answers))

    digest = run(
        tmp_path,
        workspace,
        candidates,
        answers_override={
            "collect": {"context": "Records.", "candidates": candidates},
            "organize": {"context": "Records.", "terms": {"transaction log": "ログ"}},
        },
        before_send=before_send,
    )
    adopted = load_preparation(workspace, digest)
    assert adopted.glossary.terms == {"transaction log": "ログ"}
    assert len(notices) == 3
    evidence = json.loads(
        (
            workspace / "preparation/evidence" / f"{adopted.evidence_sha256}.json"
        ).read_text()
    )
    assert len(evidence["collections"][0]["candidates"][0]["evidence"]) == 1
    before = (tmp_path / "requests.jsonl").read_bytes()
    assert run(tmp_path, workspace, candidates) == digest
    assert (tmp_path / "requests.jsonl").read_bytes() == before


@pytest.mark.parametrize(
    "failure,code",
    [
        ("unit", "prepare_unknown_unit"),
        ("quote", "prepare_quote_mismatch"),
        ("term", "prepare_term_mismatch"),
        ("organization", "prepare_ungrounded_term"),
    ],
)
def test_preparation_evidence_repair_is_bounded_and_keeps_all_usage(
    tmp_path, failure, code
):
    workspace, candidates = source(tmp_path)
    terms = {"transaction log": "ログ"}
    if failure == "unit":
        candidates[0]["evidence"][0]["unit_id"] = "unknown"
    elif failure == "quote":
        candidates[0]["evidence"][0]["quote"] = "Invented transaction log."
    elif failure == "term":
        candidates[0]["term"] = "Transaction log"
    else:
        terms = {"ungrounded": "偽"}
    with pytest.raises(PreparationValidationError) as error:
        run(
            tmp_path,
            workspace,
            candidates,
            answers_override={
                "collect": {"context": "Records.", "candidates": candidates},
                "organize": {"context": "Records.", "terms": terms},
            },
        )
    assert error.value.code == code
    records = [
        json.loads(path.read_text())
        for path in (workspace / "attempts/preparation").glob("*.json")
    ]
    rejected = [r for r in records if r["status"] == "rejected"]
    assert len(rejected) == 3
    assert all(r["validation_error"] == code for r in rejected)
    assert all(
        r["attempts"][0]["final"]["usage_status"] == "observed" for r in rejected
    )
    assert len((tmp_path / "requests.jsonl").read_text().splitlines()) == (
        4 if failure == "organization" else 3
    )
    assert not (workspace / "preparation-snapshot.json").exists()


def test_preparation_requests_japanese_context_and_literal_unit_evidence(tmp_path):
    workspace, candidates = source(tmp_path)
    run(tmp_path, workspace, candidates)
    requests = [
        json.loads(line)
        for line in (tmp_path / "requests.jsonl").read_text().splitlines()
    ]
    collect, organize = [request["messages"][0]["content"] for request in requests]
    for policy in (collect, organize):
        assert "English-to-Japanese translation" in policy
        assert "context" in policy and "Japanese" in policy
        assert "original spelling" in policy
        assert "spelling and case" in policy
    assert "candidate.translation in Japanese" in collect
    assert "terms values in Japanese" in organize
    assert (
        "evidence.quote must be an exact substring of source_text for its unit_id"
        in collect
    )
    assert "candidate.term must occur verbatim in that quote" in collect
    assert "paired inline markers only" in collect
    assert "heading_path is context only, not quote evidence" in collect
    assert "Never infer or reconstruct content hidden by standalone markers" in collect
    assert "do not remove those markers to join terms" in collect


@pytest.fixture(params=["pdf", "epub"])
def emphasized_source(tmp_path, request):
    if request.param == "pdf":
        from doc_translate.snapshot import load_source_snapshot

        workspace, _ = source(tmp_path)
        data = load_source_snapshot(workspace).model_dump(mode="json")
        data["blocks"][0].update(
            text="A transaction log records changes.",
            structure={
                "inline": [
                    {"kind": "text", "text": "A "},
                    {
                        "kind": "emphasis",
                        "children": [{"kind": "text", "text": "transaction"}],
                    },
                    {"kind": "text", "text": " log records changes."},
                ]
            },
        )
        snapshot = SourceSnapshot.model_validate(data)
    else:
        from test_epub import make_epub
        from test_epub_source import extract

        xhtml = b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>A <em>transaction</em> log records changes.</p></body></html>'
        snapshot = extract(make_epub(tmp_path, extra={"OPS/text/ch.xhtml": xhtml}))
        workspace = tmp_path / "workspace"
    save_source_snapshot(workspace, snapshot)
    unit = generate_translation_units(snapshot)[0]
    assert unit.source_text == "A ⟦1⟧transaction⟦/1⟧ log records changes."
    return workspace, unit


def test_preparation_adopts_compounds_across_emphasis_with_original_quotes(
    tmp_path, emphasized_source
):
    workspace, unit = emphasized_source
    candidates = [
        {
            "term": "transaction log",
            "translation": "取引ログ",
            "evidence": [{"unit_id": unit.unit_id, "quote": unit.source_text}],
        }
    ]
    digest = run(
        tmp_path,
        workspace,
        candidates,
        answers_override={
            "collect": {"context": "Records.", "candidates": candidates},
            "organize": {
                "context": "Records.",
                "terms": {"transaction log": "取引ログ"},
            },
        },
    )
    adopted = load_preparation(workspace, digest)
    assert load_preparation(workspace) == adopted
    assert adopted.glossary.terms == {"transaction log": "取引ログ"}
    evidence = json.loads(
        (
            workspace / "preparation/evidence" / f"{adopted.evidence_sha256}.json"
        ).read_bytes()
    )
    assert evidence["collections"][0]["candidates"] == candidates


@pytest.mark.parametrize(
    ("term", "quote"),
    [
        ("transaction log", "A transaction log records changes."),
        ("transaction log", "Invented ⟦1⟧transaction⟦/1⟧ log definition."),
        ("⟦1⟧", "A ⟦1⟧transaction⟦/1⟧ log records changes."),
    ],
)
def test_preparation_rejects_unmarked_or_fabricated_quotes_and_paired_marker_terms(
    tmp_path, emphasized_source, term, quote
):
    workspace, unit = emphasized_source
    candidates = [
        {
            "term": term,
            "translation": "不正な候補",
            "evidence": [{"unit_id": unit.unit_id, "quote": quote}],
        }
    ]
    with pytest.raises(ValueError, match="Term evidence does not match source text"):
        run(
            tmp_path,
            workspace,
            candidates,
            answers_override={
                "collect": {"context": "Records.", "candidates": candidates},
                "organize": {"context": "Records.", "terms": {term: "不正な候補"}},
            },
        )
    assert not (workspace / "preparation-snapshot.json").exists()


@pytest.mark.parametrize("source_format", ["pdf", "epub"])
@pytest.mark.parametrize("protected_kind", ["code", "formula", "literal"])
@pytest.mark.parametrize("term", ["transaction log", "transaction`hidden` log", "⟦=1⟧"])
def test_preparation_does_not_erase_or_expand_standalone_markers_for_terms(
    tmp_path, source_format, protected_kind, term
):
    if source_format == "pdf":
        from doc_translate.snapshot import load_source_snapshot

        workspace, _ = source(tmp_path)
        data = load_source_snapshot(workspace).model_dump(mode="json")
        data["blocks"][0].update(
            text="A transaction`hidden` log records changes.",
            structure={
                "inline": [
                    {"kind": "text", "text": "A transaction"},
                    {"kind": protected_kind, "text": "`hidden`"},
                    {"kind": "text", "text": " log records changes."},
                ]
            },
        )
        snapshot = SourceSnapshot.model_validate(data)
    else:
        from test_epub import make_epub
        from test_epub_source import extract

        protected = {
            "code": "<code>`hidden`</code>",
            "formula": '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>`hidden`</mi></math>',
            "literal": "`hidden`",
        }[protected_kind]
        xhtml = f'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>A transaction{protected} log records changes.</p></body></html>'.encode()
        snapshot = extract(make_epub(tmp_path, extra={"OPS/text/ch.xhtml": xhtml}))
        workspace = tmp_path / "workspace"
    save_source_snapshot(workspace, snapshot)
    unit = generate_translation_units(snapshot)[0]
    assert unit.source_text == "A transaction⟦=1⟧ log records changes."
    candidates = [
        {
            "term": term,
            "translation": "不正な候補",
            "evidence": [{"unit_id": unit.unit_id, "quote": unit.source_text}],
        }
    ]
    with pytest.raises(ValueError, match="Term evidence does not match source text"):
        run(
            tmp_path,
            workspace,
            candidates,
            answers_override={
                "collect": {"context": "Records.", "candidates": candidates},
                "organize": {"context": "Records.", "terms": {term: "不正な候補"}},
            },
        )
    assert not (workspace / "preparation-snapshot.json").exists()


def test_preparation_reads_sections_without_sending_protected_payloads(tmp_path):
    from test_epub import make_epub
    from test_epub_source import extract

    xhtml = b"""<html xmlns="http://www.w3.org/1999/xhtml"><head><title>SECRET METADATA</title></head><body><h1>Records</h1><p>A transaction log records changes. <code>SECRET INLINE CODE</code><math xmlns="http://www.w3.org/1998/Math/MathML"><mi>SECRET FORMULA</mi></math></p><pre>SECRET CODE BODY</pre><h1>Names</h1><p>Keep Nimbus unchanged.</p><img src="missing.png" alt="A diagram"/></body></html>"""
    snapshot = extract(make_epub(tmp_path, extra={"OPS/text/ch.xhtml": xhtml}))
    workspace = tmp_path / "workspace"
    save_source_snapshot(workspace, snapshot)
    units = generate_translation_units(snapshot)
    candidates = [
        {
            "term": "transaction log",
            "translation": "取引ログ",
            "evidence": [
                {
                    "unit_id": units[1].unit_id,
                    "quote": "A transaction log records changes.",
                }
            ],
        },
        {
            "term": "Nimbus",
            "translation": "Nimbus",
            "evidence": [
                {"unit_id": units[3].unit_id, "quote": "Keep Nimbus unchanged."}
            ],
        },
    ]
    digest = run(
        tmp_path,
        workspace,
        candidates,
        answers_override={
            "collect": [
                {"context": "Durable records.", "candidates": candidates[:1]},
                {"context": "Product naming.", "candidates": candidates[1:]},
            ],
            "organize": {
                "context": "Records and names.",
                "terms": {"transaction log": "取引ログ", "Nimbus": "Nimbus"},
            },
        },
    )
    adopted = load_preparation(workspace, digest)
    assert len(adopted.runs) == 3
    requests = [
        json.loads(line)
        for line in (tmp_path / "requests.jsonl").read_text().splitlines()
    ]
    inputs = [
        json.loads(request["messages"][1]["content"][0]["text"]) for request in requests
    ]
    sections = [json.loads(request["text"]) for request in inputs[:-1]]
    assert [[unit["source_text"] for unit in section] for section in sections] == [
        ["Records", "A transaction log records changes. ⟦=1⟧⟦=2⟧"],
        ["Names", "Keep Nimbus unchanged.", "A diagram", "⟦=1⟧"],
    ]
    sent = (tmp_path / "requests.jsonl").read_text()
    assert "SECRET" not in sent
    assert "<html" not in sent and "base64" not in sent
    assert all(
        part["type"] == "text"
        for request in requests
        for part in request["messages"][1]["content"]
    )
    evidence = json.loads(
        (
            workspace / "preparation/evidence" / f"{adopted.evidence_sha256}.json"
        ).read_bytes()
    )
    assert (
        evidence["sources"][units[1].unit_id]["block_id"] == snapshot.blocks[0].block_id
    )
    assert evidence["sources"][units[1].unit_id]["node_path"] == "/1/2/2"
    assert (
        evidence["sources"][units[1].unit_id]["locations"][0]["resource_path"]
        == "OPS/text/ch.xhtml"
    )


def rewrite_artifact(workspace, directory, value):
    import hashlib

    data = json.dumps(value, ensure_ascii=False).encode()
    digest = hashlib.sha256(data).hexdigest()
    (workspace / directory / f"{digest}.json").write_bytes(data)
    return digest


def test_load_preparation_revalidates_body_evidence_even_with_valid_hashes(tmp_path):
    import pytest

    workspace, candidates = source(tmp_path)
    digest = run(tmp_path, workspace, candidates)
    adopted = load_preparation(workspace, digest)
    evidence = json.loads(
        (
            workspace / "preparation/evidence" / f"{adopted.evidence_sha256}.json"
        ).read_bytes()
    )
    evidence["collections"][0]["candidates"][0]["evidence"][0]["quote"] = (
        "Invented transaction log definition."
    )
    evidence_digest = rewrite_artifact(workspace, "preparation/evidence", evidence)
    changed = adopted.model_dump(mode="json")
    changed["evidence_sha256"] = evidence_digest
    changed_digest = rewrite_artifact(workspace, "snapshots/preparation", changed)
    with pytest.raises(ValueError, match="evidence"):
        load_preparation(workspace, changed_digest)
    assert load_preparation(workspace) == adopted


def test_preparation_reuses_fixed_version_without_resending(tmp_path):
    workspace, candidates = source(tmp_path)
    digest = run(tmp_path, workspace, candidates)
    before = (tmp_path / "requests.jsonl").read_bytes()
    assert run(tmp_path, workspace, candidates) == digest
    assert (tmp_path / "requests.jsonl").read_bytes() == before
    assert (
        load_preparation(workspace).translation_inputs_sha256
        == load_preparation(workspace, digest).translation_inputs_sha256
    )


@pytest.mark.parametrize("repair", [False, True])
def test_preparation_catalog_registers_new_records_and_revalidates_each_operation(
    tmp_path, repair
):
    from test_performance_verification import operation

    from doc_translate import preparation

    workspace, candidates = source(tmp_path)
    correct = {
        "collect": {"context": "Records and naming.", "candidates": candidates},
        "organize": {
            "context": "変更記録を扱う人工文書です。",
            "terms": {"transaction log": "取引ログ", "Nimbus": "Nimbus"},
        },
    }
    answers = json.loads(json.dumps(correct))
    if repair:
        answers["collect"]["candidates"][0]["term"] = "Transaction log"

    def before_send(_config, request, _preflight):
        if "validation_feedback" in request.context:
            (tmp_path / "answers.json").write_text(json.dumps(correct))

    with operation.preparation_measurement(workspace, diagnostic=True) as fresh:
        digest = run(
            tmp_path,
            workspace,
            candidates,
            answers_override=answers,
            before_send=before_send,
        )
    # Subsequent groups must use newly saved raw records without reading them back.
    assert fresh["attempt_read_hash_counts"] == {}
    paths = list((workspace / "attempts/preparation").glob("*.json"))
    assert len(paths) == (3 if repair else 2)
    sent = (tmp_path / "requests.jsonl").read_bytes()
    with operation.preparation_measurement(workspace, diagnostic=True) as loaded:
        adopted = preparation.load_preparation(workspace)
        assert adopted.glossary.terms["Nimbus"] == "Nimbus"
    expected = {
        f"public_load/{kind}/{run_id}": 1
        for kind in ("read", "hash")
        for run_id in adopted.runs
    }
    assert {
        k: v
        for k, v in loaded["attempt_read_hash_counts"].items()
        if "/logical_bytes" not in k
    } == expected
    (workspace / "preparation-snapshot.json").unlink()
    with operation.preparation_measurement(workspace, diagnostic=True) as reused:
        assert run(tmp_path, workspace, candidates) == digest
    counts = reused["attempt_read_hash_counts"]
    assert reused["public_load_calls"] == 0
    reads = {
        k.rsplit("/", 1)[-1]: v
        for k, v in counts.items()
        if k.startswith("search_call/read/")
    }
    hashes = {
        k.rsplit("/", 1)[-1]: v
        for k, v in counts.items()
        if k.startswith("search_call/hash/")
    }
    assert reads == hashes and set(reads.values()) == {1}
    assert set(adopted.runs) <= set(reads) <= {path.stem for path in paths}
    assert (tmp_path / "requests.jsonl").read_bytes() == sent
    # Neither public load nor another prepare may inherit an earlier operation's memo.
    damaged = workspace / "attempts/preparation" / f"{adopted.runs[0]}.json"
    damaged.write_bytes(damaged.read_bytes() + b" ")
    with pytest.raises(ValueError, match="SHA-256"):
        load_preparation(workspace, digest)
    (workspace / "preparation-snapshot.json").unlink()
    with pytest.raises(ValueError, match="SHA-256"):
        run(tmp_path, workspace, candidates)
    assert (tmp_path / "requests.jsonl").read_bytes() == sent


def test_preparation_catalog_keeps_lexical_first_match_and_lazy_damage_boundary(
    tmp_path,
):
    from test_performance_verification import operation

    workspace, candidates = source(tmp_path)
    original = run(tmp_path, workspace, candidates)
    adopted = load_preparation(workspace)
    directory = workspace / "attempts/preparation"
    record = json.loads((directory / f"{adopted.runs[0]}.json").read_bytes())
    record["elapsed_seconds"] += 1
    duplicate = rewrite_artifact(workspace, "attempts/preparation", record)
    assert duplicate != adopted.runs[0]
    later = directory / f"{'f' * 64}.json"
    later.write_bytes(b"unreached damage")
    sent = (tmp_path / "requests.jsonl").read_bytes()
    with operation.preparation_measurement(workspace, diagnostic=True) as metrics:
        result = run(tmp_path, workspace, candidates)
    assert load_preparation(workspace, result).runs[0] == min(
        duplicate, adopted.runs[0]
    )
    assert not any("f" * 64 in key for key in metrics["attempt_read_hash_counts"])
    assert (tmp_path / "requests.jsonl").read_bytes() == sent
    assert load_preparation(workspace, original) == adopted
    pointer = (workspace / "preparation-snapshot.json").read_bytes()
    earlier = directory / f"{'0' * 64}.json"
    earlier.write_bytes(b"reached damage")
    with (
        operation.preparation_measurement(workspace, diagnostic=True) as rejected,
        pytest.raises(ValueError, match="SHA-256"),
    ):
        run(tmp_path, workspace, candidates)
    assert rejected["attempt_read_hash_counts"][f"search_call/read/{'0' * 64}"] == 1
    assert rejected["attempt_read_hash_counts"][f"search_call/hash/{'0' * 64}"] == 1
    assert (workspace / "preparation-snapshot.json").read_bytes() == pointer
    assert (
        earlier.read_bytes() == b"reached damage"
        and later.read_bytes() == b"unreached damage"
    )
    assert (tmp_path / "requests.jsonl").read_bytes() == sent


def test_method_change_does_not_change_fixed_translation_inputs(tmp_path):
    workspace, candidates = source(tmp_path)
    digest = run(tmp_path, workspace, candidates)
    adopted = load_preparation(workspace, digest)
    changed = adopted.model_dump(mode="json")
    changed["runs"] = []
    for run_digest in adopted.runs:
        record = json.loads(
            (workspace / "attempts/preparation" / f"{run_digest}.json").read_bytes()
        )
        record["method"] = "previous-preparation-method"
        changed["runs"].append(
            rewrite_artifact(workspace, "attempts/preparation", record)
        )
    old_method_digest = rewrite_artifact(workspace, "snapshots/preparation", changed)
    (workspace / "preparation-snapshot.json").write_text(
        json.dumps({"schema_version": 1, "sha256": old_method_digest})
    )
    assert (
        load_preparation(workspace).translation_inputs_sha256
        == adopted.translation_inputs_sha256
    )
    new_digest = run(tmp_path, workspace, candidates)
    assert new_digest != old_method_digest
    assert (
        load_preparation(workspace).translation_inputs_sha256
        == adopted.translation_inputs_sha256
    )
    assert load_preparation(workspace, digest) == adopted


def test_rejected_evidence_records_usage_and_keeps_old_adoption(tmp_path):
    import pytest

    workspace, candidates = source(tmp_path)
    digest = run(tmp_path, workspace, candidates)
    candidates[0]["evidence"][0]["quote"] = "Invented transaction log definition."
    with pytest.raises(ValueError, match="evidence"):
        run(
            tmp_path,
            workspace,
            candidates,
            terms={"new": "新版"},
            config=CONFIG.model_copy(
                update={"options": GenerationOptions(maxTokens=512)}
            ),
        )
    assert load_preparation(workspace) == load_preparation(workspace, digest)
    records = [
        json.loads(path.read_bytes())
        for path in (workspace / "attempts/preparation").glob("*.json")
    ]
    rejected = [record for record in records if record["status"] == "rejected"]
    assert len(rejected) == 3
    assert all(
        r["attempts"][0]["final"]["usage_status"] == "observed" for r in rejected
    )
    assert (
        "Invented transaction log definition."
        in rejected[0]["attempts"][0]["final"]["message"]["content"][0]["text"]
    )


def test_rejected_schema_records_raw_response_and_keeps_old_adoption(tmp_path):
    import pytest

    from doc_translate.inference import InferenceError

    workspace, candidates = source(tmp_path)
    digest = run(tmp_path, workspace, candidates)
    with pytest.raises(InferenceError, match="answer_invalid"):
        run(
            tmp_path,
            workspace,
            candidates,
            terms={"new": "新版"},
            config=CONFIG.model_copy(
                update={"options": GenerationOptions(maxTokens=512)}
            ),
            answers_override={"collect": {"unexpected": "raw artificial answer"}},
        )
    assert load_preparation(workspace) == load_preparation(workspace, digest)
    records = [
        json.loads(path.read_bytes())
        for path in (workspace / "attempts/preparation").glob("*.json")
    ]
    rejected = [record for record in records if record["status"] == "rejected"]
    assert len(rejected) == 1
    assert rejected[0]["attempts"][0]["code"] == "answer_invalid"
    assert (
        "raw artificial answer"
        in rejected[0]["attempts"][0]["final"]["message"]["content"][0]["text"]
    )


def test_section_context_reaches_document_organization_without_term_candidates(
    tmp_path,
):
    workspace, _ = source(tmp_path)
    digest = run(
        tmp_path,
        workspace,
        [],
        answers_override={
            "collect": {
                "context": "A chapter about durable records and product naming.",
                "candidates": [],
            },
            "organize": {"context": "永続記録と命名を扱います。", "terms": {}},
        },
    )
    assert load_preparation(workspace, digest).context == "永続記録と命名を扱います。"
    requests = [
        json.loads(line)
        for line in (tmp_path / "requests.jsonl").read_text().splitlines()
    ]
    organization_input = json.loads(requests[-1]["messages"][1]["content"][0]["text"])
    assert json.loads(organization_input["text"]) == [
        {
            "context": "A chapter about durable records and product naming.",
            "candidates": [],
        }
    ]


def test_preparation_records_every_retry_attempt_separately_from_translation(tmp_path):
    workspace, candidates = source(tmp_path)
    digest = run(
        tmp_path,
        workspace,
        candidates,
        answers_override={
            "retry": True,
            "collect": {"context": "Records and naming.", "candidates": candidates},
            "organize": {
                "context": "Document context.",
                "terms": {"transaction log": "ログ"},
            },
        },
    )
    adopted = load_preparation(workspace, digest)
    record = json.loads(
        (workspace / "attempts/preparation" / f"{adopted.runs[0]}.json").read_bytes()
    )
    assert len(record["attempts"]) == 2
    assert [attempt["code"] for attempt in record["attempts"]] == [
        "generation_failed",
        None,
    ]
    assert all(attempt["final"]["usage"]["input"] > 0 for attempt in record["attempts"])
    assert [attempt["final"]["usage_status"] for attempt in record["attempts"]] == [
        "observed",
        "observed",
    ]
    assert record["attempts"][0]["wait_seconds"] == 3
    assert record["attempts"][0]["final"]["retry"]["internal_retries"] == "unknown"


def test_preparation_rejects_unknown_sources_and_unconfirmed_terms(tmp_path):
    import pytest

    workspace, candidates = source(tmp_path)
    candidates[0]["evidence"][0]["unit_id"] = "unknown-unit"
    with pytest.raises(ValueError, match="evidence"):
        run(tmp_path, workspace, candidates)
    assert not (workspace / "preparation-snapshot.json").exists()
    candidates[0]["evidence"][0]["unit_id"] = "unit-1"
    with pytest.raises(ValueError, match="body evidence"):
        run(
            tmp_path,
            workspace,
            candidates,
            answers_override={
                "collect": {"context": "Records.", "candidates": candidates},
                "organize": {"context": "Records.", "terms": {"consensus": "合意"}},
            },
        )
    assert not (workspace / "preparation-snapshot.json").exists()


def test_preparation_preserves_old_adoption_when_atomic_replace_fails(
    tmp_path, monkeypatch
):
    import os

    import pytest

    workspace, candidates = source(tmp_path)
    digest = run(tmp_path, workspace, candidates)
    replace = os.replace

    def fail_reference(source, destination):
        if Path(destination) == workspace / "preparation-snapshot.json":
            raise OSError("Artificial reference replace failure")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_reference)
    with pytest.raises(OSError, match="Artificial"):
        run(tmp_path, workspace, candidates, terms={"new": "新版"})
    assert load_preparation(workspace) == load_preparation(workspace, digest)
    assert len(list((workspace / "attempts/preparation").glob("*.json"))) == 3


def test_preparation_load_verifies_all_dependency_hashes_and_adopted_inputs(tmp_path):
    import pytest

    workspace, candidates = source(tmp_path)
    digest = run(tmp_path, workspace, candidates)
    adopted = load_preparation(workspace, digest)
    for directory, dependency in [
        ("snapshots/preparation", digest),
        ("preparation/evidence", adopted.evidence_sha256),
        ("snapshots/source", adopted.source_snapshot_sha256),
        ("attempts/preparation", adopted.runs[0]),
    ]:
        path = workspace / directory / f"{dependency}.json"
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with pytest.raises(ValueError, match="SHA-256"):
            load_preparation(workspace, digest)
        path.write_bytes(original)
    changed = adopted.model_dump(mode="json")
    changed["glossary"]["terms"]["transaction log"] = "Unvalidated replacement"
    changed_digest = rewrite_artifact(workspace, "snapshots/preparation", changed)
    with pytest.raises(ValueError, match="adopted inputs"):
        load_preparation(workspace, changed_digest)
    assert load_preparation(workspace) == adopted


def test_preparation_honors_workspace_writer_lock(tmp_path):
    import pytest

    from doc_translate.workspace import WorkspaceLockedError, workspace_lock

    workspace, candidates = source(tmp_path)
    with workspace_lock(workspace), pytest.raises(WorkspaceLockedError):
        run(tmp_path, workspace, candidates)
    assert not (tmp_path / "requests.jsonl").exists()


def test_changed_style_and_user_terms_make_new_fixed_inputs_without_changing_source(
    tmp_path,
):
    workspace, candidates = source(tmp_path)
    original = run(tmp_path, workspace, candidates)
    previous = load_preparation(workspace, original)
    policy = StylePolicy(version="2", text="本文はですます調。見出しは短く記述します。")
    updated = run(
        tmp_path,
        workspace,
        candidates,
        policy=policy,
        terms={"transaction log": "トランザクションログ"},
        version="2",
    )
    current = load_preparation(workspace, updated)
    assert current.translation_inputs_sha256 != previous.translation_inputs_sha256
    assert current.source_snapshot_sha256 == previous.source_snapshot_sha256
    assert current.glossary.version == "2"
    assert current.policy == policy
    assert current.glossary.style == policy.text
    assert current.glossary.terms["transaction log"] == "トランザクションログ"
    assert load_preparation(workspace, original) == previous


def test_pdf_preparation_does_not_send_block_or_inline_code_and_formula(tmp_path):
    from doc_translate.snapshot import load_source_snapshot

    workspace, candidates = source(tmp_path)
    snapshot = load_source_snapshot(workspace).model_dump(mode="json")
    location = [{"format": "pdf", "page": 1, "evidence": "artificial"}]
    snapshot["blocks"].extend(
        [
            {
                "block_id": "code",
                "kind": "code",
                "text": "SECRET CODE BODY",
                "locations": location,
            },
            {
                "block_id": "formula",
                "kind": "formula",
                "text": "SECRET FORMULA",
                "locations": location,
            },
            {
                "block_id": "mixed",
                "kind": "body",
                "text": "Use SECRET INLINE CODE and SECRET INLINE FORMULA.",
                "locations": location,
                "structure": {
                    "inline": [
                        {"kind": "text", "text": "Use "},
                        {"kind": "code", "text": "SECRET INLINE CODE"},
                        {"kind": "text", "text": " and "},
                        {"kind": "formula", "text": "SECRET INLINE FORMULA"},
                        {"kind": "text", "text": "."},
                    ]
                },
            },
        ]
    )
    save_source_snapshot(workspace, SourceSnapshot.model_validate(snapshot))
    run(tmp_path, workspace, candidates)
    sent = (tmp_path / "requests.jsonl").read_text()
    assert "SECRET" not in sent
    request = json.loads(sent.splitlines()[0])
    source_input = json.loads(request["messages"][1]["content"][0]["text"])
    assert [unit["source_text"] for unit in json.loads(source_input["text"])] == [
        "A transaction log records changes. Keep Nimbus unchanged.",
        "Use ⟦=1⟧ and ⟦=2⟧.",
    ]


def test_organization_can_echo_user_only_terms_without_marking_body_evidence(tmp_path):
    workspace, candidates = source(tmp_path)
    digest = run(
        tmp_path,
        workspace,
        candidates,
        terms={"consensus": "利用者の訳語"},
        answers_override={
            "collect": {"context": "Records.", "candidates": candidates},
            "organize": {"context": "Records.", "terms": {"consensus": "モデルの訳語"}},
        },
    )
    adopted = load_preparation(workspace, digest)
    assert adopted.glossary.terms == {"consensus": "利用者の訳語"}
    evidence = json.loads(
        (
            workspace / "preparation/evidence" / f"{adopted.evidence_sha256}.json"
        ).read_bytes()
    )
    assert evidence["collections"][0]["candidates"][-1]["evidence"] == []


@pytest.mark.parametrize(
    "target", ["preparation/evidence", "preparation-snapshot.json"]
)
def test_preparation_recovers_validated_calls_before_adoption_without_resending(
    tmp_path, monkeypatch, target
):
    import os

    workspace, candidates = source(tmp_path)
    replace = os.replace

    def fail_replace(src, dst):
        if target in str(dst):
            raise OSError("interrupted adoption")
        return replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError, match="interrupted adoption"):
            run(tmp_path, workspace, candidates)
    sent = (tmp_path / "requests.jsonl").read_bytes()
    digest = run(tmp_path, workspace, candidates)
    assert load_preparation(workspace, digest).glossary.terms["Nimbus"] == "Nimbus"
    assert (tmp_path / "requests.jsonl").read_bytes() == sent


@pytest.mark.parametrize(
    "tamper",
    [
        "rejected",
        "incomplete",
        "terminal",
        "domain",
        "method",
        "schema",
        "policy",
        "hash",
    ],
)
def test_preparation_revalidates_orphan_calls_instead_of_trusting_status(
    tmp_path, monkeypatch, tamper
):
    import os

    workspace, candidates = source(tmp_path)
    replace = os.replace

    def fail_replace(src, dst):
        if "preparation/evidence" in str(dst):
            raise OSError("interrupted adoption")
        return replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_replace)
        with pytest.raises(OSError):
            run(tmp_path, workspace, candidates)
    path, record = next(
        (path, json.loads(path.read_bytes()))
        for path in (workspace / "attempts/preparation").glob("*.json")
        if json.loads(path.read_bytes())["request"]["context"]["phase"] == "collect"
    )
    sent = len((tmp_path / "requests.jsonl").read_text().splitlines())
    if tamper == "hash":
        path.write_bytes(path.read_bytes() + b" ")
        corrupt = path.read_bytes()
        with pytest.raises(ValueError, match="SHA-256"):
            run(tmp_path, workspace, candidates)
        assert path.read_bytes() == corrupt
        assert len((tmp_path / "requests.jsonl").read_text().splitlines()) == sent
        return
    # Retain the original artifact outside the candidate filename set.
    path.rename(path.with_suffix(".original"))
    if tamper == "rejected":
        record["status"] = "rejected"
    elif tamper == "incomplete":
        record["attempts"] = []
    elif tamper == "terminal":
        record["attempts"][-1]["final"]["terminal"]["reason"] = "length"
    elif tamper == "domain":
        bad = {
            "context": "Records",
            "candidates": [
                {
                    "term": "invented",
                    "translation": "偽",
                    "evidence": [{"unit_id": "unit-1", "quote": "invented"}],
                }
            ],
        }
        record["attempts"][-1]["final"]["message"]["content"] = [
            {"type": "text", "text": json.dumps(bad)}
        ]
    elif tamper == "method":
        record["method"] = "old-method"
    elif tamper == "schema":
        record["request"]["context"]["response_schema"] = {}
    else:
        record["request"]["policy"] = "old policy"
    rewrite_artifact(workspace, "attempts/preparation", record)
    digest = run(tmp_path, workspace, candidates)
    assert load_preparation(workspace, digest).glossary.terms["Nimbus"] == "Nimbus"
    assert len((tmp_path / "requests.jsonl").read_text().splitlines()) == sent + 1


def test_preparation_reuses_collection_after_organization_rejection(tmp_path):
    workspace, candidates = source(tmp_path)
    with pytest.raises(ValueError, match="body evidence"):
        run(
            tmp_path,
            workspace,
            candidates,
            answers_override={
                "collect": {"context": "Records and naming.", "candidates": candidates},
                "organize": {"context": "Records", "terms": {"ungrounded": "偽"}},
            },
        )
    sent = len((tmp_path / "requests.jsonl").read_text().splitlines())
    digest = run(tmp_path, workspace, candidates)
    assert load_preparation(workspace, digest).glossary.terms["Nimbus"] == "Nimbus"
    assert len((tmp_path / "requests.jsonl").read_text().splitlines()) == sent + 1


def id_source(
    tmp_path, source_format, *, prefix=False, heading="Beta", body="queue queue."
):
    """Two equal bodies with distinct heading contexts; positions follow reading order."""
    if source_format == "epub":
        from test_epub import make_epub
        from test_epub_source import extract

        content = "<h1>Preface</h1><p>queue queue.</p>" if prefix else ""
        content += f"<h1>Alpha</h1><p>queue queue.</p><h1>{heading}</h1><p>{body}</p>"
        return extract(
            make_epub(
                tmp_path,
                extra={
                    "OPS/text/ch.xhtml": f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{content}</body></html>'.encode()
                },
            )
        )
    from test_units import pdf_snapshot

    sections = (["Preface"] if prefix else []) + ["Alpha", heading]
    return pdf_snapshot(
        [
            {
                "block_id": f"block-{index}-{kind}",
                "kind": kind,
                "text": text,
                **({"structure": {"level": 1}} if kind == "heading" else {}),
            }
            for index, title in enumerate(sections)
            for kind, text in [
                ("heading", title),
                ("body", body if title == heading else "queue queue."),
            ]
        ]
    )


def id_answers(snapshot):
    from doc_translate.preparation import _groups

    return {
        "collect": [
            {
                "context": "人工節。",
                "candidates": [
                    {
                        "term": "queue",
                        "translation": "キュー",
                        "evidence": [
                            {"unit_id": group[-1].unit_id, "quote": "queue"},
                            {
                                "unit_id": group[-1].unit_id,
                                "quote": group[-1].source_text,
                            },
                        ],
                    },
                    {"term": "ungrounded", "translation": "一般知識", "evidence": []},
                ],
            }
            for group in _groups(generate_translation_units(snapshot))
        ],
        "organize": {"context": "人工文書。", "terms": {"queue": "自動"}},
    }


@pytest.mark.parametrize("source_format", ["pdf", "epub"])
def test_preparation_v2_rebinds_shifted_groups_and_keeps_real_context_misses(
    tmp_path, source_format
):
    workspace = tmp_path / "workspace"
    original = id_source(tmp_path, source_format)
    save_source_snapshot(workspace, original)
    first = run(
        tmp_path,
        workspace,
        [],
        answers_override=id_answers(original),
        terms={"queue": "利用者"},
    )
    old = load_preparation(workspace, first)
    immutable = {
        p: p.read_bytes()
        for directory in ["attempts", "preparation", "snapshots"]
        for p in (workspace / directory).rglob("*.json")
    }
    shifted = id_source(tmp_path, source_format, prefix=True)
    save_source_snapshot(workspace, shifted)
    notices = []
    current = run(
        tmp_path,
        workspace,
        [],
        answers_override=id_answers(shifted),
        terms={"queue": "利用者"},
        before_send=lambda c, r, p: notices.append(r),
    )
    adopted = load_preparation(workspace, current)
    # New Preface and changed preceding heading context for Alpha miss; Beta reuses.
    assert [r.context["phase"] for r in notices] == ["collect", "collect", "organize"]
    assert adopted.runs[2] == old.runs[1]
    assert adopted.glossary.terms == {"queue": "利用者"}
    assert adopted.translation_inputs_sha256 == old.translation_inputs_sha256
    evidence = json.loads(
        (
            workspace / "preparation/evidence" / f"{adopted.evidence_sha256}.json"
        ).read_bytes()
    )
    units = generate_translation_units(shifted)
    assert evidence["wire_version"] == 2
    assert evidence["bindings"][-1] == [unit.unit_id for unit in units[-2:]]
    assert evidence["collections"][-1]["candidates"][0]["evidence"] == [
        {"unit_id": units[-1].unit_id, "quote": "queue"},
        {"unit_id": units[-1].unit_id, "quote": "queue queue."},
    ]
    raw = json.loads(
        (workspace / "attempts/preparation" / f"{adopted.runs[2]}.json").read_bytes()
    )
    assert raw["wire_version"] == raw["request"]["context"]["wire_version"] == 2
    wire = json.loads(raw["request"]["text"])
    assert [u["unit_id"] for u in wire] == ["request-unit-1", "request-unit-2"]
    assert "section" not in raw["request"]["context"]
    assert wire[-1]["heading_path"] == [{"level": 1, "text": "Beta"}]
    organize = json.loads(
        (workspace / "attempts/preparation" / f"{adopted.runs[-1]}.json").read_bytes()
    )
    assert all(
        e["unit_id"] == "request-unit-2"
        for c in json.loads(organize["request"]["text"])
        for e in c["candidates"][0]["evidence"]
    )
    assert all(path.read_bytes() == data for path, data in immutable.items())
    assert load_preparation(workspace, first) == old
    for change in [{"heading": "Changed"}, {"body": "queue changed."}]:
        changed = id_source(tmp_path, source_format, prefix=True, **change)
        save_source_snapshot(workspace, changed)
        notices.clear()
        run(
            tmp_path,
            workspace,
            [],
            answers_override=id_answers(changed),
            terms={"queue": "利用者"},
            before_send=lambda c, r, p: notices.append(r),
        )
        assert any(
            r.context["phase"] == "collect"
            and json.loads(r.text)[-1]["source_text"]
            == (change.get("body", "queue queue."))
            for r in notices
        )


def test_preparation_v2_organize_reuses_raw_aliases_not_shifted_tracking_ids(tmp_path):
    from test_units import pdf_snapshot

    workspace = tmp_path / "workspace"
    original = id_source(tmp_path, "pdf")
    save_source_snapshot(workspace, original)
    first = run(tmp_path, workspace, [], answers_override=id_answers(original))
    old = load_preparation(workspace, first)
    # Add a unit to Alpha: Beta IDs shift, but both raw collections stay the same.
    shifted = pdf_snapshot(
        [
            *[block.model_dump(mode="json") for block in original.blocks[:2]],
            {"block_id": "additional", "kind": "body", "text": "Additional source."},
            *[
                {**block.model_dump(mode="json"), "block_id": f"shifted-{i}"}
                for i, block in enumerate(original.blocks[2:])
            ],
        ]
    )
    save_source_snapshot(workspace, shifted)
    answers = id_answers(shifted)
    answers["collect"][0] = id_answers(original)["collect"][0]
    notices = []
    current = run(
        tmp_path,
        workspace,
        [],
        answers_override=answers,
        before_send=lambda c, r, p: notices.append(r),
    )
    adopted = load_preparation(workspace, current)
    assert [r.context["phase"] for r in notices] == ["collect"]
    assert adopted.runs[1:] == old.runs[1:]
    evidence = json.loads(
        (
            workspace / "preparation/evidence" / f"{adopted.evidence_sha256}.json"
        ).read_bytes()
    )
    assert (
        evidence["collections"][1]["candidates"][0]["evidence"][0]["unit_id"]
        == "unit-5"
    )


@pytest.mark.parametrize(
    "tamper",
    [
        "binding-order",
        "binding-duplicate",
        "evidence-version",
        "run-version",
        "request-version",
        "alias",
        "unit-order",
        "alias-duplicate",
        "quote",
        "organize",
    ],
)
def test_load_preparation_v2_rejects_rehashed_binding_raw_and_version_tampering(
    tmp_path, tamper
):
    workspace = tmp_path / "workspace"
    snapshot = id_source(tmp_path, "pdf")
    save_source_snapshot(workspace, snapshot)
    digest = run(tmp_path, workspace, [], answers_override=id_answers(snapshot))
    adopted = load_preparation(workspace, digest)
    saved = adopted.model_dump(mode="json")
    evidence = json.loads(
        (
            workspace / "preparation/evidence" / f"{adopted.evidence_sha256}.json"
        ).read_bytes()
    )
    if tamper.startswith("binding"):
        evidence["bindings"][0] = (
            list(reversed(evidence["bindings"][0]))
            if tamper == "binding-order"
            else [evidence["bindings"][0][0]] * 2
        )
    elif tamper == "evidence-version":
        evidence["wire_version"] = 999
    else:
        index = -1 if tamper == "organize" else 0
        record = json.loads(
            (
                workspace / "attempts/preparation" / f"{adopted.runs[index]}.json"
            ).read_bytes()
        )
        if tamper == "run-version":
            record["wire_version"] = 999
        elif tamper == "request-version":
            record["request"]["context"]["wire_version"] = 999
        elif tamper in ("unit-order", "alias-duplicate"):
            projection = json.loads(record["request"]["text"])
            if tamper == "unit-order":
                projection.reverse()
            else:
                projection[1]["unit_id"] = projection[0]["unit_id"]
            record["request"]["text"] = json.dumps(projection)
        elif tamper == "organize":
            collections = json.loads(record["request"]["text"])
            collections[0]["candidates"][0]["evidence"][0]["unit_id"] = "unit-2"
            record["request"]["text"] = json.dumps(collections)
        else:
            message = record["attempts"][-1]["final"]["message"]
            raw = json.loads(message["content"][0]["text"])
            raw["candidates"][0]["evidence"][0][
                "unit_id" if tamper == "alias" else "quote"
            ] = "unit-2" if tamper == "alias" else "invented queue"
            message["content"][0]["text"] = json.dumps(raw)
        saved["runs"][index] = rewrite_artifact(
            workspace, "attempts/preparation", record
        )
    saved["evidence_sha256"] = rewrite_artifact(
        workspace, "preparation/evidence", evidence
    )
    bad = rewrite_artifact(workspace, "snapshots/preparation", saved)
    with pytest.raises(ValueError):
        load_preparation(workspace, bad)
    assert load_preparation(workspace, digest) == adopted


def test_preparation_loads_byte_original_v1_and_only_generates_missing_v2(tmp_path):
    # Captured from ec49420 before v2 existed, not a downgraded v2 fixture.
    fixture = json.loads((ROOT / "tests/fixtures/preparation-v1.json").read_bytes())
    workspace = tmp_path / "workspace"
    for name, data in fixture.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
    old = load_preparation(workspace)
    digest = json.loads(fixture["preparation-snapshot.json"])["sha256"]
    assert (
        prepare_document(
            workspace=workspace,
            book=old.glossary.book,
            policy=old.policy,
            config=CONFIG,
            process_command=["must-not-launch"],
        )
        == digest
    )
    snapshot = load_source_snapshot(workspace)
    answers = {
        "collect": {"context": "Missing section.", "candidates": []},
        "organize": {"context": old.context, "terms": {"queue": "キュー"}},
    }
    assert all((workspace / name).read_text() == data for name, data in fixture.items())
    # Change one body, retaining every original v1 byte and the other exact group.
    changed = snapshot.model_dump(mode="json")
    changed["blocks"][1]["text"] += " Additional source."
    snapshot = SourceSnapshot.model_validate(changed)
    save_source_snapshot(workspace, snapshot)
    notices = []
    mixed = run(
        tmp_path,
        workspace,
        [],
        answers_override=answers,
        before_send=lambda c, r, p: notices.append(r),
    )
    result = load_preparation(workspace, mixed)
    versions = [
        json.loads((workspace / "attempts/preparation" / f"{r}.json").read_bytes()).get(
            "wire_version", 1
        )
        for r in result.runs
    ]
    assert versions == [2, 1, 2]
    assert [r.context["phase"] for r in notices] == ["collect", "organize"]
    assert result.glossary.terms == {"queue": "キュー"}
    for name, data in fixture.items():
        if name.endswith(".json") and "/" in name:
            assert (workspace / name).read_text() == data
    # A front addition cannot transfer the remaining v1 global IDs into aliases.
    shifted = snapshot.model_dump(mode="json")
    shifted["blocks"].insert(
        0,
        {
            "block_id": "prefix",
            "kind": "body",
            "text": "Prefix",
            "locations": snapshot.blocks[0].locations,
        },
    )
    save_source_snapshot(workspace, SourceSnapshot.model_validate(shifted))
    notices.clear()
    answers["organize"]["terms"] = {}
    run(
        tmp_path,
        workspace,
        [],
        answers_override=answers,
        before_send=lambda c, r, p: notices.append(r),
    )
    assert sum(r.context["phase"] == "collect" for r in notices) == 2


def test_preparation_v2_reuses_identical_wire_with_distinct_snapshot_bindings(tmp_path):
    from test_units import pdf_snapshot

    original = id_source(tmp_path, "pdf")
    data = [block.model_dump(mode="json") for block in original.blocks]
    data.extend(
        [
            {**block.model_dump(mode="json"), "block_id": f"third-{i}"}
            for i, block in enumerate(original.blocks[2:])
        ]
    )
    snapshot = pdf_snapshot(data)
    workspace = tmp_path / "workspace"
    save_source_snapshot(workspace, snapshot)
    notices = []
    digest = run(
        tmp_path,
        workspace,
        [],
        answers_override=id_answers(snapshot),
        before_send=lambda c, r, p: notices.append(r),
    )
    adopted = load_preparation(workspace, digest)
    assert adopted.runs[1] != adopted.runs[2]  # Beta heading itself still sees Alpha.
    # A fourth Beta has the same heading context as the third, with different IDs.
    data.extend(
        [
            {**block.model_dump(mode="json"), "block_id": f"fourth-{i}"}
            for i, block in enumerate(original.blocks[2:])
        ]
    )
    snapshot = pdf_snapshot(data)
    save_source_snapshot(workspace, snapshot)
    notices.clear()
    digest = run(
        tmp_path,
        workspace,
        [],
        answers_override=id_answers(snapshot),
        before_send=lambda c, r, p: notices.append(r),
    )
    adopted = load_preparation(workspace, digest)
    assert [r.context["phase"] for r in notices] == ["organize"]
    assert adopted.runs[2] == adopted.runs[3]
    evidence = json.loads(
        (
            workspace / "preparation/evidence" / f"{adopted.evidence_sha256}.json"
        ).read_bytes()
    )
    assert [
        collection["candidates"][0]["evidence"][0]["unit_id"]
        for collection in evidence["collections"]
    ] == ["unit-2", "unit-4", "unit-6", "unit-8"]


def parallel_source(tmp_path, source_format="pdf", *, size=5, same=False):
    """Independent self-authored sections, through the real EPUB reader when selected."""
    titles = ["Same" if same else f"Section group{i}" for i in range(size)]
    if source_format == "epub":
        from test_epub import make_epub
        from test_epub_source import extract

        content = "".join(f"<h1>{title}</h1><p>queue queue.</p>" for title in titles)
        return extract(
            make_epub(
                tmp_path,
                extra={
                    "OPS/text/ch.xhtml": f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{content}</body></html>'.encode()
                },
            )
        )
    from test_units import pdf_snapshot

    return pdf_snapshot(
        [
            {
                "block_id": f"block-{i}-{kind}",
                "kind": kind,
                "text": text,
                **({"structure": {"level": 1}} if kind == "heading" else {}),
            }
            for i, title in enumerate(titles)
            for kind, text in [("heading", title), ("body", "queue queue.")]
        ]
    )


def timeline_events(path):
    return sorted(
        [json.loads(line) for line in path.read_text().splitlines()],
        key=lambda event: int(event["ns"]),
    )


@pytest.mark.parametrize("source_format", ["pdf", "epub"])
def test_preparation_parallel_reverse_completion_preserves_group_and_run_order(
    tmp_path, source_format
):
    import threading

    snapshot = parallel_source(tmp_path, source_format, size=4)
    workspace = tmp_path / "workspace"
    save_source_snapshot(workspace, snapshot)
    answers = id_answers(snapshot)
    for i, answer in enumerate(answers["collect"]):
        answer["context"] = f"Section {i}"
    timeline = tmp_path / "timeline.jsonl"
    answers["observation"] = {
        "delay_ms": [900, 600, 300, 0],
        "timeline": str(timeline),
    }
    barrier = threading.Barrier(4, timeout=10)

    def before_send(_config, request, _preflight):
        if request.context["phase"] == "collect":
            barrier.wait()

    digest = run(
        tmp_path,
        workspace,
        [],
        concurrency=4,
        answers_override=answers,
        before_send=before_send,
    )
    adopted = load_preparation(workspace, digest)
    evidence = json.loads(
        (
            workspace / "preparation/evidence" / f"{adopted.evidence_sha256}.json"
        ).read_text()
    )
    assert [answer["context"] for answer in evidence["collections"]] == [
        f"Section {i}" for i in range(4)
    ]
    records = [
        json.loads((workspace / "attempts/preparation" / f"{run_id}.json").read_text())
        for run_id in adopted.runs
    ]
    assert [
        json.loads(record["request"]["text"])[0]["source_text"]
        for record in records[:-1]
    ] == [f"Section group{i}" for i in range(4)]
    assert [item["context"] for item in json.loads(records[-1]["request"]["text"])] == [
        f"Section {i}" for i in range(4)
    ]
    events = timeline_events(timeline)
    assert [
        e["section"] for e in events if e["phase"] == "collect" and e["event"] == "end"
    ] == [3, 2, 1, 0]
    assert [e["event"] for e in events if e["phase"] == "organize"] == ["start", "end"]
    assert events[-2]["phase"] == "organize"
    active = peak = 0
    for event in events:
        active += 1 if event["event"] == "start" else -1
        peak = max(peak, active)
    assert active == 0 and 1 < peak <= 4


@pytest.mark.parametrize("failed", [False, True])
def test_preparation_parallel_same_wire_miss_shares_raw_success_or_failure(
    tmp_path, monkeypatch, failed
):
    import threading

    from doc_translate import preparation
    from doc_translate.inference import InferenceError
    from doc_translate.render import _usage

    snapshot = parallel_source(tmp_path, size=4, same=True)
    workspace = tmp_path / "workspace"
    save_source_snapshot(workspace, snapshot)
    answers = id_answers(snapshot)
    answers["observation"] = {"delay_ms": 100, "fail_sections": [1] if failed else []}
    original = preparation._AttemptCatalog.flight
    barrier = threading.Barrier(4, timeout=10)
    flights = []

    def flight(self, request, config):
        if request.context["phase"] == "collect":
            barrier.wait()
        result = original(self, request, config)
        flights.append(result[0])
        return result

    monkeypatch.setattr(preparation._AttemptCatalog, "flight", flight)
    if failed:
        with pytest.raises(InferenceError):
            run(tmp_path, workspace, [], answers_override=answers, concurrency=4)
        assert not (workspace / "preparation-snapshot.json").exists()
    else:
        digest = run(tmp_path, workspace, [], answers_override=answers, concurrency=4)
        adopted = load_preparation(workspace, digest)
        assert adopted.runs[1] == adopted.runs[2] == adopted.runs[3]
        evidence = json.loads(
            (
                workspace / "preparation/evidence" / f"{adopted.evidence_sha256}.json"
            ).read_text()
        )
        assert [
            answer["candidates"][0]["evidence"][0]["unit_id"]
            for answer in evidence["collections"]
        ] == [f"unit-{i}" for i in (2, 4, 6, 8)]
    assert all(future.done() for future in flights)
    requests = [
        json.loads(json.loads(line)["messages"][1]["content"][0]["text"])
        for line in (tmp_path / "requests.jsonl").read_text().splitlines()
    ]
    assert sum(r["context"]["phase"] == "collect" for r in requests) == 2
    assert sum(r["context"]["phase"] == "organize" for r in requests) == (
        0 if failed else 1
    )
    assert len(_usage(workspace)["stages"]["preparation"]) == len(requests)


@pytest.mark.parametrize("failure", ["local", "cancel", "save"])
def test_preparation_parallel_stop_drains_and_recovers_saved_success(
    tmp_path, monkeypatch, failure
):
    import os
    import threading

    from doc_translate import preparation
    from doc_translate.inference import InferenceError

    flights = []
    original_flight = preparation._AttemptCatalog.flight

    def flight(self, request, config):
        result = original_flight(self, request, config)
        flights.append(result[0])
        return result

    monkeypatch.setattr(preparation._AttemptCatalog, "flight", flight)
    snapshot = parallel_source(tmp_path)
    workspace = tmp_path / "workspace"
    save_source_snapshot(workspace, snapshot)
    answers = id_answers(snapshot)
    old = run(tmp_path, workspace, [], answers_override=answers)
    pointer = (workspace / "preparation-snapshot.json").read_bytes()
    config = CONFIG.model_copy(update={"options": GenerationOptions(temperature=0.2)})
    cancelled = threading.Event()
    answers["observation"] = {
        "delay_ms": [0, 800, 0, 0, 0],
        "fail_sections": [2] if failure == "local" else [],
        "timeline": str(tmp_path / "timeline.jsonl"),
    }
    replace = os.replace

    def fail_replace(src, dst):
        if failure == "save" and Path(dst).parent == workspace / "attempts/preparation":
            value = json.loads(Path(src).read_bytes())
            if "Section group2" in value["request"]["text"]:
                raise OSError("artificial immutable save failure")
        return replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_replace)

    def observer(event):
        if failure == "cancel" and event.kind == "saved":
            cancelled.set()

    with pytest.raises((InferenceError, OSError)):
        run(
            tmp_path,
            workspace,
            [],
            config=config,
            concurrency=2,
            answers_override=answers,
            cancel=cancelled,
            observer=observer,
        )
    assert (workspace / "preparation-snapshot.json").read_bytes() == pointer
    assert all(future.done() for future in flights)
    assert load_preparation(workspace, old)
    immutable = {
        path: path.read_bytes()
        for path in workspace.rglob("*.json")
        if path.parent.name not in {"workspace"}
    }
    events = timeline_events(tmp_path / "timeline.jsonl")
    assert all(event["phase"] == "collect" for event in events)
    assert {e["section"] for e in events} <= {0, 1, 2}
    assert {(e["pid"], e["section"]) for e in events if e["event"] == "start"} == {
        (e["pid"], e["section"]) for e in events if e["event"] == "end"
    }
    successful = [
        json.loads(p.read_text())
        for p in (workspace / "attempts/preparation").glob("*.json")
        if json.loads(p.read_text())["config"] == config.model_dump(mode="json")
        and json.loads(p.read_text())["status"] == "validated"
    ]
    assert successful
    prior_count = len((tmp_path / "requests.jsonl").read_text().splitlines())
    monkeypatch.setattr(os, "replace", replace)
    answers.pop("observation")
    recovered = run(
        tmp_path, workspace, [], config=config, concurrency=2, answers_override=answers
    )
    assert (
        load_preparation(workspace, recovered).translation_inputs_sha256
        == load_preparation(workspace, old).translation_inputs_sha256
    )
    assert all(path.read_bytes() == data for path, data in immutable.items())
    assert len(
        (tmp_path / "requests.jsonl").read_text().splitlines()
    ) - prior_count == 6 - len(successful)


@pytest.mark.parametrize("concurrency", [0, 3, 8, True, "2"])
def test_preparation_rejects_invalid_concurrency_before_sending(tmp_path, concurrency):
    workspace, candidates = source(tmp_path)
    with pytest.raises(ValueError, match="concurrency"):
        run(tmp_path, workspace, candidates, concurrency=concurrency)
    assert not (tmp_path / "requests.jsonl").exists()


def _preparation_signal_child(directory, concurrency):
    """Subprocess entry: keep real generation, validation and saving under signal."""
    import os
    import signal
    from contextlib import nullcontext

    from doc_translate.inference import InferenceError
    from doc_translate.run import _cancellation

    owned = Path(directory)
    workspace = owned / "workspace"
    snapshot = parallel_source(owned, size=5)
    save_source_snapshot(workspace, snapshot)
    answers = id_answers(snapshot)

    def previous_handler(_signum, _frame):
        raise AssertionError("prepare did not intercept the parent signal")

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, previous_handler)
    old = run(owned, workspace, [], answers_override=answers)
    assert all(
        signal.getsignal(signum) is previous_handler
        for signum in (signal.SIGINT, signal.SIGTERM)
    )
    pointer = (workspace / "preparation-snapshot.json").read_bytes()
    immutable = {
        path: path.read_bytes()
        for directory in ("snapshots", "attempts", "preparation")
        for path in (workspace / directory).rglob("*.json")
    }
    config = CONFIG.model_copy(update={"options": GenerationOptions(temperature=0.2)})
    timeline = owned / "timeline.jsonl"
    answers["observation"] = {"delay_ms": 1500, "timeline": str(timeline)}
    # The parallel case also checks run/resume's caller-owned Event and handlers.
    with _cancellation() if concurrency == 4 else nullcontext() as cancelled:
        handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        try:
            run(
                owned,
                workspace,
                [],
                config=config,
                cancel=cancelled,
                answers_override=answers,
                **({"concurrency": concurrency} if concurrency != 1 else {}),
            )
        except InferenceError as error:
            assert error.code == "cancelled"
        else:
            raise AssertionError("parent signal did not cancel preparation")
        assert all(signal.getsignal(s) is handler for s, handler in handlers.items())
        if cancelled is not None:
            assert cancelled.is_set()
    assert all(
        signal.getsignal(signum) is previous_handler
        for signum in (signal.SIGINT, signal.SIGTERM)
    )
    assert (workspace / "preparation-snapshot.json").read_bytes() == pointer
    assert all(path.read_bytes() == data for path, data in immutable.items())
    assert load_preparation(workspace, old)
    attempts = [
        json.loads(path.read_text())
        for path in (workspace / "attempts/preparation").glob("*.json")
        if path not in immutable
    ]
    assert len(attempts) == concurrency
    assert all(
        record["status"] == "rejected"
        and record["request"]["context"]["phase"] == "collect"
        and len(record["attempts"]) == 1
        and record["attempts"][0]["code"] == "cancelled"
        for record in attempts
    )
    events = timeline_events(timeline)
    assert len([e for e in events if e["event"] == "start"]) == concurrency
    assert {(e["pid"], e["section"]) for e in events if e["event"] == "start"} == {
        (e["pid"], e["section"]) for e in events if e["event"] == "end"
    }
    assert all(e["phase"] == "collect" for e in events)
    for pid in {e["pid"] for e in events}:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    with pytest.raises(ChildProcessError):
        os.waitpid(-1, os.WNOHANG)
    print(json.dumps({"error": "cancelled", "saved_attempts": len(attempts)}))


@pytest.mark.parametrize(
    "concurrency,termination", [(1, "SIGTERM"), (4, "SIGTERM"), (4, "SIGINT")]
)
def test_preparation_parent_signal_drains_and_restores_handlers(
    tmp_path, concurrency, termination
):
    import os
    import signal
    import subprocess
    import sys
    import tempfile
    import time

    with tempfile.TemporaryDirectory(
        prefix="prepare-signal-", dir=tmp_path
    ) as temporary:
        owned = Path(temporary)
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "from test_preparation import _preparation_signal_child; "
                    f"_preparation_signal_child({temporary!r}, {concurrency})"
                ),
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "tests")},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            timeline = owned / "timeline.jsonl"
            deadline = time.monotonic() + 20
            while True:
                assert process.poll() is None, process.communicate()
                if timeline.exists():
                    events = timeline_events(timeline)
                    if sum(e["event"] == "start" for e in events) == concurrency:
                        break
                assert time.monotonic() < deadline, "real faux callbacks did not start"
                time.sleep(0.01)
            # Do not signal the group: only the API parent may cancel its helpers.
            process.send_signal(getattr(signal, termination))
            stdout, stderr = process.communicate(timeout=45)
            assert process.returncode == 0, (stdout, stderr)
            assert json.loads(stdout) == {
                "error": "cancelled",
                "saved_attempts": concurrency,
            }
        finally:
            try:
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=45)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_preparation_non_main_thread_preserves_caller_handlers(tmp_path):
    import signal
    import threading
    from concurrent.futures import ThreadPoolExecutor

    workspace, candidates = source(tmp_path)
    handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    cancelled = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        digest = executor.submit(
            run, tmp_path, workspace, candidates, cancel=cancelled, concurrency=4
        ).result(timeout=30)
    assert load_preparation(workspace, digest)
    assert not cancelled.is_set()
    assert all(signal.getsignal(s) is handler for s, handler in handlers.items())

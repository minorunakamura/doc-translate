"""Saved source -> real offline helper -> fixed, evidenced preparation."""

import json
import shutil
from pathlib import Path

import pytest

from doc_translate.inference import GenerationConfig, GenerationOptions
from doc_translate.preparation import StylePolicy, load_preparation, prepare_document
from doc_translate.snapshot import SourceSnapshot, save_source_snapshot
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
    assert len(rejected) == 1
    assert rejected[0]["attempts"][0]["final"]["usage_status"] == "observed"
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

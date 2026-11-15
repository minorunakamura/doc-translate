"""Fixed evaluation preparation and connection checks; no real runtime/auth/AI."""

import json
import subprocess
import sys
from pathlib import Path

import pdf_source_evaluation as evaluation
import pytest
from pdf_source_feasibility import finish, inventory, sha, write_json
from pdf_source_offline import CONFIG, ArtificialProcess
from pdf_source_samples import FAMILIES
from pdf_source_trial import SendBudget, trial

from doc_translate.pdf_correction import load_pdf_correction
from doc_translate.pdf_source import assemble_pdf_source
from doc_translate.snapshot import SourceSnapshot

pytest_plugins = ["test_pdf_source_feasibility"]


@pytest.fixture
def prepared(samples, tmp_path, monkeypatch):
    workspace = samples / "synthetic-inputs/sample-01"
    fixed = json.loads((workspace / "current-source.json").read_bytes())
    digest = fixed["blocks"][0]["structure"]["fragments"][0]["chunk_sha256"]
    chunk = load_pdf_correction(workspace, digest)
    expected = tmp_path / "expectations.json"
    write_json(expected, {"non_send_canary": "NEVER_INCLUDE_EXPECTATIONS"})
    old = {}
    for i, count in enumerate((1, 3)):
        path = tmp_path / f"old-{i}.json"
        write_json(path, [{}] * count)
        old[str(path)] = sha(path.read_bytes())
    for key, value in {
        "AUTHORIZATION": tmp_path,
        "CONTINUATION": tmp_path / "continuation",
        "SAMPLE": workspace,
        "SOURCE_SHA": sha((workspace / "current-source.json").read_bytes()),
        "BASELINE_SHA": sha(
            assemble_pdf_source(
                workspace=workspace,
                chunk_sha256s=[digest],
                snapshot_id="issue80-baseline",
            )
            .model_dump_json()
            .encode()
        ),
        "CHUNK": digest,
        "IMAGES": [p.image.sha256 for p in chunk.page_images],
        "EXPECTATIONS": expected,
        "EXPECTATIONS_SHA": sha(expected.read_bytes()),
        "CONFIG_SHA": sha(CONFIG.model_dump_json().encode()),
        "OLD_SENDS": old,
    }.items():
        monkeypatch.setattr(evaluation, key, value)

    def forbidden(*args, **kwargs):
        pytest.fail("Preparation connected to real runtime/auth/inference")

    with monkeypatch.context() as guard:
        guard.setattr("doc_translate.ai_runtime.installed_runtime", forbidden)
        guard.setattr("pdf_source_trial.generate", forbidden)
        output = evaluation.prepare(
            tmp_path / "proposal",
            CONFIG,
            subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        )
    evaluation.initialize_continuation(CONFIG)
    return output


def faux_connection(prepared, tmp_path, monkeypatch):
    case = json.loads((prepared / "proposal.json").read_bytes())["cases"][0]
    chunk = load_pdf_correction(prepared / "sample-01/input", case["chunk_sha256s"][0])
    controls = tmp_path / "owned-faux"
    controls.mkdir()

    def command(label, *args):
        # Each public invocation owns new faux controls, just as each real trial has new evidence.
        owned = controls / str(len(list(controls.iterdir())))
        owned.mkdir()
        return ArtificialProcess(
            owned, FAMILIES[0], False, {p.page: p for p in chunk.page_images}
        )(label, *args)

    monkeypatch.setattr(evaluation, "production_command", command)
    monkeypatch.setattr(
        "doc_translate.ai_runtime.installed_runtime",
        lambda: pytest.fail("Real runtime reached"),
    )
    real = SendBudget.before_send

    def artificial_oauth(self, config, request, preflight):
        preflight = preflight.model_copy(
            update={
                "auth": preflight.auth.model_copy(
                    update={"type": "oauth", "source": "OAuth"}
                )
            }
        )
        return real(self, config, request, preflight)

    monkeypatch.setattr(SendBudget, "before_send", artificial_oauth)


def run(prepared, output):
    return evaluation.run_approved(
        prepared,
        sha((prepared / "hashes.json").read_bytes()),
        output,
        evaluation.CONTINUATION,
    )


def test_pdf_source_evaluation_prepare_no_auth_and_public_faux_connection(
    prepared, tmp_path, monkeypatch
):
    proposal = json.loads((prepared / "proposal.json").read_bytes())
    assert [c["name"] for c in proposal["cases"]] == ["sample-01"]
    assert proposal["prior_trials"] == 2 and proposal["prior_sends"] == 4
    assert proposal["additional_trial_limit"] == 10 and proposal["cap"] == 50
    before = inventory(prepared)
    faux_connection(prepared, tmp_path, monkeypatch)
    for number in (3, 4):
        result = run(prepared, tmp_path / f"result-{number}")
        assert len(json.loads((result / "sends.json").read_bytes())) == 1
        observations = json.loads((result / "observations.json").read_bytes())
        assert observations["cumulative_trials"] == number
        assert observations["cumulative_reservations"] == number + 2
        assert "NEVER_INCLUDE_EXPECTATIONS" not in (result / "sends.json").read_text()
        evaluation.classify_trial(
            evaluation.CONTINUATION,
            number,
            "continue",
            "Offline Root analysis: artificial quality path, invariants intact",
        )
    assert inventory(prepared) == before


@pytest.mark.parametrize(
    "fault",
    [
        "request",
        "extra-file",
        "implementation",
        "old-cases",
        "old-authorization",
        "other-case",
        "reference-pages",
        "config",
        "protected",
        "missing-ledger",
        "cancel",
    ],
)
def test_pdf_source_evaluation_refuses_changed_approval_before_runtime(
    prepared, tmp_path, monkeypatch, fault
):
    import threading

    digest = sha((prepared / "hashes.json").read_bytes())
    cancel = threading.Event()
    if fault == "request":
        (prepared / "sample-01/initial-review.json").write_text("changed")
    elif fault == "extra-file":
        (prepared / "sample-01/input/extra.json").write_text("unapproved")
    elif fault == "implementation":
        monkeypatch.setattr(evaluation, "implementation_hashes", dict)
    elif fault == "protected":
        monkeypatch.setattr(evaluation, "protected_hash", lambda: "changed")
    elif fault == "missing-ledger":
        (evaluation.CONTINUATION / "origin.json").unlink()
    elif fault == "cancel":
        cancel.set()
    else:
        proposal = json.loads((prepared / "proposal.json").read_bytes())
        if fault == "old-cases":
            proposal["cases"] *= 9
        elif fault == "old-authorization":
            proposal["authorization"] = "old-nine-case-approval"
        elif fault == "other-case":
            proposal["cases"][0]["name"] = "sample-02"
        elif fault == "reference-pages":
            proposal["cases"][0]["reference_pages"] = [3]
        else:
            proposal["config"]["model"] = "unapproved"
        write_json(prepared / "proposal.json", proposal)
        # A new test-owned preparation, not an overwrite of retained approval evidence.
        import shutil

        replacement = tmp_path / "tampered-proposal"
        shutil.copytree(prepared, replacement)
        (replacement / "hashes.json").unlink()
        finish(replacement, {"tampered": True})
        prepared = replacement
        digest = sha((prepared / "hashes.json").read_bytes())
    monkeypatch.setattr(
        evaluation, "trial", lambda *a: pytest.fail("Guard reached operation")
    )
    with pytest.raises((ValueError, FileNotFoundError)):
        evaluation.run_approved(
            prepared, digest, tmp_path / "rejected", evaluation.CONTINUATION, cancel
        )
    assert not (tmp_path / "rejected").exists()


def test_pdf_source_evaluation_durable_interruption(prepared, tmp_path, monkeypatch):
    import threading

    from doc_translate.workspace import workspace_lock

    state = evaluation.Continuation(evaluation.CONTINUATION, CONFIG, threading.Event())
    with workspace_lock(evaluation.CONTINUATION):
        state.reconcile()
        proposal = json.loads((prepared / "proposal.json").read_bytes())
        state.start(
            tmp_path / "interrupted",
            prepared,
            sha((prepared / "hashes.json").read_bytes()),
            proposal,
        )
        state.reserve({"request": "reserved but delivery unknown"})
    assert (evaluation.CONTINUATION / "start-03.json").read_bytes() == (
        evaluation.CONTINUATION / "trial-03/start.json"
    ).read_bytes()
    assert (evaluation.CONTINUATION / "trial-03/send-01.json").exists()
    with pytest.raises(FileNotFoundError):
        run(prepared, tmp_path / "cannot-reset")
    with pytest.raises(FileExistsError):
        evaluation.initialize_continuation(CONFIG)


def test_pdf_source_evaluation_root_classification_and_missing_evidence(
    prepared, tmp_path, monkeypatch
):
    faux_connection(prepared, tmp_path, monkeypatch)
    result = run(prepared, tmp_path / "one")
    with pytest.raises(evaluation.RootReviewRequired):
        run(prepared, tmp_path / "no-review")
    evaluation.classify_trial(
        evaluation.CONTINUATION,
        3,
        "continue",
        "Cause, offline checks and new-trial justification retained",
    )
    (result / "sends.json").unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        run(prepared, tmp_path / "missing-evidence")


def test_pdf_source_evaluation_reservation_storage_failure(
    prepared, tmp_path, monkeypatch
):
    faux_connection(prepared, tmp_path, monkeypatch)
    original = evaluation.durable_record

    def fail(path, value):
        if path.name.startswith("send-"):
            raise OSError("injected reservation storage failure")
        return original(path, value)

    monkeypatch.setattr(evaluation, "durable_record", fail)
    with pytest.raises((ValueError, OSError, RuntimeError)):
        run(prepared, tmp_path / "storage-failed")
    failure = json.loads((tmp_path / "storage-failed/failure.json").read_bytes())
    assert failure["stop_kind"] == "safety"
    assert failure["reason"] == "reservation-storage"
    assert not (tmp_path / "storage-failed/sends.json").exists()
    with pytest.raises(ValueError, match="Safety stop"):
        evaluation.classify_trial(
            evaluation.CONTINUATION, 3, "continue", "Cannot clear a safety stop"
        )


def test_pdf_source_evaluation_review_exclusions_match_preparation_and_candidate(
    samples, tmp_path, monkeypatch
):
    row = json.loads((samples / "review-only/proposed-comparisons.json").read_bytes())[
        1
    ]
    workspace = samples / "synthetic-inputs" / row["sample"]
    fixed = SourceSnapshot.model_validate_json(
        (workspace / "current-source.json").read_bytes()
    )
    digests = row["chunk_sha256s"]
    chunks = [load_pdf_correction(workspace, d) for d in digests]
    pages = {p.page: p for c in chunks for p in c.page_images}
    proposal = tmp_path / "prepared-case"
    evaluation.prepare_case(workspace, proposal, digests, fixed)
    initial = json.loads((proposal / "initial-review.json").read_bytes())
    controls = tmp_path / "owned-faux"
    controls.mkdir()
    process = ArtificialProcess(controls, row["family"], True, pages)

    def command(label, *args):
        argv = process(label, *args)
        if "-correction-" in label:
            path = Path(argv[3])
            answer = json.loads(path.read_bytes())
            for exclusion in answer["exclusions"]:
                exclusion["reason"] += "; candidate image rechecked"
            write_json(path, answer)
        return argv

    def forbidden_runtime():
        pytest.fail("Exclusion regression reached production runtime")

    monkeypatch.setattr("doc_translate.ai_runtime.installed_runtime", forbidden_runtime)
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    budget = SendBudget(CONFIG, 3, evidence)
    before = inventory(workspace)
    result = trial(workspace, evidence / "case", digests, budget, command)
    assert result["adopt"] and inventory(workspace) == before
    assert budget.sends[0]["request"] == initial
    reviews = [s for s in budget.sends if s["request"]["stage"] == "review"]
    assert len(reviews) == 2
    for send, directory in zip(reviews, ["baseline", "candidate"], strict=True):
        request = send["request"]
        groups = request["context"]["exclusions"]
        assert len(groups) == len(digests)
        for group in groups:
            chunk = load_pdf_correction(
                evidence / "case" / directory, group["chunk_sha256"]
            )
            assert group == {
                "chunk_sha256": group["chunk_sha256"],
                "owned_pages": chunk.owned_pages,
                "reference_pages": chunk.reference_pages,
                "exclusions": [e.model_dump(mode="json") for e in chunk.exclusions],
            }
            assert len(group["exclusions"]) == 2
        wire = json.loads((controls / send["label"] / "request.json").read_bytes())
        received = json.loads(wire["messages"][1]["content"][0]["text"])
        assert received["context"] == request["context"]
    assert (
        reviews[0]["request"]["context"]["exclusions"]
        != reviews[1]["request"]["context"]["exclusions"]
    )
    assert all(
        e["reason"].endswith("; candidate image rechecked")
        for g in reviews[1]["request"]["context"]["exclusions"]
        for e in g["exclusions"]
    )


def test_pdf_source_evaluation_manifest_escape(tmp_path):
    directory = tmp_path / "proposal"
    directory.mkdir()
    write_json(directory / "hashes.json", {"../outside": "anything"})
    with pytest.raises(ValueError, match="Manifest escape"):
        evaluation.verify_manifest(
            directory, sha((directory / "hashes.json").read_bytes())
        )


def retained_counter_fixture(number, count):
    """Counter-only retained records, not model quality evidence or public-save proof."""
    directory = evaluation.CONTINUATION / f"trial-{number:02d}"
    output = evaluation.AUTHORIZATION / f"counter-only-{number}"
    start = {"number": number, "prior_sends": 4, "output": str(output)}
    evaluation.durable_record(
        evaluation.CONTINUATION / f"start-{number:02d}.json", start
    )
    directory.mkdir(mode=0o700)
    evaluation.durable_record(directory / "start.json", start)
    output.mkdir(mode=0o700)
    (output / "sample-01").mkdir(mode=0o700)
    sends = [{"reservation": i + 1, "counter_only": True} for i in range(count)]
    for i, send in enumerate(sends, 1):
        evaluation.durable_record(directory / f"send-{i:02d}.json", send)
    if sends:
        write_json(output / "sends.json", sends)
    write_json(
        output / "sample-01/result.json",
        {
            "input_hash_mtime_unchanged": True,
            "baseline_hash_mtime_unchanged": True,
            "counter_only": True,
        },
    )
    finish(output, {"counter_only": True})
    evaluation.durable_record(
        directory / "completion.json",
        {
            "manifest_sha256": sha((output / "hashes.json").read_bytes()),
            "start_sha256": sha((directory / "start.json").read_bytes()),
            "reservations": count,
            "stop_kind": "quality",
        },
    )
    evaluation.classify_trial(
        evaluation.CONTINUATION,
        number,
        "continue",
        "Counter-only fixture, no semantic success claim",
    )


@pytest.mark.parametrize(
    "fault", ["directory", "reference", "mismatch", "before-directory"]
)
def test_pdf_source_evaluation_independent_start_missing_before_runtime(
    prepared, tmp_path, monkeypatch, fault
):
    import threading

    from doc_translate.workspace import workspace_lock

    directory = evaluation.CONTINUATION / "trial-03"
    reference = evaluation.CONTINUATION / "start-03.json"
    if fault == "before-directory":
        state = evaluation.Continuation(
            evaluation.CONTINUATION, CONFIG, threading.Event()
        )
        real = evaluation.new_directory

        def interrupt(path, *args):
            if path == directory:
                raise OSError("interrupted after independent start")
            return real(path, *args)

        monkeypatch.setattr(evaluation, "new_directory", interrupt)
        with workspace_lock(evaluation.CONTINUATION):
            state.reconcile()
            with pytest.raises(OSError, match="after independent start"):
                state.start(
                    tmp_path / "interrupted",
                    prepared,
                    sha((prepared / "hashes.json").read_bytes()),
                    json.loads((prepared / "proposal.json").read_bytes()),
                )
        assert reference.exists() and not directory.exists()
    else:
        retained_counter_fixture(3, 1)
        if fault == "directory":
            before = inventory(directory)
            directory.rename(tmp_path / "retained-trial-03")
            assert inventory(tmp_path / "retained-trial-03") == before
        else:
            reference.rename(tmp_path / "retained-start-03.json")
            if fault == "mismatch":
                start = json.loads((tmp_path / "retained-start-03.json").read_bytes())
                start["number"] = 4
                evaluation.durable_record(reference, start)
    # A fresh interpreter/new output must fail before any operation or runtime lookup.
    check = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import pdf_source_evaluation as e
import doc_translate.ai_runtime as runtime
from pdf_source_feasibility import sha

e.AUTHORIZATION = Path(sys.argv[2])
e.CONTINUATION = e.AUTHORIZATION / 'continuation'
e.OLD_SENDS = json.loads((e.CONTINUATION / 'origin.json').read_bytes())['old_sends']
def forbidden(*args, **kwargs):
    raise AssertionError('Unexpected trial/runtime operation')
e.trial = e.production_command = runtime.installed_runtime = forbidden
prepared = Path(sys.argv[3])
output = e.AUTHORIZATION / 'fresh-output'
try:
    e.run_approved(prepared, sha((prepared / 'hashes.json').read_bytes()), output, e.CONTINUATION)
except ValueError as error:
    assert 'trial start' in str(error), str(error)
else:
    raise AssertionError('Missing start evidence refunded')
assert not output.exists()
assert (e.CONTINUATION / 'safety-stop.json').exists()
print('fresh-process missing-start stop; runtime calls 0')
""",
            str(Path(evaluation.__file__).parent),
            str(tmp_path),
            str(prepared),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert "runtime calls 0" in check.stdout
    assert not (evaluation.CONTINUATION / "trial-04").exists()
    assert not (evaluation.CONTINUATION / "start-04.json").exists()


@pytest.mark.parametrize("cap", ["trial", "send"])
def test_pdf_source_evaluation_separate_cumulative_caps_before_operation(
    prepared, tmp_path, monkeypatch, cap
):
    if cap == "trial":
        for number in range(3, 13):
            retained_counter_fixture(number, 0)
    else:
        retained_counter_fixture(3, 50)
    monkeypatch.setattr(
        evaluation,
        "trial",
        lambda *a: pytest.fail("Exhausted budget reached operation"),
    )
    with pytest.raises(ValueError, match="Trial cap" if cap == "trial" else "Send cap"):
        run(prepared, tmp_path / "exhausted")
    assert not (tmp_path / "exhausted").exists()


def test_pdf_source_evaluation_last_reservation_not_refunded(prepared, tmp_path):
    import threading

    from doc_translate.workspace import workspace_lock

    retained_counter_fixture(3, 49)
    state = evaluation.Continuation(evaluation.CONTINUATION, CONFIG, threading.Event())
    with workspace_lock(evaluation.CONTINUATION):
        state.reconcile()
        assert state.trials == 1 and state.sends == 49  # cumulative 3/53
        proposal = json.loads((prepared / "proposal.json").read_bytes())
        state.start(
            tmp_path / "last-send",
            prepared,
            sha((prepared / "hashes.json").read_bytes()),
            proposal,
        )
        state.reserve({"reservation": 1, "delivery": "unknown"})
        with pytest.raises(ValueError, match="Send cap"):
            state.reserve({"reservation": 2})
    assert (evaluation.CONTINUATION / "trial-04/send-01.json").exists()
    assert not (evaluation.CONTINUATION / "trial-04/send-02.json").exists()
    with pytest.raises(FileNotFoundError):
        run(prepared, tmp_path / "interruption-cannot-reset")


@pytest.mark.parametrize("phase", ["start-03", "start", "completion"])
def test_pdf_source_evaluation_start_or_completion_storage_stop(
    prepared, tmp_path, monkeypatch, phase
):
    faux_connection(prepared, tmp_path, monkeypatch)
    real = evaluation.durable_record

    def fail(path, value):
        if path.name == phase + ".json":
            raise OSError("injected durable record failure")
        return real(path, value)

    monkeypatch.setattr(evaluation, "durable_record", fail)
    with pytest.raises(OSError, match="injected"):
        run(prepared, tmp_path / "failed-record")
    with pytest.raises(ValueError, match="Safety stop"):
        run(prepared, tmp_path / "no-reset")
    assert not (evaluation.CONTINUATION / "trial-03/completion.json").exists()


def test_pdf_source_evaluation_allowed_helper_change_needs_new_exact_binding(
    prepared, tmp_path, monkeypatch
):
    faux_connection(prepared, tmp_path, monkeypatch)
    hashes = evaluation.implementation_hashes()
    hashes[str(Path(evaluation.__file__).relative_to(evaluation.ROOT))] = (
        "offline-simulated-allowed-edit"
    )
    monkeypatch.setattr(evaluation, "implementation_hashes", lambda: hashes)
    with pytest.raises(ValueError, match="Implementation changed"):
        run(prepared, tmp_path / "old-binding")
    replacement = evaluation.prepare(
        tmp_path / "new-binding",
        CONFIG,
        subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    )
    result = run(replacement, tmp_path / "current-binding")
    assert (
        json.loads((result / "observations.json").read_bytes())[
            "cumulative_reservations"
        ]
        == 5
    )


def test_pdf_source_evaluation_protected_validator_fingerprint(prepared, monkeypatch):
    import ast

    parse = ast.parse

    def changed_contract(source):
        tree = parse(source)
        node = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "validate_inspection"
        )
        node.body = [ast.Return(value=ast.Constant(value=None))]
        return tree

    monkeypatch.setattr(evaluation.ast, "parse", changed_contract)
    with pytest.raises(ValueError, match="Protected implementation"):
        evaluation.fixed_guard(CONFIG)


@pytest.mark.parametrize("fault", ["cancel", "lock", "scope"])
def test_pdf_source_evaluation_pretrial_safety_stop_cannot_clear(
    prepared, tmp_path, monkeypatch, fault
):
    import threading

    from doc_translate.workspace import WorkspaceLockedError, workspace_lock

    cancel = threading.Event()
    digest = sha((prepared / "hashes.json").read_bytes())
    if fault == "lock":
        active = evaluation.Continuation(evaluation.CONTINUATION, CONFIG, cancel)
        with workspace_lock(evaluation.CONTINUATION):
            active.reconcile()
            with pytest.raises(WorkspaceLockedError):
                run(prepared, tmp_path / "parallel")
            with pytest.raises(ValueError, match="Safety stop"):
                active.guard()
    else:
        if fault == "cancel":
            cancel.set()
        else:
            proposal = json.loads((prepared / "proposal.json").read_bytes())
            proposal["cases"][0]["name"] = "sample-02"
            write_json(prepared / "proposal.json", proposal)
            (prepared / "hashes.json").unlink()
            finish(prepared, {"test_owned_scope_fault": True})
            digest = sha((prepared / "hashes.json").read_bytes())
        with pytest.raises(ValueError):
            evaluation.run_approved(
                prepared, digest, tmp_path / "rejected", evaluation.CONTINUATION, cancel
            )
    assert (evaluation.CONTINUATION / "safety-stop.json").exists()
    assert not (evaluation.CONTINUATION / "trial-03").exists()
    cancel.clear()  # Even a fresh invocation/token must not undo the retained stop.
    with pytest.raises(ValueError, match="Safety stop"):
        run(prepared, tmp_path / "must-not-restart")


def test_pdf_source_evaluation_retry_stages_share_durable_reservations(
    prepared, tmp_path, monkeypatch
):
    faux_connection(prepared, tmp_path, monkeypatch)
    case = json.loads((prepared / "proposal.json").read_bytes())["cases"][0]
    chunk = load_pdf_correction(prepared / "sample-01/input", case["chunk_sha256s"][0])
    controls = tmp_path / "retry-controls"
    controls.mkdir()
    process = ArtificialProcess(
        controls,
        FAMILIES[0],
        True,
        {p.page: p for p in chunk.page_images},
        coverage=True,
    )

    def command(label, *args):
        # Transport retry on both inspections; public coverage retry on correction.
        process.retry = "-correction-" not in label
        return process(label, *args)

    monkeypatch.setattr(evaluation, "production_command", command)
    with pytest.raises(ValueError):
        run(prepared, tmp_path / "quality-failure")
    output = tmp_path / "quality-failure"
    sends = json.loads((output / "sends.json").read_bytes())
    assert [s["request"]["stage"] for s in sends] == [
        "review",
        "review",
        "correction",
        "correction",
        "review",
        "review",
    ]
    assert "correction_feedback" in sends[3]["request"]["context"]
    directory = evaluation.CONTINUATION / "trial-03"
    assert sends == [
        json.loads((directory / f"send-{i:02d}.json").read_bytes()) for i in range(1, 7)
    ]
    assert (
        json.loads((directory / "completion.json").read_bytes())["stop_kind"]
        == "quality"
    )
    assert not (evaluation.CONTINUATION / "safety-stop.json").exists()
    evaluation.classify_trial(
        evaluation.CONTINUATION,
        3,
        "stop",
        "Artificial false defect; retain all retries, no further evaluation",
    )
    with pytest.raises(ValueError, match="Root continuation judgment"):
        run(prepared, tmp_path / "after-stop")

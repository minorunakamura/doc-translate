"""Issue 81 sample-01 continuation; CLI prepares only, Root alone runs real trials."""

import argparse
import ast
import json
import os
import subprocess
import sys
import threading
from importlib.metadata import distribution
from pathlib import Path

from pdf_source_feasibility import (
    ROOT,
    finish,
    inventory,
    new_directory,
    private_error,
    sha,
    write_json,
)
from pdf_source_trial import (
    RECOVERY_POLICY,
    REVIEW_POLICY,
    Inspection,
    SendBudget,
    block_pages,
    copy_dependencies,
    correction_request,
    recovery_policy,
    require,
    review_request,
    trial,
)

from doc_translate.inference import GenerationConfig
from doc_translate.pdf_correction import CorrectionAnswer, load_pdf_correction
from doc_translate.pdf_source import assemble_pdf_source
from doc_translate.workspace import workspace_lock


def verify_manifest(directory, digest):
    require(directory.absolute() == directory.resolve(), "Symlink input refused")
    data = (directory / "hashes.json").read_bytes()
    require(sha(data) == digest, "Manifest hash mismatch")
    manifest = json.loads(data)
    for relative, expected in manifest.items():
        path = directory / relative
        require(
            path.absolute() == path.resolve()
            and path.resolve().is_relative_to(directory.resolve()),
            "Manifest escape",
        )
        require(sha(path.read_bytes()) == expected, "Fixed input changed")
    require(
        {str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()}
        == set(manifest) | {"hashes.json"},
        "Unmanifested input file",
    )


def implementation_hashes():
    paths = [
        *Path(__file__).parent.glob("pdf_source_*.py"),
        *(ROOT / "src/doc_translate").rglob("*.py"),
        *(
            p
            for directory in ("ai/src", "ai/dist")
            for p in (ROOT / directory).rglob("*")
            if p.is_file()
        ),
        ROOT / "ai/tests/pdf-correction-process.mjs",
        ROOT / "ai/tests/no-network.mjs",
    ]
    return {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in sorted(paths)}


def prepare_case(workspace, output, digests, fixed_source):
    """Copy the public-reader closure, not an entire workspace or expected answers."""
    output.mkdir(mode=0o700)
    copied = output / "input"
    chunks = copy_dependencies(workspace, copied, digests)
    baseline = assemble_pdf_source(
        workspace=copied, chunk_sha256s=digests, snapshot_id="issue80-baseline"
    )
    require(
        baseline.blocks == fixed_source.blocks
        and baseline.uncertainties == fixed_source.uncertainties,
        "Fixed source/assembly mismatch",
    )
    owned = {p for c in chunks for p in c.owned_pages}
    require(
        all(block_pages(b) <= owned for b in baseline.blocks),
        "Connection ownership is not closed",
    )
    # trial revalidates this projection without copying the original source pointer.
    write_json(copied / "current-source.json", baseline.model_dump(mode="json"))
    pages = {p.page: p for c in chunks for p in c.page_images}
    request = review_request(baseline, pages, dict(zip(digests, chunks, strict=True)))
    write_json(output / "initial-review.json", request.model_dump(mode="json"))
    corrections = []
    for digest, chunk in zip(digests, chunks, strict=True):
        current = correction_request(
            copied, chunk, recovery_policy(chunk, {"blocks": [], "gaps": []})
        )
        corrections.append(
            {
                "chunk_sha256": digest,
                "owned_pages": chunk.owned_pages,
                "reference_pages": chunk.reference_pages,
                "text": current.text,
                "context": current.context,
                "policy_prefix": RECOVERY_POLICY,
                "request": current.model_dump(mode="json"),
                "request_sha256": sha(current.model_dump_json().encode()),
            }
        )
    write_json(output / "correction-templates.json", corrections)
    files = {
        str(p.relative_to(copied)): {
            "sha256": sha(p.read_bytes()),
            "bytes": p.stat().st_size,
        }
        for p in sorted(copied.rglob("*"))
        if p.is_file()
    }
    write_json(output / "copy-closure.json", files)
    return {
        "name": output.name,
        "chunk_sha256s": list(digests),
        "owned_pages": sorted(owned),
        "reference_pages": sorted({p for c in chunks for p in c.reference_pages}),
        "image_pages": sorted(pages),
        "images": [
            {
                "page": p.page,
                "artifact": p.image.artifact,
                "sha256": p.image.sha256,
                "bytes": (copied / p.image.artifact).stat().st_size,
            }
            for p in pages.values()
        ],
        "source_sha256": sha(baseline.model_dump_json().encode()),
        "review_request_sha256": sha(request.model_dump_json().encode()),
        "copy_files": len(files),
        "copy_bytes": sum(v["bytes"] for v in files.values()),
        "maximum_reservations": 6 + 9 * len(chunks),
    }


# This authorization has one private continuation location; never infer a new ledger from outputs.
AUTHORIZATION = ROOT / "work/issue81-sample01-urr6jl4s"
CONTINUATION = AUTHORIZATION / "continuation"
SAMPLE = (
    ROOT
    / "work/issue79-samples-6ywsElla/root/synthetic-corrected/synthetic-inputs/sample-01"
)
EXPECTATIONS = ROOT / "work/issue79-samples-6ywsElla/root/fixed-expectations.json"
SOURCE_SHA = "47cb5cf2bb38a23768e94bca6f52113bde0ac0690f1b50ac3d6ebf803fc09ca2"
BASELINE_SHA = "871e35048f459b1abf70d244f8ab307bf3a76925d9700661b6be5b2824f72fa5"
EXPECTATIONS_SHA = "70ea833c75fd671dbc78cc8206faa1a0bb11ff255cb55305a4198a27e8b8d3be"
CHUNK = "e25395d94243087ee021b20f284c62164130303ac95f6c40ed0feb2e1561ba7c"
IMAGES = [
    "9d7ed96a3f51fdd9f1f158a252102d580b21c37bda07349d908fcb3786b659a7",
    "55a775f4e0716f2709187249eadf45be65a46e22895b90fe3ba0286e815782b8",
]
CONFIG_SHA = "c54be509196fcb00fc7f07bcd9dbdeb866a5dd80e0eacab46e588ee39e5f332d"
PLAN_SHA = "d5f9adae55e8adb0e29c36c1b712e49977c424f7538e10ebd51739a43fba38c9"
OLD_SENDS = {
    "work/issue81-evaluation-WHicSBlL/root/real-evaluation-01/sends.json": "62c95772dfc8bfc52649098b621e94bff2c658133756a99c5888eb8d19ea9892",
    "work/issue81-sample01-approval-pdYiBjFU/real-evaluation-02/sends.json": "7f5b6947271cf7f69b56fd8d9cb1c591db733482a5d0841c653edb6c218f65b8",
}
PROTECTED_SHA = "55dced95152ef8f4ab12c4c18c2273f68b5394d7f7d0c7088e490ba0ded7f402"


def protected_hash():
    """Pin non-authorized code independently of the editable proposal binding."""
    hashes = implementation_hashes()
    protected = {k: v for k, v in hashes.items() if not k.startswith(".pi/")}
    tree = ast.parse(Path(__file__).with_name("pdf_source_trial.py").read_text())
    names = {
        "require",
        "quotes",
        "block_pages",
        "validate_region",
        "validate_inspection",
        "normalized",
        "decide",
    }
    protected["trial_contract"] = sha(
        ast.dump(
            ast.Module(
                body=[
                    node
                    for node in tree.body
                    if isinstance(node, ast.ClassDef)
                    and node.name != "SendBudget"
                    or isinstance(node, ast.FunctionDef)
                    and node.name in names
                    or isinstance(node, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id == "Visible"
                        for target in node.targets
                    )
                ],
                type_ignores=[],
            )
        ).encode()
    )
    # Other offline helpers and dependency/config inputs are not editable under this permission.
    for path in sorted(Path(__file__).parent.glob("pdf_source_*.py")):
        if path.name not in {"pdf_source_trial.py", "pdf_source_evaluation.py"}:
            protected[str(path.relative_to(ROOT))] = sha(path.read_bytes())
    for name in (
        "pyproject.toml",
        "uv.lock",
        "ai/package.json",
        "ai/package-lock.json",
        "ai/pnpm-lock.yaml",
        "ai/pnpm-workspace.yaml",
        "ai/tsconfig.json",
        "ai/tsconfig.build.json",
    ):
        path = ROOT / name
        if path.exists():
            protected[name] = sha(path.read_bytes())
    resources = Path(str(distribution("doc-translate").locate_file("doc_translate/ai")))
    for path in sorted(resources.rglob("*")):
        if path.is_file():
            protected["distributed/" + str(path.relative_to(resources))] = sha(
                path.read_bytes()
            )
    return sha(json.dumps(protected, sort_keys=True).encode())


def fixed_guard(config):
    require(
        protected_hash() == PROTECTED_SHA,
        "Protected implementation changed: REPLAN_REQUIRED",
    )
    require(
        sha((ROOT / "docs/plans/pdf-source-quality-feasibility.md").read_bytes())
        == PLAN_SHA,
        "Plan changed",
    )
    require(
        sha(config.model_dump_json().encode()) == CONFIG_SHA,
        "Unauthorized model/config",
    )
    require(SAMPLE.absolute() == SAMPLE.resolve(), "Symlink input refused")
    require(
        sha((SAMPLE / "current-source.json").read_bytes()) == SOURCE_SHA,
        "Fixed source changed",
    )
    require(
        EXPECTATIONS.absolute() == EXPECTATIONS.resolve()
        and sha(EXPECTATIONS.read_bytes()) == EXPECTATIONS_SHA,
        "Fixed expectations changed",
    )
    for (relative, digest), count in zip(OLD_SENDS.items(), (1, 3), strict=True):
        data = (ROOT / relative).read_bytes()
        require(
            sha(data) == digest and len(json.loads(data)) == count,
            "Historical consumption changed",
        )
    chunk = load_pdf_correction(SAMPLE, CHUNK)
    require(
        chunk.owned_pages == [1, 2]
        and chunk.reference_pages == []
        and [p.page for p in chunk.page_images] == [1, 2]
        and [p.image.sha256 for p in chunk.page_images] == IMAGES,
        "Fixed page/image scope changed",
    )
    baseline = assemble_pdf_source(
        workspace=SAMPLE, chunk_sha256s=[CHUNK], snapshot_id="issue80-baseline"
    )
    require(
        sha(baseline.model_dump_json().encode()) == BASELINE_SHA,
        "Fixed baseline changed",
    )
    return baseline


def scope_guard(proposal):
    require(
        proposal["authorization"] == "issue81-sample01-continuation",
        "Old authorization refused",
    )
    require(
        proposal["cap"] == 50
        and proposal["required_auth"] == {"type": "oauth", "source": "OAuth"},
        "Unauthorized limits/auth",
    )
    require(len(proposal["cases"]) == 1, "Only sample-01 authorized")
    case = proposal["cases"][0]
    require(
        case["name"] == "sample-01"
        and case["chunk_sha256s"] == [CHUNK]
        and case["owned_pages"] == [1, 2]
        and case["reference_pages"] == []
        and case["image_pages"] == [1, 2]
        and [p["sha256"] for p in case["images"]] == IMAGES,
        "Only fixed sample-01 scope authorized",
    )
    return case


def prepare(output, config, revision):
    """Prepare only sample-01; faux capture only, no real preflight."""
    require(
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        == revision,
        "Revision mismatch",
    )
    baseline = fixed_guard(config)
    require(
        output.absolute().is_relative_to(AUTHORIZATION),
        "Private authorization parent required",
    )
    output = new_directory(output, SAMPLE)
    original = inventory(SAMPLE)
    try:
        row = prepare_case(SAMPLE, output / "sample-01", [CHUNK], baseline)
        write_json(output / "input-inventory.json", {str(SAMPLE): original})
        write_json(
            output / "policies-schemas.json",
            {
                "review_policy": REVIEW_POLICY,
                "recovery_policy_prefix": RECOVERY_POLICY,
                "review_schema": Inspection.model_json_schema(),
                "correction_schema": CorrectionAnswer.model_json_schema(),
                "generation_rules": "Recovery policy includes exact full validated baseline chunk fields and validated findings; coverage feedback max3 and transport max3; reinspection receives candidate/baseline/gaps. Actual requests recorded and guarded before each send; expectations never sent.",
            },
        )
        proposal = {
            "authorization": "issue81-sample01-continuation",
            "revision": revision,
            "implementation_sha256": implementation_hashes(),
            "protected_sha256": PROTECTED_SHA,
            "config": config.model_dump(mode="json"),
            "config_sha256": sha(config.model_dump_json().encode()),
            "cap": 50,
            "cases": [row],
            "prior_trials": 2,
            "prior_sends": 4,
            "additional_trial_limit": 10,
            "cumulative_trial_limit": 12,
            "cumulative_send_limit": 54,
            "continuation": str(CONTINUATION),
            "required_auth": {"type": "oauth", "source": "OAuth"},
            "expectations_non_send": {
                "path": str(EXPECTATIONS),
                "sha256": EXPECTATIONS_SHA,
            },
            "recovery_rounds": 1,
            "coverage_generations": 3,
            "transport_attempts": 3,
            "real_ai_sends": 0,
            "usage": "known prior 67694 tokens; unknown is not zero; reservations are not billing",
        }
        scope_guard(proposal)
        write_json(output / "proposal.json", proposal)
    finally:
        unchanged = inventory(SAMPLE) == original
        finish(
            output,
            {
                "input_hash_mtime_unchanged": unchanged,
                "real_ai_sends": 0,
                "cases_prepared": 1,
            },
        )
        require(unchanged, "Original input changed")
    return output


def durable_record(path, value):
    """Exclusive, fsynced and reread start/reservation/outcome; partial records never refunded."""
    data = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    for directory in (path.parent, path.parent.parent):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    require(path.read_bytes() == data, "Continuation readback failed")


def initialize_continuation(config):
    """Root calls once explicitly. run_approved never initializes/resets the record."""
    fixed_guard(config)
    path = new_directory(CONTINUATION)
    durable_record(
        path / "origin.json",
        {
            "authorization": "issue81-sample01-continuation",
            "prior_trials": 2,
            "prior_sends": 4,
            "old_sends": OLD_SENDS,
            "plan_sha256": PLAN_SHA,
        },
    )
    return path


class RootReviewRequired(ValueError):
    """A complete trial awaits Root analysis; not missing execution evidence."""


class Continuation:
    """Single authorized sequence, held under workspace_lock for the whole run."""

    def __init__(self, path, config, cancel):
        require(
            path.absolute() == CONTINUATION
            and path == path.resolve()
            and path.is_dir(),
            "Mandatory existing continuation required",
        )
        require(path.stat().st_mode & 0o077 == 0, "Private continuation required")
        self.path, self.config, self.cancel = path, config, cancel
        self.active = None
        self.stop_reason = None
        self.reservations = []
        self.trials = 0
        self.sends = 0

    def reconcile(self):
        require(
            not (self.path / "safety-stop.json").exists(),
            "Safety stop retained; human confirmation required",
        )
        origin = json.loads((self.path / "origin.json").read_bytes())
        require(
            origin
            == {
                "authorization": "issue81-sample01-continuation",
                "prior_trials": 2,
                "prior_sends": 4,
                "old_sends": OLD_SENDS,
                "plan_sha256": PLAN_SHA,
            },
            "Continuation origin changed",
        )
        expected = {"origin.json", ".writer.lock"}
        self.trials = self.sends = 0
        for number in range(3, 13):
            directory = self.path / f"trial-{number:02d}"
            reference = self.path / f"start-{number:02d}.json"
            require(
                directory.exists() == reference.exists(),
                "Missing independent trial start or trial directory",
            )
            if not reference.exists():
                continue
            require(number == self.trials + 3, "Missing trial start")
            expected.update({directory.name, reference.name})
            data = (directory / "start.json").read_bytes()
            require(reference.read_bytes() == data, "Independent trial start mismatch")
            start = json.loads(data)
            require(
                start["number"] == number and start["prior_sends"] == self.sends + 4,
                "Trial sequence mismatch",
            )
            output = Path(start["output"])
            require(
                output == output.resolve()
                and output.is_relative_to(AUTHORIZATION)
                and not output.is_relative_to(self.path),
                "Trial output escape",
            )
            completion = json.loads((directory / "completion.json").read_bytes())
            require(
                completion["start_sha256"]
                == sha((directory / "start.json").read_bytes()),
                "Trial start evidence changed",
            )
            verify_manifest(output, completion["manifest_sha256"])
            sends_path = output / "sends.json"
            sends = json.loads(sends_path.read_bytes()) if sends_path.exists() else []
            reservations = [
                json.loads((directory / f"send-{i:02d}.json").read_bytes())
                for i in range(1, completion["reservations"] + 1)
            ]
            require(sends == reservations, "Send reservations/evidence mismatch")
            result = json.loads((output / "sample-01/result.json").read_bytes())
            require(
                result["input_hash_mtime_unchanged"]
                and result["baseline_hash_mtime_unchanged"],
                "Input/baseline changed",
            )
            if not (directory / "classification.json").exists():
                raise RootReviewRequired("Root continuation judgment required")
            review = json.loads((directory / "classification.json").read_bytes())
            require(
                review["completion_sha256"]
                == sha((directory / "completion.json").read_bytes())
                and review["decision"] == "continue"
                and bool(review["analysis"].strip()),
                "Root continuation judgment required",
            )
            require(
                completion["stop_kind"] in {"quality", "transport", "completed"},
                "Safety stop: explicit human confirmation required; no automatic continuation",
            )
            require(
                {p.name for p in directory.iterdir()}
                == {"start.json", "completion.json", "classification.json"}
                | {f"send-{i:02d}.json" for i in range(1, len(sends) + 1)},
                "Unknown continuation evidence",
            )
            self.trials += 1
            self.sends += len(sends)
        require(
            {p.name for p in self.path.iterdir()} == expected,
            "Unknown continuation record",
        )
        require(self.trials < 10, "Trial cap reached (12 cumulative)")
        require(self.sends < 50, "Send cap reached (54 cumulative)")

    def guard(self):
        self.stop_reason = "retained-safety-stop"
        require(
            not (self.path / "safety-stop.json").exists(),
            "Safety stop retained; human confirmation required",
        )
        self.stop_reason = "cancelled"
        require(not self.cancel.is_set(), "Cancelled")
        self.stop_reason = "trial-cap"
        require(self.trials < 10, "Trial cap reached (12 cumulative)")
        self.stop_reason = "send-cap"
        require(
            self.sends + len(self.reservations) < 50, "Send cap reached (54 cumulative)"
        )
        self.stop_reason = "fixed-scope/hash/protected-implementation"
        fixed_guard(self.config)
        if self.active is not None:
            self.stop_reason = "prepared-evidence/hash"
            verify_manifest(self.prepared, self.manifest)
            require(
                self.proposal["implementation_sha256"] == implementation_hashes(),
                "Implementation changed",
            )
        self.stop_reason = None

    def start(self, output, prepared, manifest, proposal):
        self.prepared, self.manifest, self.proposal = prepared, manifest, proposal
        self.guard()
        number = self.trials + 3
        start = {
            "number": number,
            "prior_sends": self.sends + 4,
            "output": str(output),
            "prepared": str(prepared),
            "manifest_sha256": manifest,
            "implementation_sha256": proposal["implementation_sha256"],
        }
        # Keep the start outside the trial directory so losing the directory cannot refund it.
        durable_record(self.path / f"start-{number:02d}.json", start)
        directory = new_directory(self.path / f"trial-{number:02d}")
        durable_record(directory / "start.json", start)
        self.active = directory

    def reserve(self, send):
        self.guard()
        require(self.active is not None, "Missing durable trial start")
        assert self.active is not None
        durable_record(
            self.active / f"send-{len(self.reservations) + 1:02d}.json", send
        )
        self.reservations.append(send)


def classify_trial(continuation, number, decision, analysis):
    """Root's offline analysis, not a retry loop or a way to clear safety stops."""
    require(
        continuation == CONTINUATION and continuation == continuation.resolve(),
        "Mandatory continuation required",
    )
    require(
        decision in {"continue", "stop"} and bool(analysis.strip()),
        "Root analysis required",
    )
    with workspace_lock(continuation):
        require(
            decision == "stop" or not (continuation / "safety-stop.json").exists(),
            "Safety stop cannot be cleared by helper",
        )
        directory = continuation / f"trial-{number:02d}"
        data = (directory / "completion.json").read_bytes()
        require(
            decision == "stop"
            or json.loads(data)["stop_kind"] in {"quality", "transport", "completed"},
            "Safety stop cannot be cleared by helper",
        )
        durable_record(
            directory / "classification.json",
            {
                "decision": decision,
                "analysis": analysis,
                "completion_sha256": sha(data),
            },
        )


def production_command(_label, _source, _baseline, _first):
    """Public generate/correct_pdf select the verified installed runtime, never a Node override."""
    return None  # noqa: RET501 - None explicitly selects the production process seam.


def run_approved(prepared, approved_manifest_sha256, output, continuation, cancel=None):
    """Root only: one new trial under the revised permission, never an automatic loop."""
    cancel = cancel if cancel is not None else threading.Event()
    state = Continuation(continuation, None, cancel)
    require(
        not output.exists()
        and not output.is_relative_to(prepared)
        and not prepared.is_relative_to(output),
        "Source/output overlap or existing output",
    )
    stop_kind = "safety"
    try:
        with workspace_lock(continuation):
            state.reconcile()
            require(
                output.absolute() == output.resolve()
                and output.is_relative_to(AUTHORIZATION)
                and not output.is_relative_to(continuation),
                "Private trial output required",
            )
            verify_manifest(prepared, approved_manifest_sha256)
            proposal = json.loads((prepared / "proposal.json").read_bytes())
            case = scope_guard(proposal)
            config = GenerationConfig.model_validate(proposal["config"])
            state.config = config
            state.guard()
            require(
                proposal["protected_sha256"] == PROTECTED_SHA
                and proposal["continuation"] == str(continuation),
                "Authorization binding changed",
            )
            require(
                inventory(SAMPLE)
                == json.loads((prepared / "input-inventory.json").read_bytes())[
                    str(SAMPLE)
                ],
                "Original input changed",
            )
            current_hashes = implementation_hashes()
            bound_hashes = proposal["implementation_sha256"]
            stop_kind = "stale-binding"
            require(
                bound_hashes == current_hashes,
                "Implementation changed; prepare current binding",
            )
            stop_kind = "safety"
            state.start(output, prepared, approved_manifest_sha256, proposal)
            output = new_directory(output, prepared)
            budget = SendBudget(
                config,
                50 - state.sends,
                output,
                cancel,
                required_auth=proposal["required_auth"],
                continuation=state,
            )
            rows = []
            stop_kind = "safety"
            try:
                write_json(
                    output / "approval-reference.json",
                    {
                        "prepared": str(prepared),
                        "manifest_sha256": approved_manifest_sha256,
                        "continuation": str(continuation),
                        "trial": state.trials + 3,
                    },
                )
                rows.append(
                    {
                        "sample": "sample-01",
                        **trial(
                            prepared / "sample-01/input",
                            output / "sample-01",
                            case["chunk_sha256s"],
                            budget,
                            production_command,
                        ),
                    }
                )
                write_json(output / "cases.json", rows)
                stop_kind = "completed"
            except BaseException as error:
                cancel.set()
                path = output / "sample-01/result.json"
                if path.exists():
                    result = json.loads(path.read_bytes())
                    if result.get("input_hash_mtime_unchanged") and result.get(
                        "baseline_hash_mtime_unchanged"
                    ):
                        stop_kind = result.get("stop", {}).get("kind", "safety")
                write_json(
                    output / "failure.json",
                    {
                        "error_type": type(error).__name__,
                        "stage": budget.stage,
                        "reason": budget.safety_reason,
                        "stop_kind": stop_kind,
                    },
                )
                raise
            finally:
                # Finalization failures are safety failures even after a quality rejection.
                outcome_kind, stop_kind = stop_kind, "safety"
                fixed_guard(config)
                require(
                    inventory(SAMPLE)
                    == json.loads((prepared / "input-inventory.json").read_bytes())[
                        str(SAMPLE)
                    ],
                    "Original input changed",
                )
                finish(
                    output,
                    {
                        "cases": rows,
                        "reservations": len(budget.sends),
                        "stop_kind": outcome_kind,
                        "stopped": cancel.is_set(),
                        "cumulative_trials": state.trials + 3,
                        "cumulative_reservations": state.sends
                        + len(state.reservations)
                        + 4,
                        "usage": "Attempts: missing usage unknown, reservations not billing",
                        "meaning_quality_evaluated": False,
                    },
                )
                require(
                    state.reservations == budget.sends, "Reservation evidence mismatch"
                )
                assert state.active is not None
                durable_record(
                    state.active / "completion.json",
                    {
                        "manifest_sha256": sha((output / "hashes.json").read_bytes()),
                        "start_sha256": sha((state.active / "start.json").read_bytes()),
                        "reservations": len(state.reservations),
                        "stop_kind": outcome_kind,
                    },
                )
                stop_kind = outcome_kind
    except BaseException as error:
        if stop_kind == "safety" and not isinstance(error, RootReviewRequired):
            # Independent exclusive marker: a competing caller never rewrites the locked ledger.
            try:
                durable_record(
                    continuation / "safety-stop.json",
                    {
                        "error_type": type(error).__name__,
                        "reason": state.stop_reason or "evidence/scope/runtime",
                    },
                )
            except FileExistsError:
                pass  # A partial or existing stop record also permanently blocks sending.
        raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("output", "config"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = vars(parser.parse_args())
    require(
        args["output"].absolute().is_relative_to(ROOT / "work"),
        "Output must be in ignored work",
    )
    args["config"] = GenerationConfig.model_validate_json(args["config"].read_bytes())
    os.umask(0o077)
    result = prepare(**args)
    print(
        f"Prepared sample-01 only: {result}; offline faux capture, no real runtime/auth/AI; Root checks revised permission and cumulative ledger"
    )


if __name__ == "__main__":
    sys.excepthook = private_error
    main()

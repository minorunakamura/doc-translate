"""Artificial answer adapter only; never an evaluator or a real-provider launcher."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from pdf_source_feasibility import (
    ROOT,
    finish,
    inventory,
    new_directory,
    sha,
    write_json,
)
from pdf_source_samples import FAMILIES, response
from pdf_source_trial import (
    BlockCheck,
    Correspondence,
    Gap,
    Inspection,
    PageCheck,
    Region,
    SendBudget,
    block_pages,
    quotes,
    require,
    trial,
)

from doc_translate.inference import GenerationConfig, GenerationOptions
from doc_translate.snapshot import SourceSnapshot

CONFIG = GenerationConfig(
    provider="faux",
    model="vision",
    thinking="off",
    options=GenerationOptions(timeoutMs=60000),
)


def artificial_inspection(source, family, altered, pages, baseline=None, first=None):
    """Scripted answers exercise the wire/validator, not image recognition accuracy."""

    def region(page):
        image = pages[page]
        return Region(
            page=page,
            image_sha256=image.image.sha256,
            box=(0, 0, image.width_pixels, image.height_pixels),
            observation="Artificial observation: compare drawn page positions and current content.",
        )

    def local(block):
        return block.structure["fragments"][0]["block"]["block_id"]

    bad = (
        {"invented", "number", "code", "table"}
        if family == "history-layout"
        else {"diagram", "duplicate", "part-1", "part-2"}
    )
    checks = [
        BlockCheck(
            target=b.block_id,
            verdict="defect" if altered and local(b) in bad else "normal",
            quotes=quotes(b),
            cells={
                c["cell_id"]: "defect" if altered and c["column"] == 1 else "normal"
                for c in b.structure.get("cells", [])
            },
            evidence=[region(p) for p in sorted(block_pages(b))],
            structure="Artificial full layout/order/table/code membership observation.",
            connection="Artificial comparison of both page fragments and joined full text.",
            picture_region=b.structure.get("region"),
            components="INLET, arrow, OUTLET and rectangle compared to crop.",
            duplication="Artificial caption/body/internal lettering comparison.",
        )
        for b in source.blocks
    ]
    gaps = []
    if family == "history-layout":
        label = next(
            b.block_id for b in source.blocks if local(b) == "measurement-label"
        )
        area = region(2).model_copy(
            update={
                "box": (420, 190, 900, 265),
                "observation": "Measurement value is obliterated; label remains readable.",
            }
        )
        gaps.append(
            Gap(
                key="unreadable-value",
                verdict="indeterminate",
                before=label,
                after=None,
                evidence=area,
                absence="No readable value; do not invent one.",
            )
        )
        if altered:
            gaps.append(
                Gap(
                    key="missing-paragraph",
                    verdict="defect",
                    before=source.blocks[0].block_id,
                    after=source.blocks[1].block_id,
                    evidence=region(1),
                    absence="The image paragraph between opening and limit is absent; invented paragraph is not its text.",
                )
            )
    groups = []
    if baseline is not None:
        old_by_local = {local(b): b for b in baseline.blocks}
        new_by_local = {local(b): b for b in source.blocks}
        if family == "history-layout":
            for key, old in old_by_local.items():
                new = new_by_local["paragraph" if key == "invented" else key]
                groups.append(
                    Correspondence(
                        before=[old.block_id],
                        after=[new.block_id],
                        gaps=["missing-paragraph"] if key == "invented" else [],
                        relation="restored" if key in bad else "kept",
                        evidence=[
                            region(p)
                            for p in sorted(block_pages(old) | block_pages(new))
                        ],
                        explanation="Artificial original-location correspondence; unchanged content retained.",
                    )
                )
        else:
            groups.append(
                Correspondence(
                    before=[b.block_id for b in baseline.blocks],
                    after=[b.block_id for b in source.blocks],
                    gaps=[],
                    relation="restored",
                    evidence=[
                        region(p)
                        for p in sorted(
                            {p for b in baseline.blocks for p in block_pages(b)}
                        )
                    ],
                    explanation="Artificial image-grounded crop/duplicate or connection restoration.",
                )
            )
    return Inspection(
        pages=[
            PageCheck(
                page=p,
                status="inspected",
                evidence=region(p),
                coverage="Artificial full-page body/code/cells, omission/addition, header exclusions, layout, figure duplication and boundary inspection; local unreadability is listed separately.",
            )
            for p in pages
        ],
        blocks=checks,
        gaps=gaps,
        correspondence=groups,
    )


class ArtificialProcess:
    def __init__(
        self, directory, family, altered, pages, *, retry=False, coverage=False
    ):
        self.directory, self.family, self.altered, self.pages = (
            directory,
            family,
            altered,
            pages,
        )
        self.retry, self.coverage = retry, coverage

    def __call__(self, label, source, baseline, first):
        path = self.directory / label
        path.mkdir()
        if "-correction-" in label:
            answer = response(
                source.draft_sha256, self.family, False, source.owned_pages
            )
            if self.coverage:
                answer = {
                    "responses": [
                        {**answer, "blocks": [], "exclusions": [], "uncertainties": []},
                        answer,
                    ]
                }
        else:
            answer = artificial_inspection(
                source,
                self.family,
                self.altered if baseline is None else False,
                self.pages,
                baseline,
                first,
            ).model_dump(mode="json")
        if self.retry:
            answer = {"responses": [answer, answer], "retry": True}
        write_json(path / "answer.json", answer)
        node = shutil.which("node")
        require(node is not None, "Existing Node required")
        return [
            node,
            str(ROOT / "ai/tests/pdf-correction-process.mjs"),
            str(path / "credentials"),
            str(path / "answer.json"),
            str(path / "request.json"),
        ]


def offline(output, inputs, manifest_sha256, revision, cap):
    """Eight fixed artificial samples, one shared budget; no real runtime option."""
    require(
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        == revision,
        "Revision mismatch",
    )
    require(inputs.absolute() == inputs.resolve(), "Symlink input refused")
    manifest_bytes = (inputs / "hashes.json").read_bytes()
    require(sha(manifest_bytes) == manifest_sha256, "Input manifest mismatch")
    original = inventory(inputs)
    manifest = json.loads(manifest_bytes)
    for relative, digest in manifest.items():
        path = inputs / relative
        require(
            path.resolve().is_relative_to(inputs.resolve())
            and path.absolute() == path.resolve(),
            "Manifest escape",
        )
        require(sha(path.read_bytes()) == digest, "Fixed input changed")
    output = new_directory(output, inputs)
    budget = SendBudget(CONFIG, cap, output)
    observations = []
    write_json(
        output / "execution.json",
        {
            "revision": revision,
            "input_manifest_sha256": manifest_sha256,
            "config": CONFIG.model_dump(mode="json"),
            "cap": cap,
            "trials_per_sample": 1,
            "recovery_rounds": 1,
            "transport_attempts": 3,
            "coverage_generations": 3,
            "real_ai_sends": 0,
            "meaning_quality_evaluated": False,
            "helper_sha256": {
                p.name: sha(p.read_bytes())
                for p in Path(__file__).parent.glob("pdf_source_*.py")
            },
        },
    )
    try:
        with tempfile.TemporaryDirectory(prefix="issue80-faux-owned-") as temporary:
            for index in range(8):
                name = f"sample-{index + 1:02d}"
                workspace = inputs / "synthetic-inputs" / name
                source = SourceSnapshot.model_validate_json(
                    (workspace / "current-source.json").read_bytes()
                )
                digests = list(
                    dict.fromkeys(
                        f["chunk_sha256"]
                        for b in source.blocks
                        for f in b.model_dump(mode="json")["structure"]["fragments"]
                    )
                )
                from doc_translate.pdf_correction import load_pdf_correction

                pages = {
                    p.page: p
                    for digest in digests
                    for p in load_pdf_correction(workspace, digest).page_images
                }
                process = ArtificialProcess(
                    Path(temporary), FAMILIES[index // 2], bool(index % 2), pages
                )
                observations.append(
                    {
                        "sample": name,
                        **trial(workspace, output / name, digests, budget, process),
                    }
                )
                write_json(output / "cases.json", observations)
    finally:
        require(inventory(inputs) == original, "Fixed input hash/mtime changed")
        finish(
            output,
            {
                "cases": observations,
                "reservations": len(budget.sends),
                "usage": "See per-attempt provider usage; reservations are not billing",
                "real_ai_sends": 0,
                "meaning_quality_evaluated": False,
                "input_hash_mtime_unchanged": True,
                "cleanup": "owned temporary faux credentials/answers removed; synchronous children reaped; evidence retained",
                "stopped": budget.cancel.is_set(),
            },
        )
    return output

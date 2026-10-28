"""Document runs, setup/authentication, catalogs and saved-only operations."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from doc_translate import ai_runtime
from doc_translate.auth import authenticate
from doc_translate.inference import Selection, resolve_config
from doc_translate.preparation import Glossary, StylePolicy
from doc_translate.render import render_document, workspace_status
from doc_translate.run import (
    CONFIG_ERROR_CODES,
    resume_document,
    run_document,
)


def _announce(workspace, config, inputs, preflight):
    if inputs.stage == "review":
        from doc_translate.snapshot import (
            SnapshotReference,
            load_source_snapshot,
            source_snapshot_sha256,
        )

        source = load_source_snapshot(workspace)
        notice = {
            "input": source.document.artifact,
            "document_sha256": source.document.sha256,
            "source_snapshot_sha256": source_snapshot_sha256(workspace),
            "translation_snapshot_sha256": SnapshotReference.model_validate_json(
                (workspace / "translation-snapshot.json").read_bytes()
            ).sha256,
            "transmission": {
                "unit_ids": inputs.context["unit_ids"],
                "block_ids": inputs.context["block_ids"],
                "pages": inputs.context["pages"],
                "images": inputs.context["pages"],
                "content": "selected saved source/translation pairs, fixed policy/glossary; selected page images and blocks only"
                if inputs.context["pages"]
                else "selected saved source/translation pairs and fixed policy/glossary only; no page images or other unit context",
            },
        }
    else:
        run = json.loads((workspace / "run.json").read_bytes())
        notice = {
            "input": run["source_name"],
            "document_sha256": run["document"]["sha256"],
            "owned_pages": run["owned_pages"],
            "reference_pages": run["reference_pages"],
            "transmission": {
                "owned_pages": inputs.context.get(
                    "owned_pages",
                    run["corrected_pages"]
                    if run["document"]["format"] == "pdf"
                    else None,
                ),
                "reference_pages": inputs.context.get("reference_pages", []),
                "content": "page images and scoped extraction draft"
                if inputs.stage == "correction"
                else "saved source units/context/glossary only; no original file, images, code or formula payload",
            },
        }
    print(
        json.dumps(
            {
                **notice,
                "stage": inputs.stage,
                "provider": config.provider,
                "model": config.model,
                "thinking": config.thinking,
                "auth": preflight.auth.model_dump(),
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
        flush=True,
    )


def _page_range(value: str) -> tuple[int, int]:
    try:
        start, end = (int(number) for number in value.split("-"))
        if start < 1 or end < start:
            raise ValueError
        return start, end
    except ValueError:
        raise argparse.ArgumentTypeError(
            "Expected ascending positive page range A-B"
        ) from None


def main(
    argv: Sequence[str] | None = None, *, process_command: Sequence[str] | None = None
) -> None:
    parser = argparse.ArgumentParser(
        prog="doc-translate",
        description="Traceable PDF and EPUB translation, rendering and status.",
        epilog="Outputs JSON. Exit codes: 0 success, 1 partial output/execution failure, 2 input/configuration error. Run/resume send explicitly scoped data to selected providers. PDF source candidate adoption is explicit via revise --source; pending resume/render exit 1, successful status exits 0. Saved render/status/revise need no Node, authentication or inference.",
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    setup = commands.add_parser(
        "setup-ai",
        help="Explicitly install locked helper dependencies; no login or inference.",
        description="Install bundled locks in a new application-owned runtime outside document workspaces. Requires installed Node >=24.21.0 and the selected manager. Outputs runtime location and versions as JSON; no login or inference.",
    )
    setup.add_argument("--package-manager", choices=("npm", "pnpm"), required=True)
    commands.add_parser(
        "providers",
        help="Optional offline built-in provider catalog lookup.",
        description="Optional lookup: output built-in provider IDs and names as JSON. Requires setup-ai and Node only; no authentication, refresh or network requests.",
    )
    models = commands.add_parser(
        "models",
        help="Optional offline chat model and capability lookup.",
        description="Optional lookup: output built-in chat model IDs, image-input and thinking capabilities as JSON. Requires setup-ai and Node only; no authentication, refresh or network requests.",
    )
    models.add_argument("--provider", required=True)
    login = commands.add_parser(
        "login",
        help="Authenticate using the provider's public flow; secrets require a terminal.",
    )
    login.add_argument("--provider", required=True)
    login.add_argument("--method", choices=("oauth", "api_key"), required=True)
    logout = commands.add_parser(
        "logout", help="Delete this application's stored provider credential."
    )
    logout.add_argument("--provider", required=True)
    for operation in ("render", "status"):
        command = commands.add_parser(
            operation,
            help="Regenerate outputs from saved snapshots only."
            if operation == "render"
            else "Inspect saved processing, QA, scope, usage and output paths.",
            description="No acquisition, correction, preparation, translation, Node, authentication or network access. Uses only adopted source H, never pending candidate C. Reports source_adoption with H/B/C paths, inputs, envelope and next_operation. Render exits 1 for partial output or pending adoption; status exits 0 when inspection succeeds. Input/configuration errors exit 2.",
        )
        command.add_argument("workspace", type=Path)
    revise = commands.add_parser(
        "revise",
        help="Adopt validated JSON revisions without Node or inference.",
        description="Offline source replacement or translation patch with current snapshot hashes (CAS) and a reason. PDF candidate adoption uses the copied source envelope with candidate_sha256; compare H/B/C and optionally edit source before adoption. No automatic merge, translation overwrite, Node, authentication, PDF models or inference. Old snapshots, reasons and raw answers are retained. Run resume afterward to update affected stages, or render after a translation-only patch. Stale bases/candidates and invalid inputs exit 2.",
    )
    revise.add_argument("workspace", type=Path)
    revisions = revise.add_mutually_exclusive_group(required=True)
    revisions.add_argument(
        "--translations", type=Path, help="TranslationPatch JSON; changed units only."
    )
    revisions.add_argument(
        "--source",
        type=Path,
        help="SourceReplacement JSON; whole snapshot, optional candidate_sha256 for explicit adoption. FILE is relative to the current directory, not the workspace.",
    )
    revise.add_argument(
        "--reason",
        required=True,
        help="Nonblank revision/adoption reason; replace TEXT.",
    )
    review = commands.add_parser(
        "review",
        help="Independently review explicit saved units or PDF pages; never rewrite text.",
        description="Send only comma-separated explicit unit IDs (--units unit-1,unit-2), or PDF pages A-B with selected page images. Requires current saved translations. Records findings, reviewer, scope and usage; empty findings never approve quality. Uses explicit provider/model/thinking over saved translation (units) or correction (pages) settings, never ambient PI_*. Does not modify saved stage settings or snapshots. Exit 0 accepted review, 1 inference/save failure, 2 invalid scope/inputs.",
    )
    review.add_argument("workspace", type=Path)
    scope = review.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--units",
        help="Comma-separated explicit unit IDs; no ranges or implicit expansion.",
    )
    scope.add_argument(
        "--pages",
        type=_page_range,
        help="PDF review page range A-B, inclusive; no scope expansion.",
    )
    for field in ("provider", "model", "thinking"):
        review.add_argument(f"--{field}")
    run = commands.add_parser(
        "run",
        help="Translate a local PDF/EPUB in a new workspace.",
        description="First use: setup-ai once, then login if needed. providers/models are optional lookups. Regular use: run with stage or shared provider/model/thinking (or PI_* environment). Creates a new workspace; existing paths including empty directories/symlinks are refused. Announces input hash, scope and actual preflight authentication on stderr before each transmission; summary/artifact paths on stdout. Exit 0 includes needs-attention, 1 partial/failure, 2 input/configuration error.",
    )
    run.add_argument("input", type=Path)
    run.add_argument("--workspace", type=Path, required=True)
    resume = commands.add_parser(
        "resume",
        help="Verify saved inputs and continue only unfinished or explicitly changed stages.",
        description="Saved settings are defaults; ambient PI_* never changes them. Explicit glossary/style policy/provider/model/thinking overrides are persisted. Glossary/style changes do not repeat PDF correction; changed correction inputs may require new calls, not unnecessary acquisition. Transmission scope cannot expand. Changed PDF machine content after source revision is saved as a candidate: stop before preparation/translation, compare H/B/C, then revise --source --reason and resume; no automatic merge. Pending adoption exits 1; input/configuration errors exit 2. Lost responses may have unknown usage and resending may duplicate charges. Completed saved results need no Node/authentication.",
    )
    resume.add_argument("workspace", type=Path)
    resume.add_argument(
        "--policy",
        type=Path,
        help="Explicit style policy JSON (version/text); validated data, never code.",
    )
    for command in (run, resume):
        for prefix in ("", "correction-", "translation-"):
            for field in ("provider", "model", "thinking"):
                command.add_argument(f"--{prefix}{field}")
        command.add_argument(
            "--artifacts-path",
            type=Path,
            help="Prepared local PDF models; needed only for acquisition, never downloaded.",
        )
        command.add_argument(
            "--glossary",
            type=Path,
            help="Glossary JSON (version/book/style/terms); import preferred terms only.",
        )
    run.add_argument("--pages", type=_page_range, help="PDF ownership A-B (inclusive).")
    run.add_argument(
        "--reference-pages",
        type=_page_range,
        help="Explicit PDF reference-only transmission permission A-B.",
    )
    args = parser.parse_args(argv)
    try:
        if args.operation in {"run", "resume"}:
            shared = Selection(
                **{
                    field: getattr(args, field)
                    for field in ("provider", "model", "thinking")
                }
            )

            def selection(stage):
                return Selection(
                    **{
                        field: getattr(args, f"{stage}_{field}")
                        for field in ("provider", "model", "thinking")
                    }
                )

            terms = (
                Glossary.model_validate_json(args.glossary.read_bytes()).terms
                if args.glossary
                else None
            )

            def announce(config, inputs, preflight):
                _announce(args.workspace, config, inputs, preflight)

            if args.operation == "resume":
                result = resume_document(
                    workspace=args.workspace,
                    translation=selection("translation"),
                    correction=selection("correction"),
                    shared=shared,
                    artifacts_path=args.artifacts_path,
                    user_terms=terms,
                    policy=StylePolicy.model_validate_json(args.policy.read_bytes())
                    if args.policy
                    else None,
                    process_command=process_command,
                    before_send=announce,
                )
            else:
                result = run_document(
                    source=args.input,
                    workspace=args.workspace,
                    translation=resolve_config(
                        stage=selection("translation"), shared=shared
                    ),
                    correction=resolve_config(
                        stage=selection("correction"), shared=shared
                    )
                    if args.input.suffix.lower() == ".pdf"
                    else None,
                    pages=args.pages,
                    reference_pages=args.reference_pages,
                    artifacts_path=args.artifacts_path,
                    user_terms=terms,
                    process_command=process_command,
                    before_send=announce,
                )
        elif args.operation == "review":
            from doc_translate.review import review_document

            result = review_document(
                workspace=args.workspace,
                unit_ids=[i.strip() for i in args.units.split(",")]
                if args.units is not None
                else None,
                pages=args.pages,
                selection=Selection(
                    **{
                        field: getattr(args, field)
                        for field in ("provider", "model", "thinking")
                    }
                ),
                process_command=process_command,
                before_send=lambda config, inputs, preflight: _announce(
                    args.workspace, config, inputs, preflight
                ),
            )
        elif args.operation == "revise":
            from doc_translate.revise import revise_document

            result = revise_document(
                workspace=args.workspace,
                translations=args.translations,
                source=args.source,
                reason=args.reason,
            )
        elif args.operation in {"render", "status"}:
            result = (
                render_document(args.workspace)
                if args.operation == "render"
                else workspace_status(args.workspace)
            )
        elif args.operation == "setup-ai":
            result = ai_runtime.setup(args.package_manager)
        elif args.operation in {"login", "logout"}:
            result = authenticate(
                args.operation, args.provider, getattr(args, "method", None)
            )
        else:
            result = ai_runtime.metadata(
                args.operation, getattr(args, "provider", None)
            )
    except ai_runtime.RuntimeUnavailable as error:
        parser.error(str(error))
    except (OSError, ValueError, RuntimeError) as error:
        if args.operation in {
            "run",
            "resume",
            "render",
            "status",
            "revise",
            "review",
        } and isinstance(error, (FileNotFoundError, ValueError)):
            parser.error(str(error))
        parser.exit(1, f"doc-translate: {error}\n")
    print(json.dumps(result, ensure_ascii=False))
    if args.operation == "review" and result["review"]["status"] == "rejected":
        parser.exit(2 if result["review"]["error"] in CONFIG_ERROR_CODES else 1)
    if (
        args.operation in {"run", "resume"}
        and result["scope"]["run"]["error_code"] in CONFIG_ERROR_CODES
    ):
        parser.exit(2)
    if (
        args.operation in {"run", "resume", "render"}
        and result["processing"]["status"] == "partial"
    ):
        parser.exit(1)

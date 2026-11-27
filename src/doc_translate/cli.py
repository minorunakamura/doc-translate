"""Document runs, setup/authentication, catalogs and saved-only operations."""

import argparse
import json
import sys
from collections.abc import Sequence
from contextlib import nullcontext
from pathlib import Path

from pydantic import ValidationError

from doc_translate import ai_runtime
from doc_translate.auth import authenticate
from doc_translate.cli_output import (
    catalog_text,
    review_text,
    saved_status_text,
    summary_text,
)
from doc_translate.inference import CONFIG_ERROR_CODES, Selection, resolve_config
from doc_translate.preparation import Glossary, StylePolicy
from doc_translate.progress import SendNotice
from doc_translate.progress_display import ProgressDisplay
from doc_translate.render import read_saved_status, render_document, workspace_status
from doc_translate.run import resume_document, run_document

OLD_WORKSPACE_GUIDANCE = (
    "このworkspaceは古い形式で保存されています。"
    "新しいworkspaceで `run` し直してください。"
)
# Generation options removed in #118; saved configs/submitted records still name them.
_REMOVED_GENERATION_OPTIONS = frozenset(
    {"temperature", "maxTokens", "transport", "cacheRetention", "thinkingBudgets"}
)


def _saved_before_removed_options(error: Exception) -> bool:
    """True only if every error is a removed option key in saved options/submitted."""
    return isinstance(error, ValidationError) and all(
        item["type"] == "extra_forbidden"
        and item["loc"][-2:-1] in (("options",), ("submitted",))
        and item["loc"][-1] in _REMOVED_GENERATION_OPTIONS
        for item in error.errors()
    )


def _announce(workspace, config, inputs, preflight, metadata=None):
    metadata = metadata or {}
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
                "owned_pages": inputs.context.get("owned_pages"),
                **metadata,
                "reference_pages": inputs.context.get("reference_pages", []),
                "content": "page images and scoped extraction draft"
                if inputs.stage == "correction"
                else "document-wide collected results organization; no full source units retransmitted"
                if metadata.get("phase") == "organize"
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
        epilog="--json: run/resume/review/status/providers/models; others output JSON. Exit 0 ok, 1 partial/failure, 2 input/config error. Details: docs/reference/usage.md",
    )
    commands = parser.add_subparsers(dest="operation", required=True)
    commands.add_parser(
        "setup-ai",
        help="Install locked helper dependencies; no login or inference.",
        description="Install the bundled npm lock into a new application-owned runtime. Requires Node >=24.21.0 and npm; outputs JSON; no login or inference.",
    )
    providers = commands.add_parser(
        "providers",
        help="Optional offline built-in provider catalog lookup.",
        description="Show built-in provider IDs, names and login methods. Requires setup-ai and Node; no authentication or network requests.",
    )
    models = commands.add_parser(
        "models",
        help="Optional offline chat model and capability lookup.",
        description="Show built-in chat models with image-input and thinking capabilities. Requires setup-ai and Node; no authentication or network requests.",
    )
    models.add_argument("--provider", required=True)
    login = commands.add_parser(
        "login",
        help="Authenticate with a provider; secrets require a terminal.",
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
            else "Read saved progress records; --verify for full inspection.",
            description="Offline; uses adopted source H only. Partial output or pending adoption exits 1; input/hash errors exit 2."
            if operation == "render"
            else "Offline; uses adopted source H, never pending candidate C. Reading exits 0 even if partial; input/hash errors exit 2.",
        )
        command.add_argument("workspace", type=Path)
        if operation == "status":
            command.add_argument(
                "--json",
                action="store_true",
                help="Verify fully and output the complete summary JSON.",
            )
            command.add_argument(
                "--verify",
                action="store_true",
                help="Verify saved inputs and outputs fully; readable results.",
            )
    revise = commands.add_parser(
        "revise",
        help="Adopt validated JSON revisions without Node or inference.",
        description="Offline source replacement or translation patch checked against current snapshot hashes (CAS); no inference. Stale or invalid inputs exit 2.",
    )
    revise.add_argument("workspace", type=Path)
    revisions = revise.add_mutually_exclusive_group(required=True)
    revisions.add_argument(
        "--translations", type=Path, help="TranslationPatch JSON; changed units only."
    )
    revisions.add_argument(
        "--source",
        type=Path,
        help="SourceReplacement JSON (whole snapshot); candidate_sha256 adopts a PDF candidate.",
    )
    revise.add_argument(
        "--reason",
        required=True,
        help="Nonblank revision/adoption reason; replace TEXT.",
    )
    review = commands.add_parser(
        "review",
        help="AI-review explicit saved units or PDF pages; never rewrites text.",
        description="Send only the explicit units or PDF pages and record findings. Exit 0 accepted review, 1 inference/save failure, 2 invalid scope/inputs.",
    )
    review.add_argument("workspace", type=Path)
    scope = review.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--units",
        help="Comma-separated explicit unit IDs; no ranges or expansion.",
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
        description="Create a new workspace and translate. Exit 0 (even with needs-attention), 1 partial output or failure, 2 input/configuration error.",
    )
    run.add_argument("input", type=Path)
    run.add_argument("--workspace", type=Path, required=True)
    resume = commands.add_parser(
        "resume",
        help="Continue only unfinished or explicitly changed stages.",
        description="Continue unfinished/changed stages; a new PDF candidate stops before preparation/translation. Pending adoption exits 1; input/config errors exit 2.",
    )
    resume.add_argument("workspace", type=Path)
    resume.add_argument(
        "--policy",
        type=Path,
        help="Explicit style policy JSON (version/text); validated data, never code.",
    )
    for command in (run, resume):
        command.add_argument(
            "--correction-concurrency",
            type=int,
            choices=(1, 2, 4),
            help="Parallel PDF correction jobs (run: 1; resume: saved); not EPUB.",
        )
        command.add_argument(
            "--preparation-concurrency",
            type=int,
            choices=(1, 2, 4),
            help="Parallel preparation collects (run: 1; resume: saved).",
        )
        command.add_argument(
            "--events-json",
            action="store_true",
            help="Emit only pre-send JSON notices on stderr.",
        )
        for prefix in ("", "correction-", "translation-"):
            for field in ("provider", "model", "thinking"):
                command.add_argument(f"--{prefix}{field}")
        command.add_argument(
            "--artifacts-path",
            type=Path,
            help="Prepared local PDF models; never downloaded.",
        )
        command.add_argument(
            "--glossary",
            type=Path,
            help="Glossary JSON; imports preferred terms only.",
        )
    run.add_argument("--pages", type=_page_range, help="PDF ownership A-B (inclusive).")
    run.add_argument(
        "--reference-pages",
        type=_page_range,
        help="PDF reference-only pages A-B (inclusive).",
    )
    review.add_argument(
        "--events-json",
        action="store_true",
        help="Emit only pre-send JSON notices on stderr.",
    )
    for command in (providers, models, run, resume, review):
        command.add_argument(
            "--json",
            action="store_true",
            help="Output full result JSON instead of readable text.",
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

            announce = (
                SendNotice(
                    lambda config, inputs, preflight, metadata: _announce(
                        args.workspace, config, inputs, preflight, metadata
                    ),
                    {},
                )
                if args.events_json
                else None
            )

            with (
                nullcontext(None) if args.events_json else ProgressDisplay() as observer
            ):
                if args.operation == "resume":
                    result = resume_document(
                        workspace=args.workspace,
                        preparation_concurrency=args.preparation_concurrency,
                        correction_concurrency=args.correction_concurrency,
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
                        observer=observer,
                    )
                else:
                    result = run_document(
                        source=args.input,
                        correction_concurrency=args.correction_concurrency,
                        preparation_concurrency=args.preparation_concurrency
                        if args.preparation_concurrency is not None
                        else 1,
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
                        observer=observer,
                    )
        elif args.operation == "review":
            from doc_translate.review import review_document

            with (
                nullcontext(None) if args.events_json else ProgressDisplay() as observer
            ):
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
                    before_send=(
                        lambda config, inputs, preflight: _announce(
                            args.workspace, config, inputs, preflight
                        )
                    )
                    if args.events_json
                    else None,
                    observer=observer,
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
                if args.json or args.verify
                else read_saved_status(args.workspace)
            )
        elif args.operation == "setup-ai":
            result = ai_runtime.setup()
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
            if _saved_before_removed_options(error):
                parser.error(OLD_WORKSPACE_GUIDANCE)
            parser.error(str(error))
        parser.exit(1, f"doc-translate: {error}\n")
    if args.operation in {"providers", "models"} and not args.json:
        print(catalog_text(args.operation, result))
    elif args.operation == "status" and not args.json and not args.verify:
        print(saved_status_text(result, args.workspace))
    elif args.operation == "review" and not args.json:
        print(
            review_text(
                result,
                args.workspace,
                unit_ids=args.units.split(",") if args.units is not None else None,
                pages=args.pages,
            )
        )
    elif args.operation in {"run", "resume", "status"} and not args.json:
        print(summary_text(args.operation, result, args.workspace))
    else:
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

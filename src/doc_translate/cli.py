"""Document runs, setup/authentication, catalogs and saved-only operations."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from doc_translate import ai_runtime
from doc_translate.auth import authenticate
from doc_translate.inference import Selection, resolve_config
from doc_translate.preparation import Glossary
from doc_translate.render import render_document, workspace_status
from doc_translate.run import (
    CONFIG_ERROR_CODES,
    resume_document,
    run_document,
)


def _announce(workspace, config, inputs, preflight):
    run = json.loads((workspace / "run.json").read_bytes())
    print(
        json.dumps(
            {
                "stage": inputs.stage,
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
        epilog="Outputs JSON. Exit codes: 0 success, 1 execution failure, 2 input/configuration error. Run sends explicitly scoped document data to the selected providers. Saved render/status need no Node or authentication.",
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
            description="No acquisition, correction, translation, Node, authentication or network access. Render exits 1 for partial output; status exits 0 when inspection succeeds. Input/configuration errors exit 2.",
        )
        command.add_argument("workspace", type=Path)
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
        description="Saved settings are defaults; ambient PI_* never changes them. Explicit glossary/provider/model/thinking overrides are persisted. Transmission scope cannot expand. Lost responses may have unknown usage and resending may duplicate charges. Completed saved results need no Node/authentication.",
    )
    resume.add_argument("workspace", type=Path)
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
        if args.operation in {"run", "resume", "render", "status"} and isinstance(
            error, (FileNotFoundError, ValueError)
        ):
            parser.error(str(error))
        parser.exit(1, f"doc-translate: {error}\n")
    print(json.dumps(result, ensure_ascii=False))
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

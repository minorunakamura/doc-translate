"""Command-line setup and offline catalog access; document workflows are pending."""

import argparse
import json

from doc_translate import ai_runtime


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="doc-translate",
        description="Traceable PDF and EPUB translation (document workflows not implemented yet).",
        epilog="Outputs JSON. Exit codes: 0 success, 1 execution failure, 2 input/configuration error. No authentication or inference is performed.",
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
    args = parser.parse_args()
    try:
        if args.operation == "setup-ai":
            result = ai_runtime.setup(args.package_manager)
        else:
            result = ai_runtime.metadata(
                args.operation, getattr(args, "provider", None)
            )
    except ai_runtime.RuntimeUnavailable as error:
        parser.error(str(error))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"doc-translate: {error}\n")
    print(json.dumps(result, ensure_ascii=False))

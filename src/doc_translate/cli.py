"""Command-line setup, authentication and offline catalog access."""

import argparse
import json

from doc_translate import ai_runtime
from doc_translate.auth import authenticate


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="doc-translate",
        description="Traceable PDF and EPUB translation (document workflows not implemented yet).",
        epilog="Outputs JSON. Exit codes: 0 success, 1 execution failure, 2 input/configuration error. Authentication is performed only by login/logout; no inference is performed.",
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
    args = parser.parse_args()
    try:
        if args.operation == "setup-ai":
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
        parser.exit(1, f"doc-translate: {error}\n")
    print(json.dumps(result, ensure_ascii=False))

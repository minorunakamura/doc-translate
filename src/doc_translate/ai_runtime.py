"""Application-owned Node runtime, installed only by explicit setup-ai."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from importlib.metadata import distribution
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from doc_translate.jsonl import JSONLRecord, encode_record, read_records

SETUP = "Run doc-translate setup-ai --package-manager npm (or pnpm)."
LOCKS = {"npm": "package-lock.json", "pnpm": "pnpm-lock.yaml"}
INSTALL = {
    "npm": ["ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"],
    "pnpm": ["install", "--prod", "--frozen-lockfile", "--ignore-scripts"],
}


class ProviderMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    name: str
    login_methods: list[Literal["api_key", "oauth"]]


class ModelMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    name: str
    image_input: bool
    reasoning: bool
    thinking_levels: list[
        Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]
    ]


class ProvidersResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    helper_version: str
    library_version: str
    providers: list[ProviderMetadata]


class ModelsResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    helper_version: str
    library_version: str
    provider: str
    models: list[ModelMetadata]


class RuntimeUnavailable(ValueError):
    """A missing or incompatible runtime requires explicit setup."""


def _home() -> Path:
    return (
        Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        / "doc-translate/ai"
    ).absolute()


def _resources() -> Path:
    # force-included wheel data lives here even in a hatch editable installation.
    return Path(str(distribution("doc-translate").locate_file("doc_translate/ai")))


def _environment() -> dict[str, str]:
    environment = {
        k: v for k, v in os.environ.items() if k not in {"NODE_PATH", "NODE_OPTIONS"}
    }
    environment["PATH"] = os.pathsep.join(
        os.path.abspath(path) for path in os.get_exec_path()
    )
    # Installed shims/managers must not fetch a different package-manager version.
    environment.update(
        COREPACK_ENABLE_NETWORK="0",
        npm_config_manage_package_manager_versions="false",
        PNPM_CONFIG_MANAGE_PACKAGE_MANAGER_VERSIONS="false",
    )
    return environment


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        raise RuntimeUnavailable(f"Node >=24.21.0 is required. {SETUP}")
    try:
        version = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            check=False,
            env=_environment(),
        )
        valid = version.returncode == 0 and tuple(
            int(n) for n in version.stdout.strip().removeprefix("v").split(".")
        ) >= (24, 21, 0)
    except OSError, ValueError:
        valid = False
    if not valid:
        raise RuntimeUnavailable(f"Node >=24.21.0 is required. {SETUP}")
    return os.path.abspath(node)


def _identity(manager: str) -> dict:
    resources = _resources()
    package = json.loads((resources / "package.json").read_text())
    helper = hashlib.sha256()
    for path in [resources / "package.json", *sorted((resources / "dist").rglob("*"))]:
        if path.is_file():
            helper.update(path.relative_to(resources).as_posix().encode())
            helper.update(path.read_bytes())
    return {
        "package_manager": manager,
        "lock_sha256": hashlib.sha256(
            (resources / LOCKS[manager]).read_bytes()
        ).hexdigest(),
        "helper_version": package["version"],
        "helper_sha256": helper.hexdigest(),
        "library_version": package["dependencies"]["@earendil-works/pi-ai"],
    }


def _validate(runtime: Path, record: dict) -> None:
    manager = record["package_manager"]
    expected = _identity(manager)
    if any(record.get(key) != value for key, value in expected.items()):
        raise ValueError("Runtime identity mismatch")
    resources = _resources()
    for path in [
        resources / "package.json",
        resources / LOCKS[manager],
        *sorted((resources / "dist").rglob("*")),
    ]:
        if (
            path.is_file()
            and (runtime / path.relative_to(resources)).read_bytes()
            != path.read_bytes()
        ):
            raise ValueError("Runtime resource mismatch")
    package = json.loads((resources / "package.json").read_text())
    for name, version in package["dependencies"].items():
        installed = json.loads(
            (runtime / "node_modules" / name / "package.json").read_text()
        )
        if installed["version"] != version:
            raise ValueError("Runtime dependency version mismatch")


def _request(
    node: str, runtime: Path, operation: str, provider: str | None = None
) -> dict:
    request = JSONLRecord(
        schema_version=1,
        request_id=uuid4().hex,
        type=operation,
        payload={} if provider is None else {"provider": provider},
    )
    child = subprocess.run(
        [node, str(runtime / "dist/main.js")],
        input=encode_record(request),
        capture_output=True,
        check=False,
        env=_environment(),
        cwd=runtime,
    )
    try:
        records = list(read_records(BytesIO(child.stdout)))
        if len(records) != 1 or records[0].request_id != request.request_id:
            raise ValueError("Invalid metadata response")
        response = records[0]
        if (
            response.type == "error"
            and response.payload == {"code": "unknown_provider"}
            and child.returncode == 2
        ):
            raise RuntimeUnavailable(
                "Unknown built-in provider ID; use doc-translate providers."
            )
        if child.returncode != 0 or response.type != "result":
            raise ValueError("Helper failed")
        payload = response.payload
        package = json.loads((_resources() / "package.json").read_text())
        if (
            payload.get("helper_version") != package["version"]
            or payload.get("library_version")
            != package["dependencies"]["@earendil-works/pi-ai"]
        ):
            raise ValueError("Helper version mismatch")
        if operation == "providers":
            return ProvidersResult.model_validate(payload).model_dump()
        result = ModelsResult.model_validate(payload)
        if result.provider != provider:
            raise ValueError("Invalid catalog provider")
        return result.model_dump()
    except RuntimeUnavailable:
        raise
    except ValueError:
        # Never echo raw helper stderr, which may contain environment secrets.
        raise RuntimeError(
            f"AI metadata helper failed or returned invalid data. {SETUP}"
        ) from None


def setup(manager: str) -> dict:
    node = _node()
    executable = shutil.which(manager)
    if executable is None:
        raise RuntimeUnavailable(
            f"Installed {manager} is required; no manager is fetched. {SETUP}"
        )
    executable = os.path.abspath(executable)
    version = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        check=False,
        env=_environment(),
    )
    if version.returncode != 0:
        raise RuntimeError(f"Cannot run {manager}. {SETUP}")
    record = {**_identity(manager), "package_manager_version": version.stdout.strip()}
    home = _home()
    home.mkdir(parents=True, exist_ok=True)
    runtime = Path(
        tempfile.mkdtemp(
            prefix=f"{manager}-{record['lock_sha256'][:12]}-{record['helper_version']}-",
            dir=home,
        )
    )
    for name in ("package.json", LOCKS[manager]):
        shutil.copyfile(_resources() / name, runtime / name)
    shutil.copytree(_resources() / "dist", runtime / "dist")
    install = subprocess.run(
        [executable, *INSTALL[manager]],
        cwd=runtime,
        capture_output=True,
        check=False,
        env=_environment(),
    )
    if install.returncode != 0:
        raise RuntimeError(
            f"{manager} install failed (exit {install.returncode}); previous runtime unchanged. {SETUP}"
        )
    try:
        _validate(runtime, record)
    except OSError, ValueError, KeyError, TypeError:
        raise RuntimeError(
            f"Installed dependencies do not match bundled versions. {SETUP}"
        ) from None
    _request(node, runtime, "providers")
    (runtime / "runtime.json").write_text(json.dumps(record) + "\n", encoding="utf-8")
    link = home / f".current-{uuid4().hex}"
    link.symlink_to(runtime.name, target_is_directory=True)
    link.replace(home / "current")
    return {**record, "runtime": str(runtime)}


def installed_runtime() -> tuple[str, Path]:
    runtime = _home() / "current"
    try:
        record = json.loads((runtime / "runtime.json").read_text())
        _validate(runtime, record)
    except OSError, ValueError, KeyError, TypeError:
        raise RuntimeUnavailable(
            f"AI runtime is missing or mismatched. {SETUP}"
        ) from None
    return _node(), runtime


def metadata(operation: str, provider: str | None = None) -> dict:
    node, runtime = installed_runtime()
    return _request(node, runtime, operation, provider)

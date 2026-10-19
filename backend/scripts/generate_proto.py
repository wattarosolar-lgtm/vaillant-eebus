"""Generate the Python gRPC stubs from the bridge's proto files.

bridge/eebus-bridge/proto is the only source of truth for the bridge API. The
output is never edited by hand and is not committed: CI and the Docker build
run this script.

The stubs land in <out>/eebus/v1/. <out> goes on sys.path (PYTHONPATH, pytest
pythonpath, mypy_path), so the generated absolute imports (`from eebus.v1 import
...`) resolve as they are, without rewriting.

Usage:
    python backend/scripts/generate_proto.py           # runtime stubs only
    python backend/scripts/generate_proto.py --typed   # plus .pyi for mypy
"""

from __future__ import annotations

import argparse
import shutil
import sys
from importlib import resources
from pathlib import Path

from grpc_tools import protoc

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROTO_DIR = REPO_ROOT / "bridge" / "eebus-bridge" / "proto"
DEFAULT_OUT_DIR = REPO_ROOT / "backend" / "gen"
PACKAGE = Path("eebus") / "v1"


def _plugin(name: str) -> str:
    """Return the path of a protoc plugin installed next to the interpreter."""
    found = shutil.which(name, path=str(Path(sys.executable).parent))
    if found is None:
        raise SystemExit(f"protoc plugin {name!r} not found next to {sys.executable}; install requirements-dev.lock")
    return found


def generate(proto_dir: Path, out_dir: Path, *, typed: bool) -> None:
    proto_files = sorted(p.relative_to(proto_dir).as_posix() for p in (proto_dir / PACKAGE).glob("*.proto"))
    if not proto_files:
        raise SystemExit(f"no .proto files in {proto_dir / PACKAGE}")

    shutil.rmtree(out_dir / PACKAGE.parts[0], ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    well_known = resources.files("grpc_tools") / "_proto"
    args = [
        "grpc_tools.protoc",
        f"-I{proto_dir}",
        f"-I{well_known}",
        f"--python_out={out_dir}",
        f"--grpc_python_out={out_dir}",
    ]
    if typed:
        args += [
            f"--plugin=protoc-gen-mypy={_plugin('protoc-gen-mypy')}",
            f"--plugin=protoc-gen-mypy_grpc={_plugin('protoc-gen-mypy_grpc')}",
            f"--mypy_out={out_dir}",
            f"--mypy_grpc_out={out_dir}",
        ]
    if protoc.main([*args, *proto_files]) != 0:
        raise SystemExit("protoc failed")

    for package_dir in (out_dir / PACKAGE.parts[0], out_dir / PACKAGE):
        (package_dir / "__init__.py").touch()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--proto-dir", type=Path, default=DEFAULT_PROTO_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--typed", action="store_true", help="also generate .pyi type stubs (mypy-protobuf)")
    args = parser.parse_args()
    generate(args.proto_dir, args.out, typed=args.typed)


if __name__ == "__main__":
    main()

"""Run with python -I /path/to/openai4s_lab_provider/__main__.py."""

import importlib.util
import os
import sys


def _load_own_package():
    here = os.path.dirname(os.path.abspath(__file__))
    name = "openai4s_lab_provider"
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(here, "__init__.py"), submodule_search_locations=[here]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def scrub_secret_env():
    """Baseline name heuristic, independent of the later host-side allowlist."""
    import re

    for name in list(os.environ):
        upper = name.upper()
        if re.search(
            r"(?:^|_)(?:KEY|TOKEN|SECRET|PASS(?:WORD|WD)?|PWD|PW|PAT|CREDENTIALS?|AUTH|BEARER|COOKIE)(?:_|$)",
            upper,
        ) or upper.startswith(
            ("AWS_", "AZURE_", "GOOGLE_", "OPENAI_", "ANTHROPIC_", "OPENAI4S_SECRET_")
        ):
            os.environ.pop(name, None)


def main():
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    scrub_secret_env()
    _load_own_package()
    import argparse
    import json

    from openai4s_lab_provider.server import Server

    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("toy", "chemgymrl"), required=True)
    parser.add_argument("--describe", metavar="PROFILE")
    parser.add_argument(
        "--portable",
        action="store_true",
        help="with --describe: drop runtime-specific claims (committed manifests)",
    )
    args = parser.parse_args()
    if args.portable and not args.describe:
        parser.error("--portable requires --describe")
    if args.backend == "toy":
        from openai4s_lab_provider.toy import Backend
    else:
        from openai4s_lab_provider.chemgymrl.adapter import Backend
    backend = Backend()
    with os.fdopen(protocol_fd, "wb", buffering=0) as sink:
        if args.describe:
            descriptor = backend.describe(args.describe)
            if args.portable:
                # A committed manifest is read on every machine; a
                # reproducibility claim holds only on the measured runtime.
                descriptor["reproducibility"] = {
                    "status": "unverified",
                    "evidence": None,
                }
            sink.write(
                (
                    json.dumps(
                        descriptor, indent=2, ensure_ascii=False, allow_nan=False
                    )
                    + "\n"
                ).encode("utf-8")
            )
            backend.close()
        else:
            Server(backend).serve(sys.stdin.buffer, sink)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"{type(exc).__name__}: provider startup or service failed", file=sys.stderr
        )
        raise SystemExit(1) from None

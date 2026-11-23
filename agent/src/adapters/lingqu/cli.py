"""``python -m adapters.lingqu.cli`` — operator commands.

All output is JSON or plain text without secrets. ``probe`` and ``doctor`` are
the two commands that can touch the network; the rest are local.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .admin import doctor, list_models, probe
from .catalog import CatalogError
from .gateway import LingquGateway
from .router import RoutingConfigError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lingqu", description="Lingqu model gateway")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list profiles without secrets")
    use = sub.add_parser("use", help="set the active profile (process-local)")
    use.add_argument("profile")
    p = sub.add_parser("probe", help="make one minimal model call")
    p.add_argument("profile", nargs="?")
    sub.add_parser("doctor", help="show credential source and masked fingerprint")
    sub.add_parser("catalog-check", help="validate the catalog only")
    sub.add_parser("routes", help="show per-purpose primary profiles")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        gateway = LingquGateway()
    except (CatalogError, RoutingConfigError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2

    try:
        if args.command == "list":
            print(json.dumps(list_models(gateway), ensure_ascii=False, indent=2))
        elif args.command == "use":
            active = gateway.set_active(args.profile)
            print(json.dumps({"active_profile": active.id}, ensure_ascii=False))
        elif args.command == "probe":
            print(json.dumps(probe(gateway, args.profile).model_dump(), ensure_ascii=False, indent=2))
        elif args.command == "doctor":
            print(json.dumps(doctor(gateway), ensure_ascii=False, indent=2))
        elif args.command == "catalog-check":
            print(
                json.dumps(
                    {
                        "catalog_version": gateway.catalog.catalog_version,
                        "profiles": len(gateway.profiles()),
                        "default_profile": gateway.catalog.default_profile,
                    },
                    ensure_ascii=False,
                )
            )
        elif args.command == "routes":
            print(json.dumps(gateway._policy.all_routes(), ensure_ascii=False, indent=2))  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

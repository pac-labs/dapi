"""CLI for running, validating specs, and generating machine-readable schemas."""

import argparse
import json
import logging
import os
from pathlib import Path

from .spec import Sources, Specification, parse_document


def main():
    parser = argparse.ArgumentParser(prog="looprd")
    commands = parser.add_subparsers(dest="command")
    validate = commands.add_parser("validate")
    validate.add_argument("file", type=Path)
    validate.add_argument("--sources", action="store_true")
    schema = commands.add_parser("schema")
    schema.add_argument("--sources", action="store_true")
    admin_key = commands.add_parser("admin-key", help="Generate a management key or rotate a lost key")
    admin_key.add_argument("--rotate", action="store_true")
    args = parser.parse_args()
    if args.command == "validate":
        contract = Sources if args.sources else Specification
        contract.model_validate(parse_document(args.file.read_text(encoding="utf-8-sig")))
        print(f"Valid {contract.__name__}: {args.file}")
    elif args.command == "schema":
        print(json.dumps((Sources if args.sources else Specification).model_json_schema(), indent=2))
    elif args.command == "admin-key":
        from .admin import announce, provision
        from .config import Settings
        try:
            token = provision(Settings.from_env(), rotate=args.rotate)
        except (OSError, ValueError) as error:
            parser.error(str(error))
        announce(token)
        if token is None:
            print("Management key already configured. Use --rotate to replace a generated key.")
    else:
        import uvicorn
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
        uvicorn.run("looprd.app:create_app", factory=True, host="0.0.0.0",
                    port=int(os.getenv("LOOPRD_PORT", "8080")), workers=1,
                    access_log=False, ws_max_size=1_048_576, timeout_graceful_shutdown=35)


if __name__ == "__main__":
    main()

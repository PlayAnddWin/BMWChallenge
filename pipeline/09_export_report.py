"""Export the complete authoritative backend list, without UI pagination."""
import argparse
from pathlib import Path
import sys
from urllib.parse import quote

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings, resolve_path  # noqa: E402


def main(argv=None):
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base-url", default=settings.api_base_url)
    parser.add_argument("--format", choices=("json", "csv", "markdown"), default="json")
    parser.add_argument("--kind", choices=("requirements", "freeze", "audit", "history"), default="requirements")
    parser.add_argument("--id", dest="entity_id")
    parser.add_argument("--api-prefix", default=settings.api_prefix)
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--output", type=Path, help="File path, resolved from the repository root.")
    output.add_argument("--output-dir", type=Path,
                        help="Directory, resolved from the repository root; defaults to BMW_OUTPUT_DIR.")
    args = parser.parse_args(argv)
    if args.kind != "requirements" and not args.entity_id:
        parser.error("--id is required for freeze/audit/history exports")
    if args.format == "csv" and args.kind != "requirements":
        parser.error("CSV is only supported for requirement exports")
    if args.format == "markdown" and args.kind != "freeze":
        parser.error("Markdown is only supported for frozen plan exports")
    if args.kind == "requirements":
        route = "/requirements/export"
    elif args.kind == "history":
        route = f"/requirements/{quote(args.entity_id, safe='')}/history/export"
    else:
        route = f"/freezes/{quote(args.entity_id, safe='')}/{'audit' if args.kind == 'audit' else 'export'}"
    response = httpx.get(f"{args.api_base_url.rstrip('/')}{args.api_prefix}{route}", params={"format": args.format}, timeout=60)
    response.raise_for_status()
    destination = (resolve_path(args.output) if args.output is not None else
                   (resolve_path(args.output_dir) if args.output_dir is not None else settings.output_dir)
                   / f"{args.kind}.{args.format}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(response.text, encoding="utf-8")


if __name__ == "__main__":
    main()

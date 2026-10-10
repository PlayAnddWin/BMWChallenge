"""Export the complete authoritative backend list, without UI pagination."""
import argparse
import os
from pathlib import Path
import httpx


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base-url", default=os.getenv("API_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--format", choices=("json", "csv", "markdown"), default="json")
    parser.add_argument("--kind", choices=("requirements", "freeze", "audit", "history"), default="requirements")
    parser.add_argument("--id", dest="entity_id")
    parser.add_argument("--api-prefix", default=os.getenv("API_PREFIX", "/api"))
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--output", type=Path)
    output.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    if args.kind != "requirements" and not args.entity_id:
        parser.error("--id is required for freeze/audit/history exports")
    if args.format == "csv" and args.kind != "requirements":
        parser.error("CSV is only supported for requirement exports")
    if args.format == "markdown" and args.kind != "freeze":
        parser.error("Markdown is only supported for frozen plan exports")
    from urllib.parse import quote
    if args.kind == "requirements":
        route = "/requirements/export"
    elif args.kind == "history":
        route = f"/requirements/{quote(args.entity_id, safe='')}/history/export"
    else:
        route = f"/freezes/{quote(args.entity_id, safe='')}/{'audit' if args.kind == 'audit' else 'export'}"
    response = httpx.get(f"{args.api_base_url.rstrip('/')}{args.api_prefix}{route}", params={"format": args.format}, timeout=60)
    response.raise_for_status()
    destination = args.output or args.output_dir / f"{args.kind}.{args.format}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(response.text, encoding="utf-8")


if __name__ == "__main__":
    main()

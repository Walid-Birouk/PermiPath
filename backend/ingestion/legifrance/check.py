"""Live check of the PISTE credentials: token -> locate CESEDA -> article in force on a date.

    cd backend && uv run python -m ingestion.legifrance.check --article L422-1 --date 2026-11-26

Reads PISTE_CLIENT_ID / PISTE_CLIENT_SECRET (and optional PISTE_ENV) from the environment or the
repository `.env`. Prints no secrets. `--dump DIR` saves the raw API responses as JSON, to be
reused as test fixtures.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import ROOT_ENV_FILE
from ingestion.legifrance.client import LegifranceClient, LegifranceError
from ingestion.legifrance.settings import PisteSettings

EXIT_OK, EXIT_API_ERROR, EXIT_CONFIG_ERROR = 0, 1, 2
TEXT_PREVIEW_CHARS = 500


def _dumper(directory: Path) -> Any:
    directory.mkdir(parents=True, exist_ok=True)

    def dump(path: str, payload: Any) -> None:
        target = directory / f"{path.replace('/', '_')}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return dump


def main(
    argv: list[str] | None = None,
    *,
    env_file: Path | None = ROOT_ENV_FILE,
    http: httpx.Client | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--article", default="L422-1", help="CESEDA article number")
    parser.add_argument(
        "--date", type=date.fromisoformat, default=date.today(), help="YYYY-MM-DD (default: today)"
    )
    parser.add_argument("--dump", type=Path, help="directory for raw JSON responses")
    args = parser.parse_args(argv)

    try:
        settings = PisteSettings(_env_file=env_file)
    except ValidationError as exc:
        missing = ", ".join(f"PISTE_{'_'.join(map(str, e['loc'])).upper()}" for e in exc.errors())
        print(f"Config error: check {missing} in .env (see .env.example)", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    endpoints = settings.endpoints()
    print(f"PISTE env: {settings.env}  API: {endpoints.api_url}")
    client = LegifranceClient(
        endpoints,
        settings.client_id,
        settings.client_secret.get_secret_value(),
        http=http,
        on_response=_dumper(args.dump) if args.dump else None,
    )
    try:
        with client:
            client.access_token()
            print(f"1. Token: OK (expires in {client.token_expires_in} s)")
            code = client.find_code()
            print(f"2. CESEDA: {code.id}  {code.title}  [{code.status}]")
            article = client.article_in_force(args.article, args.date)
    except LegifranceError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return EXIT_API_ERROR

    end = article.in_force_to.isoformat() if article.in_force_to else "no end date"
    start = article.in_force_from.isoformat() if article.in_force_from else "?"
    print(f"3. Article {article.num} in force on {args.date.isoformat()}:")
    print(f"   id:       {article.id}")
    print(f"   status:   {article.status}")
    print(f"   period:   {start} -> {end}")
    print(f"   link:     {article.url(args.date)}")
    print(f"   text:     {article.text[:TEXT_PREVIEW_CHARS]}")
    print("Source: Légifrance / DILA, Licence Ouverte 2.0")
    if args.dump:
        print(f"Raw responses saved in {args.dump}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

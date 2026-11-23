#!/usr/bin/env python3
"""Seed LiteLLM_ProxyModelTable from a config.yaml model_list.

DB mode makes the database the source of truth for model deployments. A fresh
database starts empty, so the YAML model_list is only the bootstrap: this script
posts each entry to `/model/new`, which encrypts the credential fields with
LITELLM_SALT_KEY and writes a `LiteLLM_ProxyModelTable` row.

It is idempotent by (model_name, litellm_params.model): an entry already present
as a DB model is skipped. It never prints API keys.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import yaml


def _request(method: str, url: str, key: str, payload: dict | None = None) -> tuple[int, object]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {key}")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = body
        return exc.code, parsed


def _existing(base_url: str, key: str) -> dict[tuple[str, str], dict]:
    status, body = _request("GET", f"{base_url}/model/info", key)
    if status != 200 or not isinstance(body, dict):
        raise SystemExit(f"GET /model/info failed: http={status} body={str(body)[:300]}")
    rows = body.get("data") or []
    out: dict[tuple[str, str], dict] = {}
    for row in rows:
        info = row.get("model_info") or {}
        if info.get("db_model") is not True:
            continue
        params = row.get("litellm_params") or {}
        model = params.get("model") if isinstance(params, dict) else None
        if isinstance(model, str):
            out[(str(row.get("model_name")), model)] = row
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/opt/yishu/litellm/config.with-db.yaml")
    parser.add_argument("--base-url", default="http://127.0.0.1:4000")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    key = (os.getenv("LITELLM_MASTER_KEY") or "").strip()
    if not key:
        raise SystemExit("LITELLM_MASTER_KEY is required")
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    model_list = config.get("model_list") or []
    if not model_list:
        raise SystemExit(f"{args.config} has no model_list to seed")

    existing = _existing(args.base_url, key)
    created = skipped = 0
    for entry in model_list:
        name = str(entry.get("model_name") or "")
        params = entry.get("litellm_params") or {}
        model = str(params.get("model") or "")
        if not name or not model:
            print(f"SKIP malformed entry: {entry!r}", file=sys.stderr)
            continue
        if (name, model) in existing:
            print(f"SKIP {name} -> {model} (already in DB)")
            skipped += 1
            continue
        payload = {
            "model_name": name,
            "litellm_params": params,
            "model_info": {"id": str(uuid.uuid4())},
        }
        if args.dry_run:
            print(f"DRY  {name} -> {model}")
            continue
        status, body = _request("POST", f"{args.base_url}/model/new", key, payload)
        if status != 200:
            raise SystemExit(f"POST /model/new failed for {name}: http={status} body={str(body)[:400]}")
        print(f"ADD  {name} -> {model} (model_id={body.get('model_id')})")
        created += 1

    status, body = _request("GET", f"{args.base_url}/model/info", key)
    rows = body.get("data") if isinstance(body, dict) else None
    db_rows = [
        row
        for row in rows or []
        if (row.get("model_info") or {}).get("db_model") is True
    ]
    print(f"DONE created={created} skipped={skipped} db_models={len(db_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

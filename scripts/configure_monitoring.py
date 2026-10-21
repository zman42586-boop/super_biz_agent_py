"""Generate a machine-specific local scrape config; never modifies .env."""

import argparse
import json
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def local_config(host_id: str, *, remote_url: str = "", token_file: str = "") -> dict:
    if not host_id or len(host_id) > 128 or any(ord(c) < 32 for c in host_id):
        raise ValueError("Invalid host_id")
    value = {"global": {"scrape_interval": "5s", "scrape_timeout": "4s", "evaluation_interval": "5s"},
             "scrape_configs": [
                 {"job_name": "windows", "static_configs": [{"targets": ["127.0.0.1:9182"], "labels": {"host_id": host_id}}]},
             ]}
    if remote_url:
        parsed = urlparse(remote_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Use an HTTP(S) remote_write URL without embedded credentials")
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} and parsed.scheme != "https":
            raise ValueError("Remote cloud writes require HTTPS")
        remote = {"url": remote_url}
        if token_file:
            remote["authorization"] = {"type": "Bearer", "credentials_file": str(Path(token_file).resolve())}
        elif parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Cloud writes require PROMETHEUS_REMOTE_WRITE_TOKEN_FILE")
        value["remote_write"] = [remote]
    return value


def main():
    load_dotenv(ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-id", default=socket.gethostname())
    parser.add_argument("--output", type=Path, default=ROOT / "monitoring/runtime/prometheus.yml")
    parser.add_argument("--remote-write-url", default=os.getenv("PROMETHEUS_REMOTE_WRITE_URL", ""))
    args = parser.parse_args()
    value = local_config(args.host_id, remote_url=args.remote_write_url,
                         token_file=os.getenv("PROMETHEUS_REMOTE_WRITE_TOKEN_FILE", ""))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True), encoding="utf-8")
    (args.output.parent / "ports.json").write_text(json.dumps([9182, 9090]), encoding="utf-8")
    print(f"Generated {args.output}; host_id={args.host_id}; remote_write={bool(value.get('remote_write'))}")


if __name__ == "__main__":
    main()

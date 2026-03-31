#!/usr/bin/env python3
"""
Load / smoke test for Railway readiness (stdlib only — no extra pip deps).

1) Default: many concurrent GETs to /api/health/ (and optional /api/system/health/) to stress the web tier.
2) Optional: concurrent POST /api/student/exams/<exam_id>/submit — each line in --submit-specs-file must be a
   UNIQUE student session (token + exam_id + attempt_id). Reusing one attempt will return 400 after the first success.

Note: Exam submit is handled synchronously in Django views today — this exercises gunicorn + DB + Redis (if used),
not Celery queue depth. Use a Celery task load test separately if you add async grading/notifications.

Examples:
  python scripts/loadtest_railway_readiness.py --base-url https://your-app.up.railway.app --requests 100
  python scripts/loadtest_railway_readiness.py --base-url http://127.0.0.1:8001 --requests 50 --system-health

Submit (JSONL: one JSON object per line: {"token":"eyJ...","exam_id":1,"attempt_id":2}):
  python scripts/loadtest_railway_readiness.py --base-url https://api.example.com --submit-specs-file specs.jsonl
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse


def _client_ssl_ctx(disable_verify: bool) -> ssl.SSLContext | None:
    if disable_verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float,
    insecure: bool,
) -> tuple[int, str]:
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    ctx = _client_ssl_ctx(insecure)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.getcode(), (resp.read(8192) or b"").decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        chunk = e.read(4096) if e.fp else b""
        return e.code, chunk.decode("utf-8", errors="replace")


def run_health_batch(base: str, path: str, n: int, timeout: float, insecure: bool) -> tuple[int, int, float]:
    url = urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    ok = 0
    fail = 0
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(n, 150)) as ex:
        futs = [
            ex.submit(_request, "GET", url, headers={}, body=None, timeout=timeout, insecure=insecure)
            for _ in range(n)
        ]
        for f in concurrent.futures.as_completed(futs):
            code, _ = f.result()
            if 200 <= code < 300:
                ok += 1
            else:
                fail += 1
    elapsed = time.perf_counter() - t0
    return ok, fail, elapsed


@dataclass
class SubmitSpec:
    token: str
    exam_id: int
    attempt_id: int


def load_submit_specs(path: str) -> list[SubmitSpec]:
    out: list[SubmitSpec] = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            row = json.loads(line)
            out.append(
                SubmitSpec(
                    token=str(row["token"]),
                    exam_id=int(row["exam_id"]),
                    attempt_id=int(row["attempt_id"]),
                )
            )
    if not out:
        raise SystemExit(f"No specs in {path}")
    return out


def run_submits(base: str, specs: list[SubmitSpec], timeout: float, insecure: bool) -> tuple[int, int, float]:
    """Parallel POST submit; each spec should be a distinct in-progress attempt."""
    ok = 0
    fail = 0
    t0 = time.perf_counter()
    n = len(specs)

    def one(spec: SubmitSpec) -> int:
        path = f"api/student/exams/{spec.exam_id}/submit"
        url = urljoin(base.rstrip("/") + "/", path)
        payload = json.dumps(
            {"attemptId": spec.attempt_id, "answers": [], "cheatingDetected": False}
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {spec.token}",
        }
        code, _ = _request("POST", url, headers=headers, body=payload, timeout=timeout, insecure=insecure)
        return code

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(n, 150)) as ex:
        futs = [ex.submit(one, s) for s in specs]
        for f in concurrent.futures.as_completed(futs):
            code = f.result()
            if 200 <= code < 300:
                ok += 1
            else:
                fail += 1
    elapsed = time.perf_counter() - t0
    return ok, fail, elapsed


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Concurrent health / submit probe for Bekrin API")
    p.add_argument("--base-url", required=True, help="API origin, e.g. https://x.up.railway.app (no /api required)")
    p.add_argument("--requests", type=int, default=80, help="Concurrent health requests (50–150 typical)")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--insecure", action="store_true", help="Disable TLS verification (dev only)")
    p.add_argument("--system-health", action="store_true", help="Hit /api/system/health/ instead of /api/health/")
    p.add_argument(
        "--submit-specs-file",
        default="",
        help="JSONL of {token, exam_id, attempt_id} per line for parallel exam submits",
    )
    args = p.parse_args(argv)

    base = args.base_url.rstrip("/")
    if base.endswith("/api"):
        base = base[: -len("/api")].rstrip("/")
    parsed = urlparse(base)
    if not parsed.scheme or not parsed.netloc:
        print("ERROR: --base-url must include scheme and host", file=sys.stderr)
        return 2

    health_path = "/api/system/health/" if args.system_health else "/api/health/"
    print(f"GET {health_path} × {args.requests} …")
    ok, fail, elapsed = run_health_batch(base, health_path, args.requests, args.timeout, args.insecure)
    print(f"  ok={ok} fail={fail} in {elapsed:.2f}s ({args.requests / elapsed:.1f} req/s)")

    if args.submit_specs_file:
        specs = load_submit_specs(args.submit_specs_file)
        print(f"POST /api/student/exams/{{id}}/submit × {len(specs)} …")
        s_ok, s_fail, s_elapsed = run_submits(base, specs, args.timeout, args.insecure)
        print(f"  ok={s_ok} fail={s_fail} in {s_elapsed:.2f}s")

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

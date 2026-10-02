"""End-to-end check against a deployed PriorPath: demo → explain → review → letter → export.

python scripts/smoke.py https://priorpath.vercel.app [--require-explanations]
"""

import argparse
import json
import sys
from typing import Any

import httpx

EXPLAINED = ("error", "outlier", "lead")


def fail(what: str, got: object) -> SystemExit:
    return SystemExit(f"FAIL: {what} — got {str(got)[:300]}")


def call(c: httpx.Client, method: str, url: str, **kw: Any) -> httpx.Response:
    try:
        r = c.request(method, url, **kw)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise fail(f"{method} {url}", e) from e
    return r


def parse_events(stream: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in stream.strip().split("\n\n"):
        lines = block.split("\n")
        name = next((x[7:] for x in lines if x.startswith("event: ")), None)
        data = next((x[6:] for x in lines if x.startswith("data: ")), None)
        if name and data:
            events.append((name, json.loads(data)))
    return events


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base_url")
    ap.add_argument("--require-explanations", action="store_true")
    args = ap.parse_args()
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=120) as c:
        health = call(c, "GET", "/api/health").json()
        if health.get("status") != "ok":
            raise fail("health", health)
        cases = call(c, "GET", "/api/cases").json()
        if len(cases) < 10:  # 10 FHIR demo cases, plus PDF ones once their extractions are recorded
            raise fail("expected at least 10 demo cases", len(cases))
        case = next(x for x in cases if x["error_count"] > 0)
        events = parse_events(call(c, "POST", f"/api/cases/{case['id']}/explain").text)
        errors = [d for n, d in events if n == "error"]
        if errors:
            raise fail("explain stream error events", errors)
        done = [d for n, d in events if n == "done"]
        if not done:
            raise fail("no done event in explain stream", [n for n, _ in events])
        detail = call(c, "GET", f"/api/cases/{case['id']}").json()
        explainable = [f for f in detail["flags"] if f["severity"] in EXPLAINED]
        ready = sum(f["explanation_status"] == "ready" for f in explainable)
        if ready < len(explainable):
            msg = f"{ready}/{len(explainable)} explainable flags have ready explanations"
            if args.require_explanations:
                raise fail(msg, [f["explanation_status"] for f in explainable])
            print(f"WARNING: {msg}", file=sys.stderr)
        error_flag = next((f for f in detail["flags"] if f["severity"] == "error"), None)
        if error_flag is None:
            raise fail("no error-severity flag in case detail", detail["flags"])
        call(c, "PATCH", f"/api/flags/{error_flag['id']}", json={"status": "accepted"})
        letter = call(c, "POST", f"/api/cases/{case['id']}/letter").json()
        approved = call(c, "POST", f"/api/letters/{letter['id']}/approve").json()
        if approved["status"] != "approved":
            raise fail("letter approval", approved)
        exported = call(c, "GET", f"/api/letters/{letter['id']}/export?format=txt")
        if error_flag["message"] not in exported.text:
            raise fail("export missing accepted flag message", exported.text)
        print(
            f"ok: case {case['claim_id']}, explanations {done[-1]}, "
            f"{ready}/{len(explainable)} flags explained, letter {len(exported.text)} chars"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

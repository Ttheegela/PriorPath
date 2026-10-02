"""End-to-end check against a deployed PriorPath: demo → explain → review → letter → export.

python scripts/smoke.py https://priorpath.vercel.app
"""

import json
import sys

import httpx


def last_done(stream: str) -> dict[str, object]:
    """Data of the last SSE block whose event line is `done` (error events may follow or precede)."""
    done: dict[str, object] = {}
    for block in stream.strip().split("\n\n"):
        lines = block.split("\n")
        if "event: done" in lines:
            done = json.loads(next(x for x in lines if x.startswith("data: "))[6:])
    assert done, f"no done event in stream: {stream[-300:]!r}"
    return done


def main() -> int:
    base = sys.argv[1].rstrip("/")
    with httpx.Client(base_url=base, timeout=120) as c:
        assert c.get("/api/health").json() == {"status": "ok"}
        cases = c.get("/api/cases").json()
        assert len(cases) == 10, f"expected 10 demo cases, got {len(cases)}"
        case = next(x for x in cases if x["error_count"] > 0)
        done = last_done(c.post(f"/api/cases/{case['id']}/explain").text)
        detail = c.get(f"/api/cases/{case['id']}").json()
        error_flag = next(f for f in detail["flags"] if f["severity"] == "error")
        assert c.patch(f"/api/flags/{error_flag['id']}", json={"status": "accepted"}).status_code == 200
        letter = c.post(f"/api/cases/{case['id']}/letter").json()
        assert c.post(f"/api/letters/{letter['id']}/approve").json()["status"] == "approved"
        exported = c.get(f"/api/letters/{letter['id']}/export?format=txt")
        assert exported.status_code == 200 and error_flag["message"] in exported.text
        print(f"ok: case {case['claim_id']}, explanations {done}, letter {len(exported.text)} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

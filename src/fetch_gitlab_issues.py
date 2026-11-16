"""Pull every public gitlab-org/gitlab issue labeled `customer` created in the
analysis window, keep the labels that say which product stage it belongs to.

Public API, no token needed. Output: data/real/gitlab_customer_issues.csv
"""
import csv
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "real" / "gitlab_customer_issues.csv"
PROJECT = 278964  # gitlab-org/gitlab
START, END = "2024-11-01T00:00:00Z", "2026-11-01T00:00:00Z"


def label(labels, prefix):
    hits = [l.split("::", 1)[1] for l in labels if l.startswith(prefix + "::")]
    return hits[0] if hits else ""


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "support-economics-demo"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r), r.headers.get("Link", "")
        except Exception as e:  # GitLab.com returns occasional 500/502
            print(f"retry {attempt + 1}: {e}")
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"gave up on {url}")


def to_row(i):
    L = i["labels"]
    return {"iid": i["iid"], "created_at": i["created_at"], "closed_at": i["closed_at"] or "",
            "state": i["state"], "stage": label(L, "devops"), "section": label(L, "section"),
            "group": label(L, "group"), "type": label(L, "type"), "severity": label(L, "severity"),
            "priority": label(L, "priority"), "upvotes": i["upvotes"], "notes": i["user_notes_count"]}


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # one month at a time: the full window in one query times out on GitLab.com
    edges = [f"{y}-{m:02d}-01T00:00:00Z" for y in (2024, 2025, 2026) for m in range(1, 13)]
    edges = [e for e in edges if START <= e <= END]
    rows = []
    for a, b in zip(edges, edges[1:]):
        url = (f"https://gitlab.com/api/v4/projects/{PROJECT}/issues?labels=customer&state=all"
               f"&created_after={a}&created_before={b}&per_page=100&order_by=created_at&sort=asc")
        while url:
            page, link = get(url)
            rows += [to_row(i) for i in page]
            nxt = [p.split(";")[0].strip(" <>") for p in link.split(",") if 'rel="next"' in p]
            url = nxt[0] if nxt else None
            time.sleep(0.3)
        print(a[:7], len(rows))
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} issues -> {OUT}")


if __name__ == "__main__":
    main()

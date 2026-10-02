"""Rebuild docs/api/endpoints.md from docs/api/openapi.yaml.

    cd backend && python manage.py spectacular --file ../docs/api/openapi.yaml
    python ../docs/api/make_index.py
"""
import collections
import pathlib

import yaml

HERE = pathlib.Path(__file__).parent
spec = yaml.safe_load((HERE / "openapi.yaml").read_text())
groups = collections.defaultdict(list)
count = 0
for path, ops in spec["paths"].items():
    for method, op in ops.items():
        if method not in ("get", "post", "put", "patch", "delete"):
            continue
        tag = (op.get("tags") or ["other"])[0]
        summary = op.get("summary") or op.get("description", "").split("\n")[0]
        groups[tag].append(f"| {method.upper()} | `{path.replace('/api/v1', '')}` | {summary} |")
        count += 1
lines = ["# API endpoint index", "",
         f"Generated from `openapi.yaml` ({count} operations). Base URL: `/api/v1`. Full request and response",
         "shapes are in `openapi.yaml`; this list is short enough to paste into a chat.", ""]
for tag in sorted(groups):
    lines += [f"## {tag}", "", "| Method | Path | What it does |", "|---|---|---|", *groups[tag], ""]
(HERE / "endpoints.md").write_text("\n".join(lines))
print(f"{count} operations")

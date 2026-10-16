"""Lock the installed, tested dependency closure. Run in the dedicated API virtualenv."""

import importlib.metadata as md
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

root = Path(__file__).resolve().parents[1]


def closure(requirements):
    pending = [Requirement(r) for r in requirements]
    visited, packages = set(), {}
    while pending:
        req = pending.pop()
        name = canonicalize_name(req.name)
        identity = (name, tuple(sorted(req.extras)))
        if identity in visited:
            continue
        visited.add(identity)
        distribution = md.distribution(req.name)
        packages[name] = distribution.version
        for value in distribution.requires or []:
            child = Requirement(value)
            if child.marker is None or any(child.marker.evaluate({"extra": e}) for e in ["", *req.extras]):
                pending.append(child)
    return packages


requirements = [
    line.strip()
    for line in (root / "requirements-api.in").read_text().splitlines()
    if line.strip() and not line.startswith("#")
]
for filename, extra in [("requirements-api.txt", []), ("requirements-api-dev.txt", ["pytest>=8,<10", "ruff>=0.9,<1"])]:
    packages = closure(requirements + extra)
    content = "# Exact versions from the isolated, tested environment. Regenerate with tools/lock_api.py.\n"
    content += "\n".join(f"{name}=={version}" for name, version in sorted(packages.items())) + "\n"
    (root / filename).write_text(content, encoding="utf-8")
    print(filename, len(packages), "packages")

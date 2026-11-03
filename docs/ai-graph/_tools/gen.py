"""Generate docs/ai-graph/code/src/**/*.md file notes from content.py + AST/import analysis."""
import ast
import collections
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
VAULT = REPO / "docs" / "ai-graph"
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from content import FILES  # noqa: E402

DATE = __import__("datetime").date.today().isoformat()


def git_files(*paths):
    out = subprocess.check_output(["git", "ls-files", *paths], cwd=REPO, text=True)
    return [f for f in out.split() if f.endswith(".py")]


src_files = git_files("src")
mods = {}
for f in src_files:
    m = f[len("src/"):-3].replace("/", ".")
    if m.endswith(".__init__"):
        m = m[: -len(".__init__")]
    mods[m] = f

deps = collections.defaultdict(set)
users = collections.defaultdict(set)
for f in sorted(set(git_files("src", "scripts", "tests"))):
    tree = ast.parse((REPO / f).read_text(encoding="utf-8"))
    pkg = f[4:-3].replace("/", ".").rsplit(".", 1)[0] if f.startswith("src/") else None
    for n in ast.walk(tree):
        targets = []
        if isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                parts = pkg.split(".")
                parts = parts[: len(parts) - (n.level - 1)]
                base = ".".join(parts + ([base] if base else []))
            for a in n.names:
                if f"{base}.{a.name}" in mods:
                    targets.append(f"{base}.{a.name}")
                elif base in mods:
                    targets.append(base)
        elif isinstance(n, ast.Import):
            targets = [a.name for a in n.names if a.name in mods]
        for t in targets:
            tf = mods[t]
            if tf != f:
                users[tf].add(f)
                deps[f].add(tf)


def link(f):
    p = f[:-3] + ".py" if f.endswith(".py") else f
    if p.endswith("__init__.py") or p == "src/krisha/numfmt.py":
        # fold package inits into dir notes
        return f"[[code/{pathlib.PurePosixPath(p).parent}/_dir]]"
    return f"[[code/{p}]]"


def symbol_line(path, name):
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    parts = name.split(".")
    nodes = tree.body
    found = None
    for i, part in enumerate(parts):
        found = None
        for n in nodes:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == part:
                found = n
                break
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                tgts = n.targets if isinstance(n, ast.Assign) else [n.target]
                if any(isinstance(t, ast.Name) and t.id == part for t in tgts):
                    found = n
                    break
        if found is None:
            raise SystemExit(f"symbol not found: {path}:{name}")
        nodes = getattr(found, "body", [])
    return found.lineno


def write_note(path, c):
    lines = ["---", "type: file", f"path: {path}"]
    tags = ["zone/src", *c.get("tags", [])]
    lines.append(f"tags: [{', '.join(tags)}]")
    lines.append(f"updated: {DATE}")
    lines.append("---")
    lines.append(f"# {path}")
    lines.append(f"purpose: {c['purpose']}")
    if c.get("concepts"):
        lines.append("concepts: " + ", ".join(f"[[concepts/{x}]]" for x in c["concepts"]))
    if c.get("symbols"):
        lines.append("## Key symbols")
        for name, desc in c["symbols"]:
            ln = symbol_line(path, name)
            lines.append(f"- `{name}` — {desc} `{path}:{ln}`")
    d = sorted(deps.get(path, ()))
    if d or c.get("depends_extra"):
        lines.append("## Depends on")
        for x in d:
            lines.append(f"- {link(x)}")
        for x in c.get("depends_extra", []):
            lines.append(f"- {x}")
    u = sorted(users.get(path, ()))
    if u or c.get("used_extra"):
        lines.append("## Used by")
        src_u = [x for x in u if x.startswith("src/")]
        scr_u = [x for x in u if x.startswith("scripts/")]
        tst_u = [x for x in u if x.startswith("tests/")]
        for x in src_u + scr_u:
            lines.append(f"- {link(x)}")
        for x in c.get("used_extra", []):
            lines.append(f"- {x}")
        if tst_u:
            lines.append("- tests: " + ", ".join(link(x) for x in tst_u))
    if c.get("data"):
        lines.append("## Data / env")
        for x in c["data"]:
            lines.append(f"- {x}")
    if c.get("gotchas"):
        lines.append("## Gotchas")
        for x in c["gotchas"]:
            lines.append(f"- {x}")
    out = VAULT / "code" / (path + ".md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


written = []
for path, c in FILES.items():
    written.append(write_note(path, c))
missing = [f for f in src_files if f not in FILES and not f.endswith("__init__.py") and f != "src/krisha/numfmt.py"]
print("written", len(written))
print("missing notes:", missing)

"""Repoint dangling wikilinks to the nearest existing _dir note (keeps the target as alias). Run after gen.py."""
import pathlib, re, sys
root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parents[1]
notes = {p.relative_to(root).with_suffix('').as_posix() for p in root.rglob('*.md')}
link_re = re.compile(r'\[\[([^\]|#]+)((?:#[^\]|]*)?)((?:\|[^\]]*)?)\]\]')
ALIASES = {}

def resolve(t):
    if t in notes: return t
    if t in ALIASES: return ALIASES[t]
    parts = t.split('/')
    for i in range(len(parts) - 1, 0, -1):
        cand = '/'.join(parts[:i]) + '/_dir'
        if cand in notes: return cand
    return None

changed = 0
for p in root.rglob('*.md'):
    if p.name == '_CONVENTIONS.md': continue
    text = p.read_text(encoding='utf-8')
    def sub(m):
        global changed
        t = m.group(1).strip()
        r = resolve(t)
        if r is None or r == t: return m.group(0)
        changed += 1
        alias = m.group(3) or '|' + t.removeprefix('code/')
        return f'[[{r}{m.group(2)}{alias}]]'
    new = link_re.sub(sub, text)
    if new != text: p.write_text(new, encoding='utf-8')
print('rewritten links:', changed)

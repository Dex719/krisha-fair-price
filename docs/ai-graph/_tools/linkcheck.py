import re, sys, pathlib, collections
root = pathlib.Path(sys.argv[1])
notes = {p.relative_to(root).with_suffix('').as_posix() for p in root.rglob('*.md') if '.obsidian' not in p.parts}
link_re = re.compile(r'\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]')
dangling = collections.Counter(); inbound = collections.Counter(); total = 0
for p in root.rglob('*.md'):
    src = p.relative_to(root).with_suffix('').as_posix()
    for m in link_re.finditer(p.read_text(encoding='utf-8', errors='replace')):
        t = m.group(1).strip().removesuffix('.md'); total += 1
        if t in notes: inbound[t] += 1
        else: dangling[t] += 1
orphans = sorted(n for n in notes if inbound[n] == 0)
print(f'notes={len(notes)} links={total} dangling_targets={len(dangling)} orphans={len(orphans)}')
print('--- dangling (target: count)')
for t, c in dangling.most_common(): print(f'{t}: {c}')
print('--- orphans')
for o in orphans: print(o)

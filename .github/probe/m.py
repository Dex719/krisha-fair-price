import json, re, subprocess, time
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
def get(url, ref="https://krisha.kz/map/prodazha/kvartiry/almaty/"):
    p = subprocess.run(["curl", "-g", "-s", "-A", UA, "-H", "X-Requested-With: XMLHttpRequest", "-H", "Accept: application/json, text/plain, */*",
                        "-e", ref, "-o", "/tmp/b", "-w", "%{http_code} %{size_download}", url], capture_output=True, text=True, timeout=60)
    time.sleep(3)
    return p.stdout, open("/tmp/b", encoding="utf-8", errors="replace").read()
L = "https://krisha.kz/a/ajax-map-list/map/prodazha/kvartiry/"
for url in [L + "almaty-bostandykskij/?das[live.rooms][]=2&page=1",
            L + "almaty-bostandykskij/?das[live.rooms][]=2&page=2",
            L + "almaty-medeuskij/?das[live.rooms][]=1&page=1",
            L + "almaty/?page=1&das[who]=1",
            "https://krisha.kz/a/ajax-map-list/map/arenda/kvartiry/almaty-alatauskij/?das[live.rooms][]=1&page=1"]:
    meta, b = get(url)
    d = json.loads(b) if b.startswith("{") else {}
    adv = d.get("adverts") or {}
    pages = re.findall(r'data-page="(\d+)"', str(d.get("pager") or ""))
    h = d.get("html") or ""
    prev = re.findall(r'class="a-card__text-preview[^"]*"[^>]*>(.*?)</div>', h, re.S)
    titles = re.findall(r'a-card__title[^>]*>([^<]+)', h)
    subs = re.findall(r'a-card__subtitle[^>]*>\s*([^<]+)', h)
    print("==", meta, url)
    print("  n", len(adv), "maxpage", max(map(int, pages)) if pages else None, "previews", len(prev), "titles", len(titles))
    if adv:
        k = next(iter(adv)); v = dict(adv[k]); v.pop("photos", None)
        print("  keys", sorted(adv[k].keys()))
        print("  sample", json.dumps(v, ensure_ascii=False)[:600], "photos", len(adv[k].get("photos") or []))
    for t in prev[:2]:
        print("  prev:", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip()[:160])
    print("  titles", titles[:2], "subs", [s.strip()[:60] for s in subs[:2]])
    if not adv:
        print("  raw", b[:300])
# card html structure sample
meta, b = get(L + "almaty-medeuskij/?das[live.rooms][]=1&page=1")
h = json.loads(b)["html"]
i = h.find("a-card__text-preview")
print("HTMLSAMPLE", re.sub(r"\s+", " ", h[:200]))
j = h.find("<div data-id"); j = j if j >= 0 else 0
print("CARDHTML", h[j:j+7000])

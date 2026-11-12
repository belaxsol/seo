"""Crawler d'audit + règles d'analyse (communes au crawl et à l'import Screaming Frog)."""
import datetime as dt
import re
import time
from collections import Counter, deque
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import db

UA = "SeoLibre/0.1 (crawler d'audit local)"
SKIP_EXT = re.compile(r"\.(jpe?g|png|gif|webp|svg|ico|pdf|zip|rar|gz|mp[34]|avi|mov|css|js|"
                      r"woff2?|ttf|eot|xml|json|docx?|xlsx?|pptx?)(\?|$)", re.I)
STATUS = {}  # project_id -> {"running", "n", "error"}


def site_key(url):
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def norm_url(url):
    url = urldefrag(url.strip())[0]
    p = urlparse(url)
    return url + "/" if not p.path else url


def _rel_has(value, word):
    if not value:
        return False
    parts = value if isinstance(value, list) else value.split()
    return word in [v.lower() for v in parts]


def fetch_page(s, url):
    page = {"url": url, "status": 0, "content_type": None, "title": None, "meta_desc": None,
            "h1": None, "h1_count": 0, "word_count": None, "canonical": None, "indexable": 0,
            "inlinks": 0, "response_ms": None, "redirect_to": None, "links": []}
    t = time.time()
    try:
        r = s.get(url, timeout=15, allow_redirects=False)
    except requests.RequestException:
        return page
    page["response_ms"] = int((time.time() - t) * 1000)
    page["status"] = r.status_code
    page["content_type"] = r.headers.get("Content-Type", "").split(";")[0].strip()
    if 300 <= r.status_code < 400:
        loc = r.headers.get("Location")
        if loc:
            page["redirect_to"] = urljoin(url, loc)
            page["links"] = [page["redirect_to"]]
        return page
    if r.status_code != 200 or "html" not in page["content_type"]:
        return page
    soup = BeautifulSoup(r.text, "html.parser")
    if soup.title:
        page["title"] = soup.title.get_text(" ", strip=True) or None
    m = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    if m and m.get("content", "").strip():
        page["meta_desc"] = m["content"].strip()
    robots = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    noindex = "noindex" in ((robots.get("content", "") if robots else "") + " " +
                            r.headers.get("X-Robots-Tag", "")).lower()
    link = soup.find("link", rel=lambda v: _rel_has(v, "canonical"))
    if link and link.get("href"):
        page["canonical"] = norm_url(urljoin(url, link["href"]))
    h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1")]
    page["h1_count"] = len(h1s)
    page["h1"] = h1s[0] if h1s and h1s[0] else None
    page["links"] = [urljoin(url, a["href"]) for a in soup.find_all("a", href=True)]
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = (soup.body or soup).get_text(" ")
    page["word_count"] = len(re.findall(r"\w+", text))
    page["indexable"] = 0 if noindex or (page["canonical"] and page["canonical"] != url) else 1
    return page


def crawl(project_id, start_url, max_pages=200, respect_robots=True, delay=0.2):
    """Parcourt le site en largeur et enregistre les pages. Prévu pour tourner dans un thread."""
    st = STATUS[project_id] = {"running": True, "n": 0, "error": None}
    con = db.connect()
    try:
        start = norm_url(start_url)
        site = site_key(start)
        crawl_id = con.execute(
            "INSERT INTO crawls(project_id, source, started) VALUES(?,?,?)",
            (project_id, "crawl", dt.datetime.now().isoformat(timespec="seconds"))).lastrowid
        con.commit()
        rp = None
        if respect_robots:
            try:
                rp = robotparser.RobotFileParser(urljoin(start, "/robots.txt"))
                rp.read()
            except Exception:
                rp = None
        s = requests.Session()
        s.headers["User-Agent"] = UA
        queue, seen, inlinks = deque([(start, 0)]), {start}, {}
        while queue and st["n"] < max_pages:
            url, depth = queue.popleft()
            if rp and not rp.can_fetch(UA, url):
                continue
            page = fetch_page(s, url)
            page["depth"] = depth
            for link in page.pop("links"):
                if not link.startswith("http"):
                    continue
                link = norm_url(link)
                if site_key(link) != site or SKIP_EXT.search(urlparse(link).path):
                    continue
                if link != url:
                    inlinks.setdefault(link, set()).add(url)
                if link not in seen:
                    seen.add(link)
                    queue.append((link, depth + 1))
            db.insert_pages(con, crawl_id, [page])
            st["n"] += 1
            if st["n"] % 20 == 0:
                con.commit()
            time.sleep(delay)
        con.executemany("UPDATE pages SET inlinks=? WHERE crawl_id=? AND url=?",
                        [(len(v), crawl_id, u) for u, v in inlinks.items()])
        con.executemany("INSERT INTO links(crawl_id, source, target) VALUES(?,?,?)",
                        [(crawl_id, src, u) for u, v in inlinks.items() for src in sorted(v)])
        con.execute("UPDATE crawls SET pages=? WHERE id=?", (st["n"], crawl_id))
        con.commit()
        if st["n"] == 0:
            st["error"] = "Aucune page explorée (robots.txt interdit peut-être l'accès)."
    except Exception as e:  # le thread ne doit jamais mourir en silence
        st["error"] = str(e)
    finally:
        con.close()
        st["running"] = False


def audit(pages):
    """Liste de problèmes : (gravité, libellé, [urls]). gravité = 'haute' | 'moyenne' | 'basse'."""
    issues = []

    def add(sev, label, urls):
        if urls:
            issues.append((sev, label, urls))

    def is_html(p):
        return p["status"] == 200 and "html" in (p["content_type"] or "html")

    html = [p for p in pages if is_html(p)]
    add("haute", "Pages en erreur (4xx / 5xx / injoignables)",
        [p["url"] for p in pages if p["status"] == 0 or p["status"] >= 400])
    add("moyenne", "Redirections (3xx) dans les liens internes",
        [p["url"] for p in pages if 300 <= (p["status"] or 0) < 400])
    add("haute", "Balise title absente", [p["url"] for p in html if not p["title"]])
    add("basse", "Title trop long (plus de 60 caractères)",
        [p["url"] for p in html if p["title"] and len(p["title"]) > 60])
    add("basse", "Title trop court (moins de 30 caractères)",
        [p["url"] for p in html if p["title"] and len(p["title"]) < 30])
    indexable = [p for p in html if p["indexable"] != 0]
    for field, label in (("title", "Title dupliqué"), ("meta_desc", "Meta description dupliquée")):
        counts = Counter(p[field] for p in indexable if p[field])
        add("moyenne", label, [p["url"] for p in indexable if p[field] and counts[p[field]] > 1])
    add("moyenne", "Meta description absente", [p["url"] for p in html if not p["meta_desc"]])
    add("basse", "Meta description trop longue (plus de 160 caractères)",
        [p["url"] for p in html if p["meta_desc"] and len(p["meta_desc"]) > 160])
    add("moyenne", "H1 absent", [p["url"] for p in html if not p["h1"]])
    add("basse", "Plusieurs H1", [p["url"] for p in html if (p["h1_count"] or 0) > 1])
    add("moyenne", "Contenu mince (moins de 200 mots)",
        [p["url"] for p in html if p["word_count"] is not None and p["word_count"] < 200])
    add("basse", "Pages non indexables (noindex ou canonical vers une autre URL)",
        [p["url"] for p in html if p["indexable"] == 0])
    add("basse", "Pages profondes (plus de 3 clics depuis l'accueil)",
        [p["url"] for p in html if (p["depth"] or 0) > 3])
    add("basse", "Pages lentes (plus d'une seconde)",
        [p["url"] for p in html if (p["response_ms"] or 0) > 1000])
    add("basse", "Pages avec un seul lien interne entrant",
        [p["url"] for p in html if p["inlinks"] == 1 and (p["depth"] or 0) > 1])
    order = {"haute": 0, "moyenne": 1, "basse": 2}
    return sorted(issues, key=lambda i: (order[i[0]], -len(i[2])))

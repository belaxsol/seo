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
    def by_status(pred):
        return [p["url"] for p in pages if pred(p["status"] or 0)]
    add("haute", "Pages introuvables (404)", by_status(lambda s: s == 404))
    add("haute", "Accès refusé (403)", by_status(lambda s: s == 403))
    add("haute", "Autres erreurs client (4xx)", by_status(lambda s: 400 <= s < 500 and s not in (403, 404)))
    add("haute", "Erreurs serveur (5xx)", by_status(lambda s: s >= 500))
    add("haute", "Pages injoignables (délai dépassé, DNS...)", by_status(lambda s: s == 0))
    add("moyenne", "Redirections permanentes (301 / 308)", by_status(lambda s: s in (301, 308)))
    add("moyenne", "Redirections temporaires (302 / 303 / 307)", by_status(lambda s: s in (302, 303, 307)))
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


# Libellés d'incidents qui concernent un LIEN : on corrige dans les pages qui pointent vers l'URL.
LINK_ISSUES = {
    "Pages introuvables (404)", "Accès refusé (403)", "Autres erreurs client (4xx)",
    "Erreurs serveur (5xx)", "Pages injoignables (délai dépassé, DNS...)",
    "Redirections permanentes (301 / 308)", "Redirections temporaires (302 / 303 / 307)",
}

# Que faire, par type de problème.
FIX = {
    "Pages introuvables (404)": "La page n'existe pas (ou plus). Deux choix : recréer la page, ou, si elle a été "
        "supprimée, corriger le lien dans chaque page listée à droite pour pointer vers la bonne URL. "
        "Si l'ancienne URL avait des visiteurs ou des liens externes, ajoutez aussi une redirection 301.",
    "Accès refusé (403)": "Le serveur refuse l'accès au robot : protection anti-bot ou pare-feu, page réservée "
        "aux connectés, ou règle d'accès mal configurée. Testez l'URL dans un navigateur ; si elle s'ouvre, "
        "autorisez le crawler (ou vérifiez les règles du pare-feu / CDN), sinon corrigez les liens qui y mènent.",
    "Autres erreurs client (4xx)": "Le serveur rejette la requête (401 identification requise, 410 page supprimée "
        "volontairement, 429 trop de requêtes…). Corrigez les liens qui y mènent ou retirez la page.",
    "Erreurs serveur (5xx)": "Le serveur plante sur cette URL : consultez les journaux d'erreurs (PHP, Drupal "
        "watchdog…) pour trouver la cause. À corriger côté site, pas dans les liens.",
    "Pages injoignables (délai dépassé, DNS...)": "Le robot n'a pas obtenu de réponse : serveur trop lent, "
        "domaine mal orthographié ou lien externe mort. Vérifiez l'adresse dans les pages listées.",
    "Redirections permanentes (301 / 308)": "La page a changé d'adresse. Ça fonctionne, mais chaque redirection "
        "coûte du temps et dilue un peu les liens : modifiez le lien dans les pages listées pour pointer "
        "directement vers la destination finale.",
    "Redirections temporaires (302 / 303 / 307)": "Redirection « provisoire ». Si le changement est définitif, "
        "passez-la en 301 ; sinon, corrigez les liens pour viser directement la destination.",
    "Balise title absente": "Ajoutez un title unique de 30 à 60 caractères avec le mot-clé principal de la page.",
    "Title trop long (plus de 60 caractères)": "Google coupe les titles longs : raccourcissez en gardant le mot-clé au début.",
    "Title trop court (moins de 30 caractères)": "Développez le title avec le mot-clé principal et un bénéfice ou un lieu.",
    "Title dupliqué": "Chaque page doit avoir son propre title : différenciez-les selon le sujet de chaque page.",
    "Meta description dupliquée": "Rédigez une description propre à chaque page.",
    "Meta description absente": "Ajoutez une description de 120 à 160 caractères : elle s'affiche sous le titre dans Google.",
    "Meta description trop longue (plus de 160 caractères)": "Raccourcissez-la : Google la coupe au-delà de 160 caractères.",
    "H1 absent": "Ajoutez un titre principal (H1) qui décrit le sujet de la page.",
    "Plusieurs H1": "Gardez un seul H1 par page ; passez les autres en H2.",
    "Contenu mince (moins de 200 mots)": "Étoffez la page (cas concrets, questions fréquentes, détails) ou fusionnez-la avec une page proche.",
    "Pages non indexables (noindex ou canonical vers une autre URL)": "Vérifiez que c'est voulu : si la page doit apparaître dans Google, retirez le noindex ou corrigez le canonical.",
    "Pages profondes (plus de 3 clics depuis l'accueil)": "Rapprochez-les de l'accueil : lien depuis le menu, le pied de page ou une page très visitée.",
    "Pages lentes (plus d'une seconde)": "Optimisez : images, cache, hébergement. Un serveur lent freine aussi le crawl de Google.",
    "Pages avec un seul lien interne entrant": "Ajoutez des liens depuis d'autres pages pour qu'elles soient mieux explorées et poussées.",
}

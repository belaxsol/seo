"""Exploration d'un concurrent : mots-clés candidats tirés de ses pages, puis relevé de leur position."""
import datetime as dt
import random
import re
import time
from collections import deque
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests

import crawler
import db
import providers
import tracker
from providers._http import ProviderError

MAX_PAGES = 25          # pages du concurrent lues
MAX_CANDIDATES = 50     # mots-clés relevés par exploration (1 requête chacun)
STATUS = {}             # project_id -> {"running", "phase", "done", "total", "error", "competitor"}

SEPARATORS = re.compile(r"\s[|\-–—:·»]\s")
GENERIC_START = {"nos", "notre", "mentions", "politique", "plan", "contact", "accueil", "à", "qui",
                 "conditions", "cookies", "cgv", "cgu", "faq", "blog", "devis", "actualités"}


def candidates(pages, competitor, known):
    """Titres et H1 nettoyés : on retire la marque et les pages génériques (contact, mentions...)."""
    brand = competitor.split(".")[0].lower()
    seen, out = set(known), []
    for p in pages:
        for text in (p.get("h1"), p.get("title")):
            if not text:
                continue
            part = SEPARATORS.split(text)[0].strip().lower()
            part = re.sub(r"\s+", " ", part)
            words = part.split()
            if not 2 <= len(words) <= 8 or brand in part or words[0] in GENERIC_START:
                continue
            if sum(c.isdigit() for c in part) > 3 or part in seen:
                continue
            seen.add(part)
            out.append(part)
    return out[:MAX_CANDIDATES]


def read_site(competitor):
    """Lit les premières pages du concurrent (respecte robots.txt)."""
    start = f"https://{competitor}/"
    s = requests.Session()
    s.headers["User-Agent"] = crawler.UA
    rp = None
    try:  # robots.txt lu avec notre User-Agent (robotparser.read() utilise celui de Python, souvent refusé)
        r = s.get(urljoin(start, "/robots.txt"), timeout=10)
        if r.status_code == 200:
            rp = robotparser.RobotFileParser()
            rp.parse(r.text.splitlines())
    except requests.RequestException:
        rp = None
    queue, seen, pages = deque([start]), {start}, []
    while queue and len(pages) < MAX_PAGES:
        url = queue.popleft()
        if rp and not rp.can_fetch(crawler.UA, url):
            continue
        page = crawler.fetch_page(s, url)
        pages.append(page)
        for link in page["links"]:
            link = crawler.norm_url(link)
            if (link.startswith("http") and crawler.site_key(link) == competitor
                    and not crawler.SKIP_EXT.search(urlparse(link).path) and link not in seen):
                seen.add(link)
                queue.append(link)
        time.sleep(0.2)
    return pages


def run(project, competitor, engine, queue=None):
    """Prévu pour tourner dans un thread."""
    pid = project["id"]
    st = STATUS[pid] = {"running": True, "phase": "Lecture du site concurrent", "done": 0,
                        "total": 0, "error": None, "competitor": competitor, "queue": queue}
    try:
        known = {r["keyword"] for r in db.q("SELECT keyword FROM keywords WHERE project_id=?", (pid,))}
        cands = candidates(read_site(competitor), competitor, known)
        if not cands:
            st["error"] = "Aucun mot-clé candidat trouvé sur ce site (pages illisibles ou trop génériques)."
            return
        st.update(phase="Relevé des positions", total=len(cands))
        mod = providers.get("serp").get(engine)
        today = dt.date.today().isoformat()
        db.x("DELETE FROM gap_probe WHERE project_id=? AND competitor=?", (pid, competitor))
        fails = 0
        for n, kw in enumerate(cands):
            try:
                urls = mod.search(kw, project["lang"], project["country"], tracker.MAX_RESULTS)
                fails = 0
            except (ProviderError, requests.RequestException) as e:
                fails += 1
                if fails >= 3:
                    st["error"] = f"Arrêt après 3 échecs consécutifs (blocage probable) : {e}"
                    return
                continue
            db.x("DELETE FROM probe_serp WHERE project_id=? AND keyword=?", (pid, kw))
            db.xmany("INSERT OR REPLACE INTO probe_serp(project_id, keyword, date, position, url, domain) "
                     "VALUES(?,?,?,?,?,?)",
                     [(pid, kw, today, i, u, tracker.host_of(u)) for i, u in enumerate(urls, 1)])
            my_pos, my_url = tracker.find_position(urls, project["domain"])
            c_pos, c_url = tracker.find_position(urls, competitor)
            db.x("INSERT OR REPLACE INTO gap_probe(project_id, competitor, keyword, date, my_pos, "
                 "my_url, comp_pos, comp_url) VALUES(?,?,?,?,?,?,?,?)",
                 (pid, competitor, kw, today, my_pos, my_url, c_pos, c_url))
            st["done"] = n + 1
            if n < len(cands) - 1:
                time.sleep(random.uniform(4, 9))
    except Exception as e:  # le thread ne doit jamais mourir en silence
        st["error"] = str(e)
    finally:
        st["running"] = False


def run_many(project, competitors, engine):
    """Explore plusieurs concurrents à la suite ; s'arrête au premier blocage."""
    for i, comp in enumerate(competitors, 1):
        run(project, comp, engine, queue=(i, len(competitors)))
        if STATUS[project["id"]]["error"]:
            break

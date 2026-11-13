"""SEO Libre : outil SEO local, sans abonnement ni clé d'API.

    python app.py          lance l'interface sur http://127.0.0.1:5000
    python app.py track    relève les positions une fois puis quitte (pour une tâche planifiée)
"""
import csv
import datetime as dt
import io
import os
import string
import sys
import threading
import time
from collections import defaultdict
from urllib.parse import urlparse

from flask import (Flask, Response, abort, flash, redirect, render_template, request,
                   url_for)

import crawler
import db
import importers
import providers
import tracker
from providers._http import ProviderError

app = Flask(__name__)
app.secret_key = os.urandom(16)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

QUESTIONS = {"fr": ["comment", "pourquoi", "quel", "quelle", "combien", "où", "meilleur"],
             "en": ["how", "why", "what", "which", "best", "where"]}
PERIODS = [("today 3-m", "3 mois"), ("today 12-m", "12 mois"), ("today 5-y", "5 ans")]


def get_project(pid):
    p = db.one("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        abort(404)
    return p


def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        raise importers.ImportError_("Aucun fichier choisi.")
    return f.read()


def engines():
    return [(n, m.LABEL, providers.is_available(m)) for n, m in sorted(providers.get("serp").items())]


def default_engine():
    g = providers.get("serp").get("google")
    return "google" if g and providers.is_available(g) else "bing"


def start_checks(pid=None, only_missing=False):
    if tracker.STATUS["running"]:
        return False
    threading.Thread(target=tracker.run_checks, args=(pid, only_missing), daemon=True).start()
    time.sleep(0.3)
    return True


# Part de clics estimée par position (courbe CTR moyenne du marché).
CTR = {1: 30, 2: 15, 3: 10, 4: 7, 5: 5, 6: 4, 7: 3, 8: 2.5, 9: 2, 10: 1.8}


def ctr(position):
    if position is None:
        return 0.0
    p = int(position)
    return CTR.get(p, 1.0 if p <= 20 else 0.3 if p <= 30 else 0.0)


def visibility(positions):
    """Indice de visibilité 0-100 : plus c'est haut, mieux c'est (100 = tout en position 1)."""
    if not positions:
        return None
    return round(sum(ctr(p) for p in positions) / len(positions) / CTR[1] * 100, 1)


def _delta(ref, last):
    """Écart de position entre deux relevés (positif = progression)."""
    if not ref or not last:
        return None
    a, b = ref["position"], last["position"]
    if a is None and b is None:
        return None
    if a is None:  # pas classé avant : on compare à la position de départ (100)
        return tracker.BASELINE_POSITION - b
    if b is None:
        return "out"
    return a - b


def _ref(h, days):
    """Dernier relevé datant d'au moins `days` jours avant le plus récent."""
    limit = (dt.date.fromisoformat(h[-1]["date"]) - dt.timedelta(days=days)).isoformat()
    older = [r for r in h[:-1] if r["date"] <= limit]
    return older[-1] if older else None


# ---------------- Projets ----------------

@app.route("/")
def index():
    projects = db.q(
        "SELECT p.*, (SELECT COUNT(*) FROM keywords k WHERE k.project_id=p.id) AS nkw "
        "FROM projects p ORDER BY p.name")
    return render_template("index.html", projects=projects, project=None)


@app.post("/project/new")
def project_new():
    domain = tracker.clean_domain(request.form.get("domain", ""))
    if not domain:
        flash("Indiquez un domaine, par exemple monsite.fr")
        return redirect(url_for("index"))
    pid = db.x("INSERT INTO projects(name, domain, lang, country) VALUES(?,?,?,?)",
               (request.form.get("name", "").strip() or domain, domain,
                (request.form.get("lang") or "fr").strip().lower()[:2],
                (request.form.get("country") or "FR").strip().upper()[:2]))
    return redirect(url_for("positions", pid=pid))


@app.post("/p/<int:pid>/delete")
def project_delete(pid):
    db.x("DELETE FROM projects WHERE id=?", (pid,))
    return redirect(url_for("index"))


# ---------------- Suivi de positions ----------------

@app.route("/p/<int:pid>/positions")
def positions(pid):
    project = get_project(pid)
    days = request.args.get("days", 90, type=int)
    sel = request.args.get("kw", type=int)
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    kws = db.q("SELECT * FROM keywords WHERE project_id=? ORDER BY keyword, engine", (pid,))
    hist = defaultdict(list)
    for r in db.q("SELECT r.* FROM ranks r JOIN keywords k ON k.id=r.keyword_id "
                  "WHERE k.project_id=? ORDER BY r.date", (pid,)):
        hist[r["keyword_id"]].append(r)
    rows, series, sel_kw = [], [], None
    for k in kws:
        h = hist[k["id"]]
        last = h[-1] if h else None
        found = [r["position"] for r in h if r["position"] is not None]
        rows.append({"k": k, "last": last, "best": min(found) if found else None,
                     "d1": _delta(h[-2], last) if len(h) > 1 else None,
                     "d7": _delta(_ref(h, 7), last) if h else None,
                     "d30": _delta(_ref(h, 30), last) if h else None})
        if sel == k["id"]:
            sel_kw = k
        if found and (sel == k["id"] or (sel is None and len(series) < 8)):
            series.append({"name": f"{k['keyword']} ({k['engine']})",
                           "points": [[r["date"], r["position"] if r["position"] is not None
                                       else tracker.BASELINE_POSITION]  # non trouvé = bas du graphique
                                  for r in h if r["date"] >= since]})
    # note moyenne par jour : tous les mots-clés, un mot non classé compte pour 100
    by_day = defaultdict(list)
    for h in hist.values():
        for r in h:
            if r["date"] >= since:
                by_day[r["date"]].append(r["position"] if r["position"] is not None
                                         else tracker.BASELINE_POSITION)
    avg_points = [[d, round(sum(v) / len(v), 1)] for d, v in sorted(by_day.items())]
    avg_series = [{"name": "Position moyenne (non classé = 100)", "points": avg_points}]
    avg_now = avg_points[-1][1] if avg_points else None
    avg_prev = avg_points[-2][1] if len(avg_points) > 1 else None
    pos = [r["last"]["position"] for r in rows if r["last"] and r["last"]["position"] is not None]
    moved = [r["d1"] for r in rows]
    checked = [r["last"]["position"] for r in rows if r["last"]]
    kpi = {"n": len(rows), "visibility": visibility(checked), "avg": round(sum(pos) / len(pos), 1) if pos else None,
           "top3": sum(1 for p in pos if p <= 3), "top10": sum(1 for p in pos if p <= 10),
           "out": sum(1 for r in rows if r["last"] and r["last"]["position"] is None),
           "up": sum(1 for d in moved if d == "in" or (isinstance(d, (int, float)) and d > 0)),
           "down": sum(1 for d in moved if d == "out" or (isinstance(d, (int, float)) and d < 0))}
    serp = []
    if sel_kw:
        serp = db.q("SELECT * FROM serp WHERE keyword_id=? AND date=(SELECT MAX(date) FROM serp "
                    "WHERE keyword_id=?) ORDER BY position", (sel, sel))
    return render_template("positions.html", project=project, tab="positions", rows=rows,
                           series=series, avg_series=avg_series, avg_now=avg_now, avg_prev=avg_prev, days=days, sel=sel, sel_kw=sel_kw, serp=serp, kpi=kpi,
                           engines=engines(), default_engine=default_engine(),
                           status=tracker.STATUS, top=tracker.MAX_RESULTS)


@app.route("/p/<int:pid>/competitors")
def competitors(pid):
    project = get_project(pid)
    best = {}  # (domaine, mot-clé) -> meilleure position au dernier relevé
    for r in db.q("SELECT s.keyword_id, s.domain, MIN(s.position) AS pos FROM serp s "
                  "JOIN keywords k ON k.id = s.keyword_id WHERE k.project_id=? AND s.date = "
                  "(SELECT MAX(date) FROM serp WHERE keyword_id = s.keyword_id) "
                  "GROUP BY s.keyword_id, s.domain", (pid,)):
        best[(r["domain"], r["keyword_id"])] = r["pos"]
    nkw = len({k for _, k in best})
    by = defaultdict(list)
    for (domain, _), p in best.items():
        by[domain].append(p)
    rows = [{"domain": d, "nkw": len(ps), "share": round(100 * len(ps) / nkw),
             "avg": round(sum(ps) / len(ps), 1), "top3": sum(1 for p in ps if p <= 3),
             "top10": sum(1 for p in ps if p <= 10),
             "me": d == project["domain"] or d.endswith("." + project["domain"])}
            for d, ps in by.items()]
    rows.sort(key=lambda r: (-r["nkw"], r["avg"]))
    return render_template("competitors.html", project=project, tab="competitors",
                           rows=rows[:60], nkw=nkw, top=tracker.MAX_RESULTS)


CANNIB_DAYS = 30        # fenêtre d'analyse
CANNIB_SWITCHES = 2     # changements de page classée à partir desquels on parle d'alternance


def norm_url(url):
    """Identifie une page : sans schéma, www, paramètres, ancre ni slash final."""
    p = urlparse(url)
    host = p.netloc.lower().split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    return host + (p.path.rstrip("/") or "/")


def is_mine(domain, project):
    return domain == project["domain"] or domain.endswith("." + project["domain"])


@app.route("/p/<int:pid>/cannibalisation")
def cannibalisation(pid):
    """Mots-clés sur lesquels plusieurs pages du site se disputent la même place."""
    project = get_project(pid)
    since = (dt.date.today() - dt.timedelta(days=CANNIB_DAYS)).isoformat()
    kws = db.q("SELECT id, keyword, engine FROM keywords WHERE project_id=? ORDER BY keyword", (pid,))
    # (mot-clé, jour) -> [(position, page)] pour les pages du site uniquement
    mine = defaultdict(lambda: defaultdict(list))
    last_day = {}
    for r in db.q("SELECT s.keyword_id, s.date, s.position, s.url, s.domain FROM serp s "
                  "JOIN keywords k ON k.id = s.keyword_id WHERE k.project_id=? AND s.date>=? "
                  "ORDER BY s.date, s.position", (pid, since)):
        last_day[r["keyword_id"]] = r["date"]
        if is_mine(r["domain"], project):
            mine[r["keyword_id"]][r["date"]].append((r["position"], norm_url(r["url"]), r["url"]))
    rows, tracked = [], 0
    for k in kws:
        if k["id"] not in last_day:
            continue
        tracked += 1
        days = mine[k["id"]]
        if not days:
            continue
        today = days.get(last_day[k["id"]], [])
        simultaneous = len({n for _, n, _ in today}) >= 2
        # page la mieux classée chaque jour, puis nombre de changements d'un jour à l'autre
        leaders = [min(days[d])[1] for d in sorted(days)]
        switches = sum(1 for a, b in zip(leaders, leaders[1:]) if a != b)
        alternating = switches >= CANNIB_SWITCHES
        if not (simultaneous or alternating):
            continue
        pages = {}
        for d in sorted(days):
            best_of_day = {}  # une page = une ligne par jour, à sa meilleure position
            for pos, n, raw in days[d]:
                if n not in best_of_day or pos < best_of_day[n][0]:
                    best_of_day[n] = (pos, raw)
            for n, (pos, raw) in best_of_day.items():
                p = pages.setdefault(n, {"url": raw, "best": pos, "last": None, "days": 0})
                p["best"] = min(p["best"], pos)
                p["days"] += 1
                p["url"] = raw
                if d == last_day[k["id"]]:
                    p["last"] = pos
        rows.append({"k": k, "simultaneous": simultaneous, "alternating": alternating,
                     "switches": switches, "ndays": len(days),
                     "pages": sorted(pages.values(), key=lambda p: (p["last"] is None, p["best"]))})
    rows.sort(key=lambda r: (not r["simultaneous"], -r["switches"], r["k"]["keyword"]))
    stats = {"tracked": tracked, "conflicts": len(rows),
             "simultaneous": sum(1 for r in rows if r["simultaneous"]),
             "alternating": sum(1 for r in rows if r["alternating"])}
    return render_template("cannibalisation.html", project=project, tab="cannibalisation",
                           rows=rows, stats=stats, days=CANNIB_DAYS, switches=CANNIB_SWITCHES,
                           top=tracker.MAX_RESULTS)


@app.post("/p/<int:pid>/keywords/add")
def keywords_add(pid):
    get_project(pid)
    engine = request.form.get("engine") or default_engine()
    words = request.form.getlist("kw") or request.form.get("keywords", "").splitlines()
    words = {w.strip().lower() for w in words if w.strip()}
    known = {r["keyword"] for r in db.q("SELECT keyword FROM keywords WHERE project_id=? AND engine=?",
                                        (pid, engine))}
    fresh, dupes = sorted(words - known), sorted(words & known)
    db.xmany("INSERT OR IGNORE INTO keywords(project_id, keyword, engine) VALUES(?,?,?)",
             [(pid, w, engine) for w in fresh])
    if fresh:
        started = start_checks(pid, only_missing=True)
        flash(f"{len(fresh)} mot(s)-clé(s) ajouté(s) au suivi ({engine})."
              + (" Premier relevé lancé." if started else " Ils seront relevés après le passage en cours."))
    else:
        flash("Aucun mot-clé ajouté.")
    if dupes:
        flash(f"Déjà suivi{'s' if len(dupes) > 1 else ''} ({engine}) : " + ", ".join(dupes[:8])
              + ("…" if len(dupes) > 8 else ""))
    return redirect(url_for("positions", pid=pid))


@app.post("/p/<int:pid>/keywords/<int:kid>/delete")
def keyword_delete(pid, kid):
    db.x("DELETE FROM keywords WHERE id=? AND project_id=?", (kid, pid))
    return redirect(url_for("positions", pid=pid))


@app.post("/p/<int:pid>/check")
def check(pid):
    get_project(pid)
    todo = db.q("SELECT 1 FROM keywords k WHERE k.project_id = ? AND NOT EXISTS "
                "(SELECT 1 FROM ranks r WHERE r.keyword_id = k.id AND r.date = ?)",
                (pid, dt.date.today().isoformat()))
    if not todo:
        flash("Tous les mots-clés ont déjà été relevés aujourd'hui.")
    elif start_checks(pid, only_missing=True):
        flash("Relevé lancé pour les mots-clés pas encore relevés aujourd'hui.")
    else:
        flash("Un relevé est déjà en cours.")
    return redirect(url_for("positions", pid=pid))


@app.route("/p/<int:pid>/export/positions.csv")
def export_positions(pid):
    get_project(pid)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["date", "mot_cle", "moteur", "position", "url"])
    for r in db.q("SELECT r.date, k.keyword, k.engine, r.position, r.url FROM ranks r "
                  "JOIN keywords k ON k.id=r.keyword_id WHERE k.project_id=? "
                  "ORDER BY r.date, k.keyword", (pid,)):
        w.writerow(list(r))
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=positions.csv"})


# ---------------- Recherche de mots-clés ----------------

@app.route("/p/<int:pid>/ideas")
def ideas(pid):
    project = get_project(pid)
    f = request.args.get("q", "").strip().lower()
    sql, args = ("SELECT keyword, GROUP_CONCAT(source, ', ') AS sources, COUNT(*) AS n "
                 "FROM kw_ideas WHERE project_id=?"), [pid]
    if f:
        sql += " AND keyword LIKE ?"
        args.append(f"%{f}%")
    rows = db.q(sql + " GROUP BY keyword ORDER BY n DESC, keyword LIMIT 2000", args)
    total = db.one("SELECT COUNT(DISTINCT keyword) c FROM kw_ideas WHERE project_id=?", (pid,))["c"]
    sources = [(n, m.LABEL) for n, m in sorted(providers.get("suggest").items())]
    return render_template("ideas.html", project=project, tab="ideas", rows=rows, f=f,
                           total=total, sources=sources, engines=engines(),
                           default_engine=default_engine())


@app.post("/p/<int:pid>/ideas/fetch")
def ideas_fetch(pid):
    project = get_project(pid)
    seed = request.form.get("seed", "").strip().lower()
    if not seed:
        flash("Indiquez un mot-clé de départ.")
        return redirect(url_for("ideas", pid=pid))
    queries = [seed]
    if request.form.get("deep"):
        queries += [f"{seed} {c}" for c in string.ascii_lowercase]
        queries += [f"{w} {seed}" for w in QUESTIONS.get(project["lang"], QUESTIONS["en"])]
    mods = providers.get("suggest")
    found = 0
    for name in request.form.getlist("source") or list(mods):
        mod = mods.get(name)
        if not mod:
            continue
        try:
            for qy in queries:
                words = mod.suggest(qy, project["lang"], project["country"])
                db.xmany("INSERT OR IGNORE INTO kw_ideas(project_id, keyword, source) "
                         "VALUES(?,?,?)", [(pid, w.lower(), name) for w in words if w.strip()])
                found += len(words)
                if len(queries) > 1:
                    time.sleep(0.3)
        except (ProviderError, ValueError, OSError) as e:
            flash(f"{mod.LABEL} : {e}")
    flash(f"{found} suggestion(s) récupérée(s) pour « {seed} ».")
    return redirect(url_for("ideas", pid=pid, q=seed))


@app.post("/p/<int:pid>/ideas/clear")
def ideas_clear(pid):
    db.x("DELETE FROM kw_ideas WHERE project_id=?", (pid,))
    return redirect(url_for("ideas", pid=pid))


@app.route("/p/<int:pid>/export/keywords.csv")
def export_keywords(pid):
    get_project(pid)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["mot_cle", "sources"])
    for r in db.q("SELECT keyword, GROUP_CONCAT(source, ', ') FROM kw_ideas WHERE project_id=? "
                  "GROUP BY keyword ORDER BY COUNT(*) DESC, keyword", (pid,)):
        w.writerow(list(r))
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=mots-cles.csv"})


# ---------------- Tendances ----------------

@app.route("/p/<int:pid>/trends")
def trends(pid):
    project = get_project(pid)
    data = defaultdict(list)
    for r in db.q("SELECT keyword, date, value FROM trends WHERE project_id=? ORDER BY date", (pid,)):
        data[r["keyword"]].append([r["date"], r["value"]])
    names = sorted(data)
    chosen = [n for n in request.args.getlist("k") if n in data] or names[:6]
    series = [{"name": n, "points": data[n]} for n in chosen]
    return render_template("trends.html", project=project, tab="trends", names=names,
                           chosen=chosen, series=series, periods=PERIODS)


@app.post("/p/<int:pid>/trends/fetch")
def trends_fetch(pid):
    project = get_project(pid)
    words = [w.strip().lower() for w in request.form.get("keywords", "").split(",") if w.strip()]
    period = request.form.get("period", "today 12-m")
    ok = 0
    for i, w in enumerate(words[:5]):
        try:
            tracker.fetch_trend(project, w, period)
        except (ProviderError, OSError) as e:
            flash(f"« {w} » : {e}")
            break
        ok += 1
        if i < len(words) - 1:
            time.sleep(2)
    if ok:
        flash(f"Tendance récupérée pour {ok} mot(s)-clé(s). Elle sera rafraîchie toute seule chaque semaine.")
    return redirect(url_for("trends", pid=pid))


@app.post("/p/<int:pid>/trends/delete")
def trends_delete(pid):
    for table in ("trends", "trend_meta"):
        db.x(f"DELETE FROM {table} WHERE project_id=? AND keyword=?", (pid, request.form.get("keyword")))
    return redirect(url_for("trends", pid=pid))


# ---------------- Audit ----------------

@app.route("/p/<int:pid>/audit")
def audit(pid):
    project = get_project(pid)
    crawls = db.q("SELECT * FROM crawls WHERE project_id=? ORDER BY id DESC", (pid,))
    status = crawler.STATUS.get(pid, {"running": False, "n": 0, "error": None})
    current = None
    cid = request.args.get("crawl", type=int)
    for c in crawls:
        if (cid and c["id"] == cid) or (not cid and (c["pages"] or not status["running"])):
            current = c
            break
    pages, shown, issues, stats, problems, sources = [], [], [], {}, [], defaultdict(list)
    code = request.args.get("code", type=int)
    if current:
        pages = db.q("SELECT * FROM pages WHERE crawl_id=? ORDER BY depth IS NULL, depth, url",
                     (current["id"],))
        issues = crawler.audit(pages)
        n = defaultdict(int)
        for p in pages:
            n[p["status"] or 0] += 1
        stats = {"total": len(pages), "c200": n[200], "c301": n[301], "c302": n[302],
                 "c404": n[404],
                 "other": len(pages) - n[200] - n[301] - n[302] - n[404]}
        problems = sorted((p for p in pages if p["status"] != 200),
                          key=lambda p: (p["status"] or 999, p["url"]))
        for r in db.q("SELECT l.target, l.source FROM links l JOIN pages p ON p.crawl_id = "
                      "l.crawl_id AND p.url = l.target WHERE l.crawl_id=? AND p.status != 200",
                      (current["id"],)):
            sources[r["target"]].append(r["source"])
        shown = [p for p in pages if code is None or (p["status"] or 0) == code]
    return render_template("audit.html", project=project, tab="audit", crawls=crawls,
                           current=current, pages=shown[:1000], npages=len(shown), code=code,
                           issues=issues, stats=stats, problems=problems[:500],
                           nproblems=len(problems), sources=sources, status=status)


@app.post("/p/<int:pid>/crawl")
def crawl_start(pid):
    project = get_project(pid)
    if crawler.STATUS.get(pid, {}).get("running"):
        flash("Un crawl est déjà en cours.")
        return redirect(url_for("audit", pid=pid))
    url = request.form.get("url", "").strip() or f"https://{project['domain']}/"
    if not url.startswith("http"):
        url = "https://" + url
    max_pages = min(max(request.form.get("max_pages", 200, type=int), 1), 20000)
    threading.Thread(target=crawler.crawl, args=(pid, url, max_pages,
                                                 bool(request.form.get("robots"))),
                     daemon=True).start()
    time.sleep(0.3)
    return redirect(url_for("audit", pid=pid))


@app.post("/p/<int:pid>/import/screamingfrog")
def import_sf(pid):
    get_project(pid)
    try:
        pages = importers.import_screaming_frog(upload())
    except importers.ImportError_ as e:
        flash(str(e))
        return redirect(url_for("audit", pid=pid))
    con = db.connect()
    try:
        cid = con.execute("INSERT INTO crawls(project_id, source, started, pages) VALUES(?,?,?,?)",
                          (pid, "screaming-frog",
                           dt.datetime.now().isoformat(timespec="seconds"), len(pages))).lastrowid
        db.insert_pages(con, cid, pages)
        con.commit()
    finally:
        con.close()
    flash(f"{len(pages)} URL importées depuis Screaming Frog.")
    return redirect(url_for("audit", pid=pid, crawl=cid))


@app.post("/p/<int:pid>/crawl/<int:cid>/delete")
def crawl_delete(pid, cid):
    db.x("DELETE FROM crawls WHERE id=? AND project_id=?", (cid, pid))
    return redirect(url_for("audit", pid=pid))


if __name__ == "__main__":
    db.init()
    if len(sys.argv) > 1 and sys.argv[1] == "track":
        tracker.run_checks(only_missing=True)
        tracker.refresh_trends()
        print("Relevé et tendances à jour.")
    else:
        if os.environ.get("SEOLIBRE_AUTO_TRACK", "1") == "1":
            threading.Thread(target=tracker.scheduler, daemon=True).start()
        port = int(os.environ.get("SEOLIBRE_PORT", "5000"))
        print(f"SEO Libre : ouvrez http://127.0.0.1:{port} dans votre navigateur")
        app.run(host="127.0.0.1", port=port, debug=False, threaded=True)

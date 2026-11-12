"""Suivi de positions : interroge les moteurs et enregistre une position par jour."""
import datetime as dt
import os
import random
import threading
import time
from urllib.parse import urlparse

import requests

import db
import providers
from providers._http import ProviderError

MAX_RESULTS = int(os.environ.get("SEOLIBRE_DEPTH", "30"))  # profondeur du relevé
STATUS = {"running": False, "done": 0, "total": 0}
_lock = threading.Lock()


def clean_domain(d):
    d = d.strip().lower()
    if "//" in d:
        d = urlparse(d).netloc
    d = d.split("/")[0]
    return d[4:] if d.startswith("www.") else d


def host_of(url):
    h = urlparse(url).netloc.lower().split(":")[0]
    return h[4:] if h.startswith("www.") else h


def save_serp(keyword_id, date, urls):
    """Garde la page de résultats du jour (pour la vue concurrents)."""
    con = db.connect()
    try:
        con.execute("DELETE FROM serp WHERE keyword_id=? AND date=?", (keyword_id, date))
        con.executemany("INSERT INTO serp(keyword_id, date, position, url, domain) VALUES(?,?,?,?,?)",
                        [(keyword_id, date, i, u, host_of(u)) for i, u in enumerate(urls, 1)])
        con.commit()
    finally:
        con.close()


def find_position(urls, domain):
    domain = clean_domain(domain)
    for i, u in enumerate(urls, 1):
        host = urlparse(u).netloc.lower().split(":")[0]
        if host == domain or host.endswith("." + domain):
            return i, u
    return None, None


def run_checks(project_id=None, only_missing=False):
    """Vérifie les mots-clés (tous, ou ceux sans relevé aujourd'hui)."""
    if not _lock.acquire(blocking=False):
        return
    try:
        today = dt.date.today().isoformat()
        sql = ("SELECT k.*, p.domain, p.lang, p.country FROM keywords k "
               "JOIN projects p ON p.id = k.project_id WHERE 1=1")
        args = []
        if project_id:
            sql += " AND k.project_id = ?"
            args.append(project_id)
        if only_missing:
            sql += " AND NOT EXISTS (SELECT 1 FROM ranks r WHERE r.keyword_id = k.id AND r.date = ?)"
            args.append(today)
        rows = db.q(sql, args)
        STATUS.update(running=True, done=0, total=len(rows))
        engines = providers.get("serp")
        errors = {}
        for n, k in enumerate(rows):
            now = dt.datetime.now().isoformat(timespec="seconds")
            mod = engines.get(k["engine"])
            if errors.get(k["engine"], 0) >= 3:
                err = "Moteur ignoré pour ce passage : 3 échecs consécutifs (blocage probable)."
            elif not mod:
                err = f"Moteur inconnu : {k['engine']}"
            else:
                try:
                    urls = mod.search(k["keyword"], k["lang"], k["country"], MAX_RESULTS)
                    pos, url = find_position(urls, k["domain"])
                    db.x("INSERT OR REPLACE INTO ranks(keyword_id, date, position, url) "
                         "VALUES(?,?,?,?)", (k["id"], today, pos, url))
                    save_serp(k["id"], today, urls)
                    err, errors[k["engine"]] = None, 0
                except ProviderError as e:
                    err = str(e)
                    errors[k["engine"]] = errors.get(k["engine"], 0) + 1
                except requests.RequestException:
                    err = "Connexion au moteur impossible (réseau ou blocage)."
                    errors[k["engine"]] = errors.get(k["engine"], 0) + 1
                except Exception as e:
                    err = f"Erreur : {e}"
                    errors[k["engine"]] = errors.get(k["engine"], 0) + 1
                if n < len(rows) - 1:
                    time.sleep(random.uniform(4, 9))  # rester discret
            db.x("UPDATE keywords SET last_checked=?, last_error=? WHERE id=?", (now, err, k["id"]))
            STATUS["done"] = n + 1
    finally:
        STATUS["running"] = False
        _lock.release()


def fetch_trend(project, keyword, period):
    """Récupère une courbe Google Trends et la remplace en base."""
    mod = providers.get("trends")["google-trends"]
    pts = mod.fetch(keyword, project["lang"], project["country"], period)
    pid = project["id"]
    db.x("DELETE FROM trends WHERE project_id=? AND keyword=?", (pid, keyword))
    db.xmany("INSERT OR REPLACE INTO trends(project_id, keyword, date, value) VALUES(?,?,?,?)",
             [(pid, keyword, d, v) for d, v in pts])
    db.x("INSERT OR REPLACE INTO trend_meta(project_id, keyword, period, fetched) VALUES(?,?,?,?)",
         (pid, keyword, period, dt.date.today().isoformat()))


def refresh_trends(max_age_days=7):
    """Rafraîchit les courbes de tendance vieilles de plus d'une semaine."""
    limit = (dt.date.today() - dt.timedelta(days=max_age_days)).isoformat()
    rows = db.q("SELECT m.keyword, m.period, p.* FROM trend_meta m "
                "JOIN projects p ON p.id = m.project_id WHERE m.fetched <= ?", (limit,))
    for r in rows:
        try:
            fetch_trend(r, r["keyword"], r["period"])
        except Exception:
            return  # Google limite : on réessaiera au prochain passage
        time.sleep(random.uniform(4, 9))


def scheduler(interval=1800):
    """Tant que l'outil est ouvert : relevé quotidien des positions, tendances chaque semaine."""
    while True:
        try:
            run_checks(only_missing=True)
            refresh_trends()
        except Exception:
            pass
        time.sleep(interval)

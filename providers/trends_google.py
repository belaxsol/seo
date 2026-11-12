"""Courbe d'intérêt Google Trends via les points d'accès non officiels du site.

Google limite fortement ces requêtes (erreur 429). En cas de refus, l'outil
réessaie tout seul au passage suivant.
"""
import datetime as dt
import json

from ._http import ProviderError, session

KIND = "trends"
NAME = "google-trends"
LABEL = "Google Trends"
BASE = "https://trends.google.com/trends/api"


def _json(text):
    return json.loads(text[text.index("{"):])


def fetch(keyword, lang="fr", country="FR", period="today 12-m"):
    s = session(lang, country)
    hl = f"{lang}-{country}"
    s.get(f"https://trends.google.com/trends/?geo={country}", timeout=20)  # cookies
    req = {"comparisonItem": [{"keyword": keyword, "geo": country, "time": period}],
           "category": 0, "property": ""}
    r = s.get(f"{BASE}/explore", params={"hl": hl, "tz": "-60", "req": json.dumps(req)},
              timeout=20)
    if r.status_code == 429:
        raise ProviderError("Google Trends limite les requêtes (429). "
                            "Réessayez dans quelques minutes.")
    if r.status_code != 200:
        raise ProviderError(f"Google Trends a répondu {r.status_code}")
    try:
        widget = next(w for w in _json(r.text)["widgets"] if w.get("id") == "TIMESERIES")
        r = s.get(f"{BASE}/widgetdata/multiline",
                  params={"hl": hl, "tz": "-60", "req": json.dumps(widget["request"]),
                          "token": widget["token"]}, timeout=20)
        if r.status_code != 200:
            raise ProviderError(f"Google Trends a répondu {r.status_code}")
        data = _json(r.text)["default"]["timelineData"]
        return [(dt.datetime.fromtimestamp(int(d["time"]), dt.timezone.utc).strftime("%Y-%m-%d"),
                 d["value"][0]) for d in data]
    except (KeyError, ValueError, StopIteration) as e:
        raise ProviderError(f"Réponse Google Trends inattendue ({e}).")

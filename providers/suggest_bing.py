"""Suggestions de la barre de recherche Bing (gratuit, sans clé)."""
from ._http import ProviderError, session

KIND = "suggest"
NAME = "bing-suggest"
LABEL = "Bing Suggest"


def suggest(query, lang="fr", country="FR"):
    r = session(lang, country).get(
        "https://api.bing.com/osjson.aspx",
        params={"query": query, "mkt": f"{lang}-{country}"}, timeout=15)
    if r.status_code != 200:
        raise ProviderError(f"Bing Suggest a répondu {r.status_code}")
    data = r.json()
    return [s for s in data[1] if isinstance(s, str)]

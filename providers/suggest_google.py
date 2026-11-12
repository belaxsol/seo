"""Suggestions de la barre de recherche Google (gratuit, sans clé)."""
from ._http import ProviderError, session

KIND = "suggest"
NAME = "google-suggest"
LABEL = "Google Suggest"


def suggest(query, lang="fr", country="FR"):
    r = session(lang, country).get(
        "https://suggestqueries.google.com/complete/search",
        params={"client": "firefox", "q": query, "hl": lang, "gl": country.lower(),
                "ie": "utf-8", "oe": "utf-8"}, timeout=15)
    if r.status_code != 200:
        raise ProviderError(f"Google Suggest a répondu {r.status_code}")
    r.encoding = "utf-8"
    data = r.json()
    return [s for s in data[1] if isinstance(s, str)]

"""Résultats DuckDuckGo (version HTML, sans JavaScript, sans clé)."""
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from ._http import ProviderError, session

KIND = "serp"
NAME = "duckduckgo"
LABEL = "DuckDuckGo"


def search(query, lang="fr", country="FR", max_results=30):
    s = session(lang, country)
    r = s.post("https://html.duckduckgo.com/html/",
               data={"q": query, "kl": f"{country.lower()}-{lang.lower()}"}, timeout=20)
    if r.status_code != 200:
        raise ProviderError(f"DuckDuckGo a répondu {r.status_code} (blocage temporaire probable)")
    soup = BeautifulSoup(r.text, "html.parser")
    urls = []
    for a in soup.select("a.result__a"):
        href = a.get("href", "")
        if "uddg=" in href:
            href = parse_qs(urlparse(href).query).get("uddg", [""])[0]
        if href.startswith("http") and "duckduckgo.com/y.js" not in href:
            urls.append(href)
    if not urls:
        raise ProviderError("DuckDuckGo n'a renvoyé aucun résultat lisible (blocage probable)")
    return urls[:max_results]

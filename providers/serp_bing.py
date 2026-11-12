"""Résultats Bing par simple requête HTTP (sans clé)."""
import base64
import random
import time
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from ._http import ProviderError, session

KIND = "serp"
NAME = "bing"
LABEL = "Bing"


def _real_url(href):
    # Bing enveloppe parfois les liens : /ck/a?...&u=a1<base64 de l'URL>
    if "bing.com/ck/a" in href:
        u = parse_qs(urlparse(href).query).get("u", [""])[0]
        if u.startswith("a1"):
            b = u[2:] + "=" * (-len(u[2:]) % 4)
            try:
                return base64.urlsafe_b64decode(b).decode("utf-8", "ignore")
            except ValueError:
                return ""
    return href


def search(query, lang="fr", country="FR", max_results=30):
    s = session(lang, country)
    urls = []
    for first in range(1, max_results + 1, 10):
        r = s.get("https://www.bing.com/search",
                  params={"q": query, "first": first, "setlang": lang, "cc": country,
                          "mkt": f"{lang}-{country}"}, timeout=20)
        if r.status_code != 200:
            raise ProviderError(f"Bing a répondu {r.status_code} (blocage temporaire probable)")
        soup = BeautifulSoup(r.text, "html.parser")
        found = 0
        for a in soup.select("li.b_algo h2 a"):
            href = _real_url(a.get("href", ""))
            if href.startswith("http") and href not in urls:
                urls.append(href)
                found += 1
        if not found:
            break
        time.sleep(random.uniform(1, 2))
    if not urls:
        raise ProviderError("Bing n'a renvoyé aucun résultat lisible (blocage ou page de vérification)")
    return urls[:max_results]

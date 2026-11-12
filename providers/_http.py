import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


class ProviderError(Exception):
    """Erreur lisible par l'utilisateur (blocage, limite, format inattendu)."""


def session(lang="fr", country="FR"):
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": f"{lang}-{country},{lang};q=0.9,en;q=0.5",
    })
    return s

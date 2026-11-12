"""Fournisseurs de données, découverts automatiquement.

Pour en ajouter un : créer un fichier dans ce dossier avec
  KIND  = "serp" | "suggest" | "trends"
  NAME  = identifiant court (stocké en base)
  LABEL = nom affiché
et la fonction attendue pour ce type :
  serp    -> search(query, lang, country, max_results) -> liste d'URL classées
  suggest -> suggest(query, lang, country) -> liste de mots-clés
  trends  -> fetch(keyword, lang, country, period) -> liste de (date ISO, valeur)
Optionnel : available() -> bool (False si une dépendance manque).
"""
import importlib
import pkgutil

_cache = None


def _load():
    global _cache
    if _cache is None:
        _cache = {}
        for info in pkgutil.iter_modules(__path__):
            if info.name.startswith("_"):
                continue
            mod = importlib.import_module(f"{__name__}.{info.name}")
            if hasattr(mod, "KIND") and hasattr(mod, "NAME"):
                _cache.setdefault(mod.KIND, {})[mod.NAME] = mod
    return _cache


def get(kind):
    """Dictionnaire nom -> module pour un type de fournisseur."""
    return _load().get(kind, {})


def is_available(mod):
    fn = getattr(mod, "available", None)
    return fn() if fn else True

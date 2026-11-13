# SEO Libre

Outil SEO local et open source (licence MIT) : pas d'abonnement, pas de clé d'API,
toutes les données restent dans un fichier SQLite sur votre machine.

## Lancer

Il faut Python 3.9 ou plus récent.

- **Windows** : double-clic sur `lancer.bat`
- **macOS / Linux** : `./lancer.sh`
- ou à la main : `pip install -r requirements.txt` puis `python app.py`

Ouvrir ensuite http://127.0.0.1:5000.

## Les modules

Tout est automatique. Le seul import possible est un crawl Screaming Frog.

| Onglet | Ce qu'il fait |
|---|---|
| **Positions** | Vous listez vos mots-clés, l'outil relève leur position chaque jour sur Google (ou Bing, DuckDuckGo) : évolution à 1, 7 et 30 jours, meilleure position, page classée, courbe et page de résultats complète par mot-clé |
| **Concurrents** | Domaines les plus présents sur vos mots-clés, avec leur position moyenne |
| **Cannibalisation** | Repère les mots-clés où plusieurs de vos pages se disputent la même place : *simultanée* (2 pages ou plus dans le top au dernier relevé) ou *alternante* (la page la mieux classée change 2 fois ou plus en 30 jours). Les variantes d'URL (`www`, paramètres, `#ancre`, slash final) sont regroupées. Utilise les pages de résultats déjà relevées : aucune requête en plus |
| **Mots-clés** | Suggestions Google et Bing (mode approfondi : a → z + questions) ; un clic pour les ajouter au suivi |
| **Tendances** | Courbes Google Trends, rafraîchies chaque semaine |
| **Audit** | Nombre d'URL, de 301, 302 et 404, avec leurs liens entrants et les pages qui font le lien ; règles SEO (titles, H1, contenu mince…). Crawler intégré (HTML brut, respecte robots.txt), ou import de l'export « Internal » de Screaming Frog (`internal_all.csv`) |

### Suivi de positions

- Une position par jour et par mot-clé ; le graphique montre l'évolution sur 30 jours, 90 jours ou 1 an.
- Tant que l'outil est ouvert, le relevé du jour se fait tout seul, pour tous les sites.
  Pour un relevé sans ouvrir l'interface (Planificateur de tâches Windows, cron) : `python app.py track`.
- Les moteurs bloquent les relevés trop nombreux : l'outil attend 4 à 9 secondes entre
  deux requêtes et s'arrête sur un moteur après 3 échecs. Prévu pour quelques dizaines
  de mots-clés, pas des milliers.
- **Google** est le moteur par défaut. Il passe par un vrai navigateur (Chromium, installé
  au premier lancement). Si Google affiche un captcha, le relevé s'arrête et reprend au
  passage suivant ; pour le résoudre à la main, lancez l'outil avec
  `SEOLIBRE_GOOGLE_VISIBLE=1` (la fenêtre du navigateur s'affiche).
- Profondeur du relevé : top 30 par défaut, réglable avec `SEOLIBRE_DEPTH` (par exemple 50
  ou 100). Plus c'est profond, plus Google bloque vite.

### Limites à connaître

- Pas de volumes de recherche chiffrés : aucune source gratuite et automatique n'en
  fournit. L'onglet Tendances donne la popularité relative et la saisonnalité.
- Google Trends est lu par un accès non officiel que Google limite (erreur 429) : en cas
  de refus, l'outil réessaie au passage suivant.
- Pas d'analyse de backlinks (cela demande un index du web entier).
- Les sélecteurs de pages de résultats cassent quand un moteur change son HTML : c'est
  l'endroit à corriger en premier si un relevé échoue (`providers/serp_*.py`).

## Ajouter une source de données

Chaque source est un fichier dans `providers/`, chargé automatiquement :

```python
# providers/serp_monmoteur.py
KIND = "serp"            # "serp", "suggest" ou "trends"
NAME = "monmoteur"       # identifiant stocké en base
LABEL = "Mon moteur"     # nom affiché

def search(query, lang="fr", country="FR", max_results=30):
    return ["https://…", "https://…"]   # URL dans l'ordre du classement
```

- `suggest(query, lang, country)` renvoie une liste de mots-clés.
- `fetch(keyword, lang, country, period)` renvoie une liste de `(date ISO, valeur)`.
- Lever `ProviderError("message")` pour afficher une erreur lisible.

## Organisation du code

| Fichier | Rôle |
|---|---|
| `app.py` | Interface web (Flask) et routes |
| `db.py` | Schéma SQLite |
| `tracker.py` | Relevé des positions et planification |
| `crawler.py` | Crawler et règles d'audit (`audit()` : ajouter une règle = une ligne) |
| `importers.py` | Lecture de l'export CSV Screaming Frog |
| `providers/` | Sources de données interchangeables |

Variables d'environnement : `SEOLIBRE_PORT`, `SEOLIBRE_DB` (chemin de la base),
`SEOLIBRE_AUTO_TRACK=0` pour couper le relevé automatique.

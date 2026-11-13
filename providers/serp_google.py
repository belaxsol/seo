"""Résultats Google via un vrai navigateur (Playwright + Chromium).

Google exige JavaScript et affiche un captcha quand les requêtes sont trop
nombreuses. Le navigateur garde un profil (cookies, consentement) dans le
dossier .profil-google/ pour ressembler à une navigation normale.

Si Google demande un captcha : lancer l'outil avec SEOLIBRE_GOOGLE_VISIBLE=1,
la fenêtre du navigateur s'affiche et vous pouvez le résoudre à la main
(l'outil attend jusqu'à 3 minutes).
"""
import os
import random
import re
from urllib.parse import quote_plus, urlparse

from ._http import UA, ProviderError

KIND = "serp"
NAME = "google"
LABEL = "Google"
GOOGLE_URL = "https://www.google.com/search"
LAUNCH_ARGS = []
PROFILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       ".profil-google")


def available():
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _real_url(href, text):
    """URL réelle du résultat : le lien direct, ou (redirection google.com/goto) l'adresse
    affichée sous le titre (« https://domaine › chemin »)."""
    if href.startswith("http") and "google." not in urlparse(href).netloc:
        return href
    m = re.search(r"https?://[^\s›]+(?:\s›\s[^\n›]+)*", text or "")
    if not m:
        return None
    url = re.sub(r"\s›\s", "/", m.group(0)).replace("…", "").strip()
    return url if "google." not in urlparse(url).netloc else None


def _accept_overlay(page):
    """Accepte la fenêtre de consentement superposée (même URL, sans page consent.)."""
    page.wait_for_timeout(1500)
    btn = page.locator("button:has-text('Tout accepter'), button:has-text('Accept all')")
    if btn.count():
        try:
            btn.first.click(timeout=4000)
            page.wait_for_timeout(1500)
        except Exception:
            pass


def search(query, lang="fr", country="FR", max_results=30):
    if not available():
        raise ProviderError("Playwright n'est pas installé : relancez lancer.bat / lancer.sh")
    from playwright.sync_api import sync_playwright

    visible = os.environ.get("SEOLIBRE_GOOGLE_VISIBLE") == "1"
    urls = []
    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                PROFILE, headless=not visible, locale=f"{lang}-{country}",
                user_agent=UA, args=LAUNCH_ARGS)
        except Exception as e:
            raise ProviderError("Chromium ne démarre pas (python -m playwright install "
                                f"chromium) : {str(e)[:120]}")
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for start in range(0, max_results, 10):
                page.goto(f"{GOOGLE_URL}?q={quote_plus(query)}&hl={lang}"
                          f"&gl={country.lower()}&start={start}",
                          wait_until="domcontentloaded", timeout=30000)
                if "consent." in page.url:
                    try:
                        page.locator("button:has-text('Tout accepter'), "
                                     "button:has-text('Accept all')").first.click(timeout=4000)
                        page.wait_for_load_state("domcontentloaded")
                    except Exception:
                        if not visible:
                            raise ProviderError("Page de consentement Google non franchie : "
                                                "relancez avec SEOLIBRE_GOOGLE_VISIBLE=1")
                        # Fenêtre visible : on laisse l'utilisateur accepter à la main.
                        try:
                            page.wait_for_url(lambda u: "consent." not in u, timeout=180000)
                        except Exception:
                            raise ProviderError("Consentement Google non accepté dans les 3 minutes")
                _accept_overlay(page)
                if "/sorry/" in page.url:
                    if not visible:
                        raise ProviderError("Google demande un captcha. Relancez avec "
                                            "SEOLIBRE_GOOGLE_VISIBLE=1 pour le résoudre à la main.")
                    try:
                        page.wait_for_url(lambda u: "/sorry/" not in u, timeout=180000)
                    except Exception:
                        raise ProviderError("Captcha Google non résolu dans les 3 minutes")
                page.wait_for_timeout(random.randint(1500, 3500))
                if visible and not page.query_selector("a:has(h3)"):
                    # Bandeau de consentement ou page inattendue : attendre l'utilisateur.
                    try:
                        page.wait_for_selector("a:has(h3)", timeout=180000)
                    except Exception:
                        pass
                items = page.eval_on_selector_all(
                    "a:has(h3)", "els => els.map(e => [e.href, e.innerText])")
                found = 0
                for href, text in items:
                    h = _real_url(href, text)
                    if h and h not in urls:
                        urls.append(h)
                        found += 1
                if not found:
                    break
        finally:
            ctx.close()
    if not urls:
        raise ProviderError("Google n'a renvoyé aucun résultat lisible")
    return urls[:max_results]

"""Import d'un crawl Screaming Frog (CSV). Les en-têtes sont reconnus de façon souple
(anglais ou français, virgule / point-virgule / tabulation, UTF-8 ou UTF-16)."""
import csv
import io
import re
import unicodedata


class ImportError_(Exception):
    pass


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def read_table(raw):
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", "replace")
    head = text[:20000]
    lines = max(1, head.count("\n"))
    if head.count("\t") >= lines:
        delim = "\t"
    elif head.count(";") > head.count(","):
        delim = ";"
    else:
        delim = ","
    return [r for r in csv.reader(io.StringIO(text), delimiter=delim)]


def parse_num(s):
    """'1 000', '1,000', '10K – 100K', '1,5' -> premier nombre trouvé."""
    if not s:
        return None
    t = re.sub(r"[\s \xa0]", "", str(s)).lower()
    t = re.sub(r"(?<=\d)[,.](?=\d{3}(?:\D|$))", "", t).replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)([km]?)", t)
    if not m:
        return None
    return float(m.group(1)) * {"": 1, "k": 1e3, "m": 1e6}[m.group(2)]


def _header(rows, test):
    for i, row in enumerate(rows[:30]):
        cells = [norm(c) for c in row]
        if any(test(c) for c in cells):
            return i, cells
    raise ImportError_("En-têtes non reconnus dans ce fichier.")


def _get(row, i):
    return row[i].strip() if i is not None and i < len(row) else None


# ---------- Screaming Frog (export « Internal », onglet All ou HTML) ----------

SF = {
    "url": ("address", "adresse"),
    "status": ("status code", "code de statut", "code d etat"),
    "content_type": ("content type", "content", "type de contenu"),
    "title": ("title 1", "titre 1"),
    "meta_desc": ("meta description 1",),
    "h1": ("h1 1",),
    "h1_2": ("h1 2",),
    "word_count": ("word count", "nombre de mots"),
    "depth": ("crawl depth", "profondeur de crawl", "profondeur d exploration"),
    "canonical": ("canonical link element 1",),
    "indexability": ("indexability", "indexabilite"),
    "inlinks": ("unique inlinks", "inlinks", "liens entrants uniques", "liens entrants"),
    "response": ("response time", "temps de reponse"),
    "redirect_to": ("redirect url", "url de redirection"),
}


def _int(s):
    n = parse_num(s)
    return int(n) if n is not None else None


def import_screaming_frog(raw):
    rows = read_table(raw)
    i, cells = _header(rows, lambda c: c in SF["url"])
    idx = {}
    for key, names in SF.items():
        for n in names:
            if n in cells:
                idx[key] = cells.index(n)
                break
    pages = []
    for row in rows[i + 1:]:
        url = _get(row, idx.get("url"))
        if not url or not url.startswith("http"):
            continue
        h1 = _get(row, idx.get("h1"))
        resp = _get(row, idx.get("response"))
        ix = norm(_get(row, idx.get("indexability")) or "")
        try:
            ms = int(float(resp.replace(",", ".")) * 1000) if resp else None
        except ValueError:
            ms = None
        pages.append({
            "url": url, "status": _int(_get(row, idx.get("status"))) or 0,
            "content_type": _get(row, idx.get("content_type")),
            "title": _get(row, idx.get("title")) or None,
            "meta_desc": _get(row, idx.get("meta_desc")) or None,
            "h1": h1 or None,
            "h1_count": (1 if h1 else 0) + (1 if _get(row, idx.get("h1_2")) else 0),
            "word_count": _int(_get(row, idx.get("word_count"))),
            "depth": _int(_get(row, idx.get("depth"))),
            "canonical": _get(row, idx.get("canonical")) or None,
            "indexable": None if not ix else (0 if ix.startswith("non") else 1),
            "inlinks": _int(_get(row, idx.get("inlinks"))),
            "response_ms": ms,
            "redirect_to": _get(row, idx.get("redirect_to")) or None,
        })
    if not pages:
        raise ImportError_("Aucune URL trouvée : exportez l'onglet « Internal » de Screaming Frog en CSV.")
    return pages

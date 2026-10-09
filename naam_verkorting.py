"""Korte namen uit Yahoo's longName. Pure functies, geen netwerk of database."""
import re

# iShares staat niet in fund_family ("BlackRock Asset Management Ireland - ETF").
MERK_WOORDEN = {"ishares"}
RUIS_WOORDEN = {"core", "etf", "ucits"}
VALUTA_CODES = {"usd", "eur", "gbp", "chf"}
KLASSE_WOORDEN = {"acc", "dist", "dis", "accumulating", "distributing"}
JURIDISCHE_ACHTERVOEGSELS = {"inc", "incorporated", "nv", "co", "ltd", "corp", "plc", "se", "ag"}
# Langste eerst: "Ordinary Shares New" mag niet als "Ordinary Shares" + "New" blijven hangen.
AANDEELSOORT_STAARTEN = (("ordinary", "shares", "new"), ("ordinary", "shares"), ("registered", "shares"))


def _sleutel(woord):
    return re.sub(r"[^a-z0-9&]", "", woord.lower())


def _woorden(tekst):
    if not tekst:
        return set()
    return {w for w in re.split(r"[^a-z0-9&]+", tekst.lower()) if w}


def _is_losse_letter(woord):
    return re.fullmatch(r"[a-z]", _sleutel(woord)) is not None


def _verwijder_merk_vooraan(tokens, merk_sleutels):
    """(rest, eerste verwijderde merkwoord of None)."""
    i = 0
    while i < len(tokens) and _sleutel(tokens[i]) in merk_sleutels:
        i += 1
    return tokens[i:], (tokens[0] if i else None)


def _verwijder_staart(tokens):
    t = list(tokens)
    while t and _sleutel(t[-1]) in KLASSE_WOORDEN:
        t.pop()
    if len(t) >= 2 and _is_losse_letter(t[-1]) and _sleutel(t[-2]) in VALUTA_CODES:
        t.pop()
    if t and _sleutel(t[-1]) in VALUTA_CODES:
        t.pop()
        if t and _is_losse_letter(t[-1]) and not (len(t) >= 2 and _sleutel(t[-2]) == "class"):
            t.pop()
    return t


def _verwijder_aandeelsoort(tokens):
    for staart in AANDEELSOORT_STAARTEN:
        if len(tokens) > len(staart) and tuple(_sleutel(w) for w in tokens[-len(staart):]) == staart:
            return tokens[:-len(staart)]
    return list(tokens)


def _verwijder_juridische_achtervoegsels(tokens):
    t = list(tokens)
    while len(t) > 1:
        if _sleutel(t[-1]) in JURIDISCHE_ACHTERVOEGSELS:
            t.pop()
        elif len(t) > 2 and _sleutel(t[-1]) == "a" and _sleutel(t[-2]) == "class":
            t.pop()
            t.pop()
        else:
            break
    if t:
        t[-1] = t[-1].rstrip(",;")
    return t


def _korte_naam_en_merk(long_name, fund_family):
    if not long_name or not long_name.strip():
        return None, None
    long_name = long_name.strip()

    merk_sleutels = MERK_WOORDEN | _woorden(fund_family)
    tokens, merk = _verwijder_merk_vooraan(long_name.split(), merk_sleutels)
    tokens = [w for w in tokens if _sleutel(w) not in RUIS_WOORDEN]
    tokens = _verwijder_aandeelsoort(tokens)
    tokens = _verwijder_staart(tokens)
    tokens = _verwijder_juridische_achtervoegsels(tokens)

    naam = " ".join(tokens).strip()
    return (naam or long_name), merk


def korte_naam(long_name, fund_family):
    return _korte_naam_en_merk(long_name, fund_family)[0]


def _onderscheidende_woorden(long_name, andere_long_names):
    """Woorden (zonder haakjes) uit long_name die niet in alle andere namen voorkomen, in volgorde."""
    andere_sets = [{_sleutel(w) for w in n.split()} for n in andere_long_names]
    uit = []
    for woord in long_name.split():
        sleutel = _sleutel(woord)
        if not sleutel or sleutel in RUIS_WOORDEN:
            continue
        if all(sleutel in s for s in andere_sets):
            continue
        uit.append(woord.strip("()[],;"))
    return uit


def _is_uniek(namen):
    kleine = [n.lower() for n in namen.values()]
    return len(set(kleine)) == len(kleine)


def _los_botsing_op(groep, long_names, namen, merken):
    """Voegt per categorie woorden toe aan de namen in `groep` tot ze uniek zijn."""
    onderscheidend = {
        t: _onderscheidende_woorden(long_names[t], [long_names[o] for o in groep if o != t]) for t in groep
    }
    categorieen = [
        lambda t, w: _sleutel(w) in KLASSE_WOORDEN,
        lambda t, w: merken[t] is not None and w == merken[t].strip("()[],;"),
        lambda t, w: _sleutel(w) in VALUTA_CODES,
    ]
    for hoort_erbij in categorieen:
        for t in groep:
            extra = [w for w in onderscheidend[t] if hoort_erbij(t, w)]
            if extra:
                namen[t] = " ".join([namen[t]] + extra)
        if _is_uniek({t: namen[t] for t in groep}):
            return

    botsend = [t for t in groep if any(namen[t].lower() == namen[o].lower() for o in groep if o != t)]
    for t in botsend:
        namen[t] = f"{namen[t]} {t.split('.')[0]}"


def kies_korte_namen(posities):
    """posities: {ticker: {"long_name", "fund_family"}} -> {ticker: korte naam}; zonder long_name ontbreekt de ticker."""
    namen = {}
    merken = {}
    long_names = {}
    for ticker, p in posities.items():
        naam, merk = _korte_naam_en_merk(p.get("long_name"), p.get("fund_family"))
        if naam is None:
            continue
        namen[ticker] = naam
        merken[ticker] = merk
        long_names[ticker] = p["long_name"].strip()

    per_naam = {}
    for ticker, naam in namen.items():
        per_naam.setdefault(naam.lower(), []).append(ticker)

    for groep in per_naam.values():
        if len(groep) > 1:
            _los_botsing_op(groep, long_names, namen, merken)
    return namen


# Woordgrens: "DIS" mag niet matchen in "DISCOVERY", "ACC" niet in "ACCESS".
DIS_KENMERKEN = ("DIS", "DIST", "DISTRIBUTING", "DISTRIBUTION")
ACC_KENMERKEN = ("ACC", "ACCUMULATING", "ACCUMULATION")


def _heeft_kenmerk(naam, kenmerken):
    return any(re.search(rf"\b{k}\b", str(naam or ""), re.IGNORECASE) for k in kenmerken)


def uitkeringsvorm(naam):
    """'DIS', 'ACC' of None (geen of beide kenmerken)."""
    dis, acc = _heeft_kenmerk(naam, DIS_KENMERKEN), _heeft_kenmerk(naam, ACC_KENMERKEN)
    return "DIS" if dis and not acc else "ACC" if acc and not dis else None


def uitkeringsvorm_strijdig(naam_a, naam_b):
    """Alleen True als beide namen een uitkeringsvorm noemen en die verschillen: de namen zijn geen officiële bron."""
    a, b = uitkeringsvorm(naam_a), uitkeringsvorm(naam_b)
    return bool(a and b and a != b)

// Tabblad Zoeken & wat als: een effect zoeken, het profiel bekijken en het bijkopen van een aantal stuks simuleren.
// De rekenwerk zit in de backend; hier alleen aanvragen, weergave en het negeren van verouderde antwoorden.

function nieuweWatAlsToestand() {
    return {
        resultaten: [], isinUitZoek: null, gekozen: null, kandidaten: null, isinKeuze: null, gekozenIsin: null,
        profiel: null, uitkomst: null, bezig: null,
        koers: { periode: WAT_ALS_KOERS_STANDAARD, perDag: false },
        // Per stap het nummer van de laatste aanvraag: een trager, ouder antwoord wordt genegeerd.
        volgnr: { zoek: 0, isin: 0, profiel: 0, bereken: 0, koers: 0 },
    };
}

const WAT_ALS_KOERS_PERIODES = ["1j", "3j", "7j"];
const WAT_ALS_KOERS_STANDAARD = "1j";
const WAT_ALS_KOERS_KLEUR = "#2c6fbb";
const WAT_ALS_TOTAAL_KLEUR = "#2c7a4b";
const WAT_ALS_WEEK_TIP = "Weekgemiddelde; dividend telt vanaf de week na de ex-datum";

let watAlsToestand = nieuweWatAlsToestand();
let watAlsKoersGrafiek = null;

function watAlsEl(id) {
    return document.getElementById(id);
}

function watAlsMaak(tag, klasse, tekst) {
    const el = document.createElement(tag);
    if (klasse) el.className = klasse;
    if (tekst !== undefined) el.textContent = tekst;
    return el;
}

function watAlsInfoTip(label, tekst) {
    const tip = watAlsMaak("span", "infoTip", tekst);
    tip.dataset.label = label;
    activeerInfoTip(tip);
    return tip;
}

function resetWatAls() {
    watAlsToestand = nieuweWatAlsToestand();
    watAlsEl("watAlsZoekInvoer").value = "";
    watAlsEl("watAlsAantal").value = "";
    wisWatAlsMeldingen();
    wisWatAlsKeuze();
    watAlsEl("watAlsResultaten").replaceChildren();
    zetWatAlsBezig(null);
}

function toonWatAls() {
    vernieuwWatAlsKnoppen();
}

function wisWatAlsMeldingen() {
    ["watAlsZoekMelding", "watAlsFout"].forEach(id => {
        const el = watAlsEl(id);
        el.hidden = true;
        el.textContent = "";
    });
}

function toonWatAlsMelding(id, tekst) {
    const el = watAlsEl(id);
    el.textContent = tekst;
    el.hidden = false;
}

// Alles onder de resultatenlijst; lopende aanvragen voor die onderdelen worden ongeldig.
function wisWatAlsKeuze() {
    const t = watAlsToestand;
    t.gekozen = null;
    t.kandidaten = null;
    t.isinKeuze = null;
    t.gekozenIsin = null;
    t.profiel = null;
    t.uitkomst = null;
    t.volgnr.isin += 1;
    t.volgnr.profiel += 1;
    t.volgnr.bereken += 1;
    watAlsEl("watAlsIsin").replaceChildren();
    wisWatAlsProfielEnUitkomst();
}

function wisWatAlsProfielEnUitkomst() {
    const t = watAlsToestand;
    t.profiel = null;
    t.uitkomst = null;
    t.volgnr.bereken += 1;
    wisWatAlsKoers();
    watAlsEl("watAlsProfiel").replaceChildren();
    watAlsEl("watAlsInvoerBlok").hidden = true;
    watAlsEl("watAlsWaarde").textContent = "";
    watAlsEl("watAlsUitkomst").replaceChildren();
}

function zetWatAlsBezig(tekst) {
    watAlsToestand.bezig = tekst;
    const status = watAlsEl("watAlsStatus");
    status.textContent = tekst || "";
    status.hidden = !tekst;
    vernieuwWatAlsKnoppen();
}

function vernieuwWatAlsKnoppen() {
    const t = watAlsToestand;
    const bezig = t.bezig !== null;
    watAlsEl("watAlsZoekKnop").disabled = bezig;
    document.querySelectorAll("#watAlsResultaten button, #watAlsResultaten select, #watAlsIsin input").forEach(el => {
        el.disabled = bezig;
    });
    const bevestig = watAlsEl("watAlsIsinBevestig");
    if (bevestig) bevestig.disabled = bezig || !t.isinKeuze;
    watAlsEl("watAlsAantal").disabled = bezig;
    watAlsEl("watAlsBerekenKnop").disabled = bezig || !t.profiel || t.profiel.koers_eur == null
        || !watAlsAantal(watAlsEl("watAlsAantal").value).geldig;
}

// Geeft {ok, status, data}, of null als er intussen een nieuwere aanvraag voor deze stap is (of de tab is gereset).
async function watAlsVraag(stap, bezigTekst, url, opties) {
    const t = watAlsToestand;
    t.volgnr[stap] += 1;
    const mijn = t.volgnr[stap];
    const actueel = () => t === watAlsToestand && mijn === t.volgnr[stap];
    zetWatAlsBezig(bezigTekst);
    try {
        const res = await fetchMetTimeout(url, opties);
        let data = null;
        try {
            data = await res.json();
        } catch (_) {
            data = null;
        }
        return actueel() ? { ok: res.ok, status: res.status, data } : null;
    } catch (fout) {
        return actueel() ? { ok: false, status: fout.message === "TIMEOUT" ? 504 : 0, data: null } : null;
    } finally {
        if (actueel()) zetWatAlsBezig(null);
    }
}

function toonWatAlsFout(antwoord) {
    toonWatAlsMelding("watAlsFout", watAlsFoutTekst(antwoord.status, antwoord.data && antwoord.data.error));
}

// --- Zoeken ---

async function zoekWatAls() {
    const query = watAlsEl("watAlsZoekInvoer").value.trim();
    wisWatAlsMeldingen();
    if (query.length < WAT_ALS_MIN_ZOEKTEKENS) {
        toonWatAlsMelding("watAlsZoekMelding", `Vul minstens ${WAT_ALS_MIN_ZOEKTEKENS} tekens in.`);
        return;
    }
    if (query.length > WAT_ALS_MAX_ZOEKTEKENS) {
        toonWatAlsMelding("watAlsZoekMelding", `Gebruik hoogstens ${WAT_ALS_MAX_ZOEKTEKENS} tekens.`);
        return;
    }
    const antwoord = await watAlsVraag("zoek", "Zoeken...", `/api/zoek?q=${encodeURIComponent(query)}`);
    if (!antwoord) return;
    wisWatAlsKeuze();
    if (!antwoord.ok) {
        watAlsToestand.resultaten = [];
        renderWatAlsResultaten();
        toonWatAlsFout(antwoord);
        return;
    }
    watAlsToestand.resultaten = antwoord.data.resultaten || [];
    watAlsToestand.isinUitZoek = antwoord.data.isin_uit_zoekopdracht || null;
    renderWatAlsResultaten();
}

function renderWatAlsResultaten() {
    const t = watAlsToestand;
    const plek = watAlsEl("watAlsResultaten");
    plek.replaceChildren();
    if (t.resultaten.length === 0) {
        plek.append(watAlsMaak("p", "grijsTekst", "Niets gevonden. Probeer een ticker, naam of ISIN."));
        return;
    }
    t.resultaten.forEach((resultaat, index) => {
        const isGekozen = t.gekozen !== null && t.gekozen.index === index;
        const kaart = watAlsMaak("div", isGekozen ? "watAlsResultaat gekozen" : "watAlsResultaat");
        const kop = watAlsMaak("div", "watAlsResultaatKop");
        kop.append(watAlsMaak("strong", null, resultaat.naam), watAlsMaak("span", "watAlsBadge", watAlsTypeLabel(resultaat.type)));

        const select = document.createElement("select");
        select.setAttribute("aria-label", `Notering van ${resultaat.naam}`);
        resultaat.noteringen.forEach(notering => {
            const optie = watAlsMaak("option", null, watAlsNoteringLabel(notering));
            optie.value = notering.ticker;
            select.append(optie);
        });
        select.value = isGekozen ? t.gekozen.ticker : resultaat.voorkeur_ticker;

        const knop = watAlsMaak("button", "keuzeKnop", isGekozen ? "Gekozen" : "Kies");
        knop.type = "button";
        knop.addEventListener("click", () => kiesWatAlsResultaat(index, select.value));
        // Een andere notering van het gekozen effect laadt meteen opnieuw.
        select.addEventListener("change", () => { if (isGekozen) kiesWatAlsResultaat(index, select.value); });

        kaart.append(kop, watAlsMaak("div", "watAlsKeuzeRij"));
        kaart.lastChild.append(select, knop);
        plek.append(kaart);
    });
    vernieuwWatAlsKnoppen();
}

// --- Kiezen: ISIN en profiel ---

async function kiesWatAlsResultaat(index, ticker) {
    const t = watAlsToestand;
    const resultaat = t.resultaten[index];
    wisWatAlsMeldingen();
    wisWatAlsKeuze();
    t.gekozen = { index, naam: resultaat.naam, type: resultaat.type, ticker };
    renderWatAlsResultaten();
    if (resultaat.type === "ETF") await laadWatAlsIsin();
    else await laadWatAlsProfiel(null);
}

async function laadWatAlsIsin() {
    const t = watAlsToestand;
    const parameters = new URLSearchParams({ ticker: t.gekozen.ticker, naam: t.gekozen.naam, isin: t.isinUitZoek || "" });
    const antwoord = await watAlsVraag("isin", "ISIN-varianten ophalen...", `/api/isin-kandidaten?${parameters}`);
    if (!antwoord) return;
    if (!antwoord.ok) {
        toonWatAlsFout(antwoord);
        return;
    }
    t.kandidaten = antwoord.data;
    const voorgeselecteerd = antwoord.data.kandidaten.find(k => k.voorgeselecteerd);
    t.isinKeuze = voorgeselecteerd ? voorgeselecteerd.isin : null;
    renderWatAlsIsin();
    if (antwoord.data.zekerheid === "zeker") {
        t.gekozenIsin = antwoord.data.kandidaten[0].isin;
        await laadWatAlsProfiel(t.gekozenIsin);
    } else if (antwoord.data.zekerheid === "geen") {
        await laadWatAlsProfiel(null);
    }
    // Bij "kiezen" wacht het profiel op de bevestiging van de gebruiker.
}

function renderWatAlsIsin() {
    const t = watAlsToestand;
    const plek = watAlsEl("watAlsIsin");
    plek.replaceChildren();
    const { zekerheid, bron, kandidaten } = t.kandidaten;
    plek.append(watAlsMaak("p", zekerheid === "kiezen" ? "melding" : "kleineMelding", watAlsIsinTekst(zekerheid, bron)));
    if (zekerheid === "zeker") {
        plek.append(watAlsMaak("p", "kleineMelding", `${kandidaten[0].naam} · ${kandidaten[0].isin}`));
    } else if (zekerheid === "kiezen") {
        const groep = watAlsMaak("div", "watAlsIsinKeuzes");
        kandidaten.forEach(kandidaat => {
            const label = watAlsMaak("label", "watAlsRadioLabel");
            const radio = document.createElement("input");
            radio.type = "radio";
            radio.name = "watAlsIsinKeuze";
            radio.className = "watAlsRadio";
            radio.value = kandidaat.isin;
            radio.checked = kandidaat.isin === t.isinKeuze;
            radio.addEventListener("change", () => {
                t.isinKeuze = kandidaat.isin;
                vernieuwWatAlsKnoppen();
            });
            label.append(radio, `${kandidaat.naam || "Fonds"} · ${kandidaat.isin}`);
            groep.append(label);
        });
        const bevestig = watAlsMaak("button", null, "Gebruik deze ISIN");
        bevestig.type = "button";
        bevestig.id = "watAlsIsinBevestig";
        bevestig.addEventListener("click", () => {
            t.gekozenIsin = t.isinKeuze;
            laadWatAlsProfiel(t.gekozenIsin);
        });
        plek.append(groep, bevestig);
    }
    vernieuwWatAlsKnoppen();
}

async function laadWatAlsProfiel(isin) {
    const t = watAlsToestand;
    wisWatAlsMeldingen();
    wisWatAlsProfielEnUitkomst();
    const parameters = new URLSearchParams({ ticker: t.gekozen.ticker, naam: t.gekozen.naam });
    if (isin) parameters.set("isin", isin);
    const antwoord = await watAlsVraag("profiel", "Profiel ophalen...", `/api/effect-profiel?${parameters}`);
    if (!antwoord) return;
    if (!antwoord.ok) {
        toonWatAlsFout(antwoord);
        return;
    }
    t.profiel = antwoord.data;
    renderWatAlsProfiel();
    watAlsEl("watAlsInvoerBlok").hidden = false;
    werkWatAlsWaardeBij();
    t.koers.periode = WAT_ALS_KOERS_STANDAARD;
    laadWatAlsKoers();
}

// --- Koersgrafiek ---

function wisWatAlsKoers() {
    watAlsToestand.volgnr.koers += 1;
    if (watAlsKoersGrafiek) { watAlsKoersGrafiek.destroy(); watAlsKoersGrafiek = null; }
    watAlsEl("watAlsKoers").hidden = true;
}

function bouwWatAlsKoersBesturing() {
    const keuze = watAlsEl("watAlsKoersPeriode");
    if (keuze.childElementCount > 0) return;
    keuze.replaceChildren(...WAT_ALS_KOERS_PERIODES.map(periode => {
        const knop = watAlsMaak("button", "segmentKnop", periode);
        knop.type = "button";
        knop.dataset.periode = periode;
        knop.addEventListener("click", () => {
            watAlsToestand.koers.periode = periode;
            laadWatAlsKoers();
        });
        return knop;
    }));
    watAlsEl("watAlsKoersTipPlek").append(watAlsInfoTip("Weekgemiddelde", WAT_ALS_WEEK_TIP));
}

function werkWatAlsKoersBesturingBij() {
    const { periode, perDag } = watAlsToestand.koers;
    document.querySelectorAll("#watAlsKoersPeriode .segmentKnop").forEach(knop => {
        knop.classList.toggle("actief", knop.dataset.periode === periode);
    });
    const dagAltijd = periode === WAT_ALS_KOERS_STANDAARD;
    const schakelaar = watAlsEl("watAlsKoersPerDag");
    schakelaar.disabled = dagAltijd;
    schakelaar.checked = perDag;
    watAlsEl("watAlsKoersTipPlek").hidden = dagAltijd || perDag;
}

function zetWatAlsKoersMelding(tekst, isFout) {
    const melding = watAlsEl("watAlsKoersMelding");
    melding.textContent = tekst || "";
    melding.className = isFout ? "foutTekst" : "kleineMelding";
    melding.hidden = !tekst;
}

async function laadWatAlsKoers() {
    const t = watAlsToestand;
    const mijn = ++t.volgnr.koers;
    const actueel = () => t === watAlsToestand && mijn === t.volgnr.koers;
    const { periode, perDag } = t.koers;

    watAlsEl("watAlsKoers").hidden = false;
    bouwWatAlsKoersBesturing();
    werkWatAlsKoersBesturingBij();
    if (watAlsKoersGrafiek) { watAlsKoersGrafiek.destroy(); watAlsKoersGrafiek = null; }
    watAlsEl("watAlsKoersGrafiekPlek").hidden = true;
    watAlsEl("watAlsKoersSamenvatting").textContent = "";
    zetWatAlsKoersMelding("laden…", false);

    const parameters = new URLSearchParams({ ticker: t.profiel.ticker, periode, per: perDag ? "dag" : "week" });
    let res = null;
    let data = null;
    try {
        res = await fetchMetTimeout(`/api/effect-koers?${parameters}`);
        data = await res.json();
    } catch (fout) {
        data = null;
    }
    if (!actueel()) return;
    if (!res || !res.ok || !data) {
        const grafiekFout = res && res.status === 422 && data && data.error;
        zetWatAlsKoersMelding(grafiekFout || watAlsFoutTekst(res ? res.status : 0, data && data.error), true);
        return;
    }
    zetWatAlsKoersMelding("", false);
    renderWatAlsKoers(data);
}

function watAlsKoersSamenvatting(d) {
    const delen = [`Koers: ${formatPct(d.koers_rendement, 2, true)}`];
    delen.push(d.heeft_dividend
        ? `Totaal incl. dividend: ${formatPct(d.totaal_rendement, 2, true)}`
        : "Geen dividend in deze periode");
    if (d.beschikbaar_sinds) delen.push(`beschikbaar sinds ${formatDatum(d.beschikbaar_sinds)}`);
    return delen.join(" · ");
}

function renderWatAlsKoers(d) {
    watAlsEl("watAlsKoersSamenvatting").textContent = watAlsKoersSamenvatting(d);
    watAlsEl("watAlsKoersGrafiekPlek").hidden = false;
    if (watAlsKoersGrafiek) watAlsKoersGrafiek.destroy();

    const lijn = (label, sleutel, kleur) => ({
        label, data: d.punten.map(p => p[sleutel]), borderColor: kleur, pointRadius: 0, pointHoverRadius: 4, borderWidth: 1.5,
    });
    const datasets = [lijn("Koers", "koers_index", WAT_ALS_KOERS_KLEUR)];
    if (d.heeft_dividend) datasets.push(lijn("Totaal incl. dividend", "totaal_index", WAT_ALS_TOTAAL_KLEUR));
    const koersLabel = d.per === "week" ? "Weekgemiddelde koers" : "Koers";

    watAlsKoersGrafiek = new Chart(watAlsEl("watAlsKoersChart"), {
        type: "line",
        data: { labels: d.punten.map(p => formatDatum(p.datum)), datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            locale: "nl-NL",
            interaction: { mode: "index", intersect: false },
            scales: { y: { title: { display: true, text: "Index (start = 100)" } } },
            plugins: {
                legend: { display: true },
                tooltip: {
                    callbacks: {
                        title: ctx => formatDatum(d.punten[ctx[0].dataIndex].datum),
                        label: ctx => `${ctx.dataset.label}: index ${formatGetal(ctx.parsed.y, 1)}`,
                        footer: ctx => `${koersLabel}: ${formatteerEuro(d.punten[ctx[0].dataIndex].koers_eur)}`,
                    },
                },
                datalabels: { display: false },
            },
        },
    });
}

function maakWatAlsLijst(titel, rijen) {
    const blok = watAlsMaak("div", "watAlsLijstBlok");
    blok.append(watAlsMaak("h4", null, titel));
    const lijst = watAlsMaak("ul", "watAlsLijst");
    rijen.forEach(rij => lijst.append(watAlsMaak("li", null, `${rij.naam}: ${formatPct(rij.pct, 1)}`)));
    if (rijen.length === 0) lijst.append(watAlsMaak("li", "gedempt", "onbekend"));
    blok.append(lijst);
    return blok;
}

function renderWatAlsProfiel() {
    const profiel = watAlsToestand.profiel;
    const plek = watAlsEl("watAlsProfiel");
    plek.replaceChildren();
    const kaart = watAlsMaak("div", "watAlsProfielKaart");
    kaart.append(watAlsMaak("h3", null, profiel.naam));
    const koers = profiel.koers_eur == null
        ? "geen koers beschikbaar"
        : `${formatteerEuro(profiel.koers_eur)}${profiel.koers_datum ? ` (${formatDatum(profiel.koers_datum)})` : ""}`;
    kaart.append(watAlsMaak("p", "grijsTekst", `${watAlsTypeLabel(profiel.type)} · ${profiel.ticker} · ${koers}`));

    const kolommen = watAlsMaak("div", "watAlsLijstenRij");
    kolommen.append(
        maakWatAlsLijst(`Landen (top ${WAT_ALS_TOP_LANDEN})`, watAlsTopRijen(profiel.land, WAT_ALS_TOP_LANDEN)),
        maakWatAlsLijst(`Sectoren (top ${WAT_ALS_TOP_SECTOREN})`, watAlsTopRijen(profiel.sector, WAT_ALS_TOP_SECTOREN)),
    );
    let bedrijvenBronTekst = null;
    if (profiel.type === "ETF") {
        const bedrijven = watAlsProfielBedrijven(profiel.holdings);
        bedrijvenBronTekst = bedrijven.bronTekst;
        kolommen.append(maakWatAlsLijst(`Bedrijven (top ${WAT_ALS_TOP_PROFIEL_BEDRIJVEN})`, bedrijven.rijen));
    }
    kaart.append(kolommen);
    if (bedrijvenBronTekst) kaart.append(watAlsMaak("p", "kleineMelding", bedrijvenBronTekst));

    if (profiel.holdings) {
        const holdings = profiel.holdings.top.slice(0, WAT_ALS_TOP_HOLDINGS)
            .map(h => ({ naam: h.naam, pct: h.gewicht * 100 }));
        kaart.append(maakWatAlsLijst(`Grootste holdings (${profiel.holdings.aantal} bekend)`, holdings));
    }
    const dividend = watAlsDividendRegel(profiel.dividend);
    const dividendRegel = watAlsMaak("p", "watAlsTipRegel", dividend.tekst);
    if (dividend.tip) dividendRegel.append(" ", watAlsInfoTip(dividend.tip.label, dividend.tip.tekst));
    kaart.append(dividendRegel);
    if (profiel.holdings_route === "isin" && profiel.holdings_fonds) {
        const fonds = profiel.holdings_fonds;
        kaart.append(watAlsMaak("p", "kleineMelding", `Holdings van: ${fonds.naam || "onbekend fonds"} (${fonds.isin})`));
    }
    (profiel.waarschuwingen || []).forEach(code => kaart.append(watAlsMaak("p", "melding", watAlsWaarschuwingTekst(code))));
    plek.append(kaart);
}

// --- Aantal en berekenen ---

function werkWatAlsWaardeBij() {
    const t = watAlsToestand;
    const aantal = watAlsAantal(watAlsEl("watAlsAantal").value);
    watAlsEl("watAlsWaarde").textContent = t.profiel && aantal.geldig
        ? watAlsGeschatteWaardeTekst(aantal.aantal, t.profiel.koers_eur) : "";
    vernieuwWatAlsKnoppen();
}

async function berekenWatAls() {
    const t = watAlsToestand;
    const aantal = watAlsAantal(watAlsEl("watAlsAantal").value);
    if (!t.profiel || !aantal.geldig) return;
    wisWatAlsMeldingen();
    watAlsEl("watAlsUitkomst").replaceChildren();
    const body = { ticker: t.profiel.ticker, aantal: aantal.aantal, naam: t.gekozen.naam };
    if (t.gekozenIsin) body.isin = t.gekozenIsin;
    let url;
    if (huidigeData.code) {
        url = `/api/portfolio/${encodeURIComponent(huidigeData.code)}/wat-als`;
    } else {
        // Bij "niet opslaan" bestaat de portfolio alleen in dit antwoord; de server leidt zelf af welke posities ETF's zijn.
        body.posities = (huidigeData.verdeling || []).map(p => ({ ticker: p.ticker, naam: p.naam, waarde: p.waarde }));
        url = "/api/wat-als";
    }
    const antwoord = await watAlsVraag("bereken", "Berekenen...", url, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!antwoord) return;
    if (!antwoord.ok) {
        toonWatAlsFout(antwoord);
        return;
    }
    t.uitkomst = antwoord.data;
    renderWatAlsUitkomst(antwoord.data);
}

// --- Resultaat ---

function maakWatAlsVerschilCel(rij) {
    const w = watAlsRij(rij);
    const td = watAlsMaak("td", "watAlsVerschilCel");
    const balk = watAlsMaak("span", "watAlsBalk");
    balk.setAttribute("aria-hidden", "true");
    const min = watAlsMaak("span", "watAlsBalkHelft watAlsBalkMin");
    const plus = watAlsMaak("span", "watAlsBalkHelft watAlsBalkPlus");
    if (w.balk.richting !== "geen") {
        const vulling = watAlsMaak("span", `watAlsBalkVulling ${w.balk.richting === "plus" ? "positief" : "negatief"}`);
        vulling.style.width = `${w.balk.breedtePct}%`;
        (w.balk.richting === "plus" ? plus : min).append(vulling);
    }
    balk.append(min, plus);
    td.append(watAlsMaak("span", `watAlsVerschilTekst ${w.verschilKlasse}`, w.verschilTekst), balk);
    return td;
}

function maakWatAlsVoorNaKolommen(naamKop, naamTekst) {
    return [
        { label: naamKop, renderTd: r => maakCel(naamTekst(r)) },
        { label: "Vóór", waarde: r => r.voor_pct, renderTd: r => maakCel(watAlsRij(r).voorTekst) },
        { label: "Ná", waarde: r => r.na_pct, renderTd: r => maakCel(watAlsRij(r).naTekst) },
        { label: "Verschil", waarde: r => r.verschil_pp, renderTd: maakWatAlsVerschilCel },
    ];
}

function maakWatAlsVoorNaTabel(naamKop, rijen, naamTekst = r => r.naam) {
    const wrapper = watAlsMaak("div", "tabelWrapper");
    wrapper.append(maakSorteerbareTabel(maakWatAlsVoorNaKolommen(naamKop, naamTekst), rijen, { legeTekst: "Niets om te tonen." }));
    return wrapper;
}

function maakWatAlsSectie(titel, inhoud, tip) {
    const sectie = watAlsMaak("section", "watAlsSectie");
    const kop = watAlsMaak("h3", tip ? "watAlsTipRegel" : null, titel);
    if (tip) kop.append(" ", watAlsInfoTip(tip.label, tip.tekst));
    sectie.append(kop, ...inhoud);
    return sectie;
}

// De backend levert vóór, ná en verschil per categorie; hier alleen omgezet naar de rijen van de vóór/ná-tabel.
function maakWatAlsEtfAandeelTabel(etfAandeel) {
    const rij = (naam, sleutel) => ({
        naam, voor_pct: etfAandeel.voor[sleutel], na_pct: etfAandeel.na[sleutel], verschil_pp: etfAandeel.verschil_pp[sleutel],
    });
    return maakWatAlsVoorNaTabel("Soort", [rij("ETF", "etf_pct"), rij("Aandeel", "aandeel_pct")]);
}

function watAlsEtfNaam(ticker) {
    const rij = (huidigeData.tickers || []).find(t => t.ticker === ticker);
    return rij && rij.naam ? rij.naam : ticker;
}

const WAT_ALS_OVERLAP_TIP = {
    label: "Al in je portfolio",
    tekst: "Per bedrijf telt het laagste van het gewicht in de aankoop en in je portfolio mee (zoals bij de ETF-overlap). "
        + "Die minima worden opgeteld. Bedrijven worden op naam herkend.",
};

function maakWatAlsOverlapSectie(overlap) {
    const regels = [watAlsMaak("p", "vet", watAlsOverlapRegel(overlap))];
    if (!watAlsOverlapToontLijst(overlap)) return maakWatAlsSectie("Overlap met je portfolio", regels, WAT_ALS_OVERLAP_TIP);
    const rijen = Object.entries(overlap.per_etf).map(([ticker, pct]) => ({ ticker, pct }));
    const kolommen = [
        { label: "Je ETF", renderTd: r => maakCel(watAlsEtfNaam(r.ticker)) },
        {
            label: "Overlap met de aankoop", waarde: r => (Number.isFinite(r.pct) ? r.pct : -1),
            renderTd: r => maakCel(watAlsOverlapEtfTekst(r.pct, overlap.holdings_status)),
        },
    ];
    rijen.sort((a, b) => (Number.isFinite(b.pct) ? b.pct : -1) - (Number.isFinite(a.pct) ? a.pct : -1));
    const wrapper = watAlsMaak("div", "tabelWrapper");
    wrapper.append(maakSorteerbareTabel(kolommen, rijen, { legeTekst: "Je hebt geen andere ETF's om mee te vergelijken." }));
    return maakWatAlsSectie("Overlap met je portfolio", [...regels, wrapper], WAT_ALS_OVERLAP_TIP);
}

function renderWatAlsUitkomst(data) {
    const { wat_als: w, aankoop } = data;
    const plek = watAlsEl("watAlsUitkomst");
    plek.replaceChildren();

    const samenvatting = [
        watAlsMaak("p", "vet", `${aankoop.aantal} × ${aankoop.naam} = ${formatteerEuro(aankoop.waarde_eur)}`),
        watAlsMaak("p", null, `Portfolio: ${formatteerEuro(w.totaal.voor)} → ${formatteerEuro(w.totaal.na)}`),
        watAlsMaak("p", null, watAlsDividendResultaatTekst(aankoop)),
    ];
    const orderkosten = watAlsOrderkostenRegels(data.orderkosten);
    const orderkostenRegel = watAlsMaak("p", "watAlsTipRegel", orderkosten.tekst);
    orderkostenRegel.append(" ", watAlsInfoTip("Orderkosten", orderkosten.tip));
    samenvatting.push(orderkostenRegel);
    if (orderkosten.aansluiting) samenvatting.push(watAlsMaak("p", "melding", orderkosten.aansluiting));
    if (w.aankoop.bijkopen) samenvatting.push(watAlsMaak("p", "kleineMelding", "Je bezit dit al: de aankoop wordt bij je bestaande positie opgeteld."));
    const zelfdeFonds = watAlsMeldingZelfdeFonds(aankoop.zelfde_fonds_als);
    if (zelfdeFonds) samenvatting.push(watAlsMaak("p", "melding", zelfdeFonds));
    plek.append(maakWatAlsSectie("Resultaat", samenvatting));

    const onbekendRegel = onderdeel => {
        const tekst = watAlsOnbekendTekst(w.aankoop, onderdeel);
        return tekst ? [watAlsMaak("p", "melding", tekst)] : null;
    };
    if (w.overlap) plek.append(maakWatAlsOverlapSectie(w.overlap));
    if (w.al_in_bezit) {
        plek.append(maakWatAlsSectie("Heb je dit bedrijf al?", watAlsAlInBezitRegels(w.al_in_bezit).map(tekst => watAlsMaak("p", null, tekst))));
    }

    plek.append(
        maakWatAlsSectie("Posities", [maakWatAlsVoorNaTabel("Positie", w.posities, r => (r.nieuw ? `${r.naam} (nieuw)` : r.naam))]),
        maakWatAlsSectie("ETF en aandelen", [maakWatAlsEtfAandeelTabel(w.etf_aandeel)]),
        maakWatAlsSectie("Land", onbekendRegel("land") || [maakWatAlsVoorNaTabel("Land", w.land)], {
            label: "Percentages bij land",
            tekst: "Percentages ten opzichte van de som van de bedragen per land, niet van je portfoliowaarde. Het verschil is in procentpunten.",
        }),
        maakWatAlsSectie("Sector", onbekendRegel("sector") || [maakWatAlsVoorNaTabel("Sector", w.sector)], {
            label: "Percentages bij sector",
            tekst: "Percentages ten opzichte van de som van de bedragen per sector, niet van je portfoliowaarde. Het verschil is in procentpunten.",
        }),
        maakWatAlsSectie(`Top ${WAT_ALS_TOP_BEDRIJVEN} bedrijven (na de aankoop)`, onbekendRegel("bedrijven") || [
            maakWatAlsVoorNaTabel("Bedrijf", w.bedrijven.slice(0, WAT_ALS_TOP_BEDRIJVEN)),
        ]),
    );
    plek.scrollIntoView({ behavior: "smooth", block: "start" });
}

// --- Listeners ---

watAlsEl("watAlsZoekForm").addEventListener("submit", gebeurtenis => {
    gebeurtenis.preventDefault();
    zoekWatAls();
});

watAlsEl("watAlsAantalForm").addEventListener("submit", gebeurtenis => {
    gebeurtenis.preventDefault();
    berekenWatAls();
});

watAlsEl("watAlsAantal").addEventListener("input", werkWatAlsWaardeBij);

watAlsEl("watAlsKoersPerDag").addEventListener("change", gebeurtenis => {
    watAlsToestand.koers.perDag = gebeurtenis.target.checked;
    laadWatAlsKoers();
});

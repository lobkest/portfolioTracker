// Tabblad Instellingen > Ticker-zekerheid: per positie een kaart met prijscontrole en alternatieven.

const ZEKERHEID_LABELS = {
    zeker: "Zeker",
    onzeker: "Onzeker",
    geen_match: "Geen match gevonden",
    onbekend: "Onbekend (opnieuw uploaden om te verversen)"
};

const TICKER_POSITIE_TIMEOUT_MS = 30000;
const TICKER_UITGEBREID_TIMEOUT_MS = 60000;

const DAGRANGE_UITLEG = "Dagrange = intraday-high/low van Yahoo, met marge: per grens de ruimste van ±5% en €0,50.";

function voegInfoRegelToe(container, label, waarde) {
    const regel = document.createElement("div");
    regel.className = waarde ? "kleinLabel" : "kleinLabel gedempt";
    const labelSpan = document.createElement("span");
    labelSpan.textContent = `${label}: `;
    regel.appendChild(labelSpan);
    regel.appendChild(document.createTextNode(waarde || "onbekend"));
    container.appendChild(regel);
}

function maakBeursRegel(excelBeurs, yahooBeurs, beursKlopt) {
    const beursRegel = document.createElement("div");
    beursRegel.className = "kleinLabel tickerRegel";
    const tekst = `Beurs — Excel: ${excelBeurs || "onbekend"}, Yahoo: ${yahooBeurs || "onbekend"}`;
    if (beursKlopt === false) {
        beursRegel.classList.add("negatief");
        beursRegel.textContent = `⚠️ ${tekst} — komt niet overeen`;
    } else if (beursKlopt === true) {
        beursRegel.classList.add("positief");
        beursRegel.textContent = `✓ ${tekst}`;
    } else if (beursKlopt === "otc_na_delisting") {
        beursRegel.classList.add("positief");
        beursRegel.textContent = `✓ ${tekst} (nu OTC, waarschijnlijk na delisting)`;
    } else {
        beursRegel.textContent = tekst;
    }
    return beursRegel;
}

function maakPrijscontroleTabel(prijsChecks) {
    if (!prijsChecks || prijsChecks.length === 0) {
        const p = document.createElement("p");
        p.className = "tickerNotitie";
        p.textContent = "Geen prijscontrole beschikbaar (geen transacties met een koers > 0 gevonden).";
        return p;
    }

    const wrapper = document.createElement("div");
    wrapper.className = "tabelWrapper";

    const tabel = document.createElement("table");
    tabel.className = "kleineTabel";
    wrapper.appendChild(tabel);

    const kop = document.createElement("tr");
    ["Datum", "Excel-koers", "Yahoo-koers", "Low", "High", "Binnen dagrange"].forEach(tekst => {
        const th = document.createElement("th");
        th.textContent = tekst;
        if (tekst === "Binnen dagrange") th.title = DAGRANGE_UITLEG;
        kop.appendChild(th);
    });
    tabel.appendChild(kop);

    let heeftSplitCorrectie = false;

    prijsChecks.forEach(c => {
        const rij = document.createElement("tr");
        const gecorrigeerd = c.yahoo_koers_gecorrigeerd != null;
        if (gecorrigeerd) heeftSplitCorrectie = true;
        const yahooKoersTekst = c.yahoo_koers == null
            ? "onbekend"
            : gecorrigeerd ? `${c.yahoo_koers_gecorrigeerd.toFixed(3)} *` : c.yahoo_koers.toFixed(3);

        [
            formatDatum(c.datum),
            c.bekende_koers != null ? c.bekende_koers.toFixed(3) : "-",
            yahooKoersTekst,
            c.low != null ? c.low.toFixed(3) : "-",
            c.high != null ? c.high.toFixed(3) : "-",
        ].forEach((tekst, i) => {
            const td = document.createElement("td");
            td.textContent = tekst;
            if (i === 2 && gecorrigeerd) {
                td.title = `Ruwe Yahoo-koers ${c.yahoo_koers.toFixed(3)}, gecorrigeerd voor een split sinds deze `
                    + `datum (factor ×${c.split_factor.toFixed(4)}).`;
            }
            rij.appendChild(td);
        });

        const dagrangeTd = document.createElement("td");
        if (c.binnen_dagrange === true) {
            dagrangeTd.textContent = "✓";
            dagrangeTd.className = "positief";
            dagrangeTd.title = "Excel-koers valt binnen het intraday-high/low van deze handelsdag (±5% of €0,50)";
        } else if (c.binnen_dagrange === false) {
            dagrangeTd.textContent = "✗";
            dagrangeTd.className = "negatief";
            dagrangeTd.title = "Excel-koers valt buiten het intraday-high/low van deze handelsdag (±5% of €0,50)";
        } else {
            dagrangeTd.textContent = "–";
            dagrangeTd.className = "gedempt";
            dagrangeTd.title = "Geen High/Low-data beschikbaar voor deze datum";
        }
        rij.appendChild(dagrangeTd);
        tabel.appendChild(rij);
    });

    if (heeftSplitCorrectie) {
        const voetnoot = document.createElement("p");
        voetnoot.className = "tickerNotitie tickerVoetnoot";
        voetnoot.textContent = "* gecorrigeerd voor een aandelensplitsing die na deze datum heeft plaatsgevonden "
            + "(zweef over de koers voor details).";
        wrapper.appendChild(voetnoot);
    }

    return wrapper;
}

// Meest recente prijscheck met een bekende dagrange.
function laatsteDagrangeUitChecks(prijsChecks) {
    if (!prijsChecks) return null;
    for (let i = prijsChecks.length - 1; i >= 0; i--) {
        const c = prijsChecks[i];
        if (c.high != null && c.low != null) return { high: c.high, low: c.low };
    }
    return null;
}

// isEtf bepaalt de kolommen: land/sector alleen voor aandelen.
function maakAlternatievenTabel(alternatieven, aanbevolenAlternatief, isEtf) {
    const wrapper = document.createElement("div");
    wrapper.className = "tabelWrapper";

    const tabel = document.createElement("table");
    tabel.className = "kleineTabel";
    wrapper.appendChild(tabel);

    const kolomLabels = isEtf
        ? ["Ticker", "Beurs", "Valuta", "Binnen dagrange", ""]
        : ["Ticker", "Beurs", "Land", "Sector", "Valuta", "Binnen dagrange", ""];

    const kop = document.createElement("tr");
    kolomLabels.forEach(tekst => {
        const th = document.createElement("th");
        th.textContent = tekst;
        if (tekst === "Binnen dagrange") {
            th.title = `Op hoeveel van de gecontroleerde datums de Excel-koers binnen de dagrange van deze kandidaat valt. ${DAGRANGE_UITLEG}`;
        }
        kop.appendChild(th);
    });
    tabel.appendChild(kop);

    alternatieven.forEach(alt => {
        const rij = document.createElement("tr");
        const aanbevolen = aanbevolenAlternatief === alt.ticker;
        if (aanbevolen) rij.className = "aanbevolen";

        const dagrangeTekst = !alt.aantal_gecontroleerd
            ? "geen prijsdata"
            : `${alt.aantal_matches}/${alt.aantal_gecontroleerd}${alt.uitkeringsvorm_strijdig ? " (DIS/ACC wijkt af)" : ""}`;
        const waarden = isEtf
            ? [alt.ticker, alt.beurs || "onbekend", alt.valuta || "onbekend", dagrangeTekst]
            : [
                alt.ticker, alt.beurs || "onbekend", alt.land || "onbekend", alt.sector || "onbekend",
                alt.valuta || "onbekend", dagrangeTekst,
            ];
        waarden.forEach(tekst => {
            const td = document.createElement("td");
            td.textContent = tekst;
            rij.appendChild(td);
        });

        const labelTd = document.createElement("td");
        if (aanbevolen) labelTd.textContent = "← aanbevolen";
        rij.appendChild(labelTd);

        tabel.appendChild(rij);
    });

    return wrapper;
}

// Eén regel i.p.v. de ruwe resultaten (soms 100+ rijen); geen regel als er geen oordeel is.
function maakOpenfigiRegel(p) {
    if (p.openfigi_root_bekend == null) return null;

    const regel = document.createElement("div");
    regel.className = "kleinLabel tickerRegel";
    const root = p.ticker ? p.ticker.split(".")[0] : "";

    if (p.openfigi_root_bekend) {
        const n = p.openfigi_root_matches || 0;
        regel.classList.add("positief");
        regel.textContent = `✓ OpenFIGI: root '${root}' bevestigd (${n} resultaat${n === 1 ? "" : "en"})`;
    } else {
        regel.classList.add("negatief", "vet");
        regel.textContent = `⚠️ OpenFIGI: root '${root}' niet gevonden — mogelijk verkeerde ticker`;
    }
    return regel;
}

// __TIJDELIJK, diagnostisch__: samen met openfigi_kandidaten_debug (ticker_zekerheid.py) verwijderen.
function maakOpenfigiKandidatenDebugBlok(p) {
    const debug = p.openfigi_kandidaten_debug;
    if (!debug) return null;

    const details = document.createElement("details");
    details.className = "debugBlok";

    const summary = document.createElement("summary");
    summary.textContent = "🐛 Debug: OpenFIGI-kandidaten";
    details.appendChild(summary);

    const inhoud = document.createElement("div");
    inhoud.className = "debugInhoud";

    if (!debug.aangeroepen) {
        const regel = document.createElement("div");
        regel.textContent = `Niet aangeroepen (${debug.reden || "onbekende reden"}).`;
        inhoud.appendChild(regel);
    } else {
        const rootsRegel = document.createElement("div");
        rootsRegel.textContent = debug.roots.length > 0
            ? `Unieke OpenFIGI-roots voor deze ISIN: ${debug.roots.join(", ")}`
            : "Geen OpenFIGI-resultaten voor deze ISIN.";
        inhoud.appendChild(rootsRegel);

        if (debug.roots.length > 0) {
            const nieuwRegel = document.createElement("div");
            nieuwRegel.textContent = debug.nieuwe_roots.length > 0
                ? `Nieuw doorzocht (nog niet bekend): ${debug.nieuwe_roots.join(", ")}`
                : "Geen enkele root was nieuw — allemaal al bekend.";
            inhoud.appendChild(nieuwRegel);

            if (debug.overgeslagen_roots.length > 0) {
                const overgeslagenRegel = document.createElement("div");
                overgeslagenRegel.textContent = `Overgeslagen (al bekend): ${debug.overgeslagen_roots.join(", ")}`;
                inhoud.appendChild(overgeslagenRegel);
            }

            debug.nieuwe_roots.forEach(root => {
                const resultaten = (debug.yahoo_resultaten && debug.yahoo_resultaten[root]) || [];
                const regel = document.createElement("div");
                regel.className = "tickerRegel";
                if (resultaten.length === 0) {
                    regel.textContent = `Yahoo-zoekopdracht op '${root}': geen resultaten.`;
                } else {
                    const items = resultaten.map(r => `${r.symbol || "?"} (${r.exchange || "?"})`).join(", ");
                    regel.textContent = `Yahoo-zoekopdracht op '${root}': ${items}`;
                }
                inhoud.appendChild(regel);
            });
        }
    }

    details.appendChild(inhoud);
    return details;
}

function maakTickerZekerheidKaart(p) {
    const rij = document.createElement("div");
    rij.className = "tickerKaart";

    if (p.waarschuwing) {
        const banner = document.createElement("div");
        banner.textContent = `⚠️ ${p.waarschuwing}`;
        banner.className = "tickerKaartWaarschuwing";
        rij.appendChild(banner);
    }

    const titel = document.createElement("div");
    const label = ZEKERHEID_LABELS[p.zekerheid] || p.zekerheid;
    const naamStrong = document.createElement("strong");
    naamStrong.textContent = p.naam;
    titel.appendChild(naamStrong);
    titel.appendChild(document.createTextNode(p.ticker ? ` (${p.ticker}) — ${label}` : ` — ${label}`));
    rij.appendChild(titel);

    if (!p.ticker) {
        const geenTicker = document.createElement("p");
        geenTicker.className = "tickerNotitie";
        geenTicker.textContent = "Geen ticker gevonden voor deze positie.";
        rij.appendChild(geenTicker);
        return rij;
    }

    if (p.is_etf) {
        // Voor een ETF is high/low het sterke signaal, niet land/sector.
        voegInfoRegelToe(rij, "Valuta", p.valuta);
        voegInfoRegelToe(rij, "Fondsfamilie", p.fondsfamilie);
        voegInfoRegelToe(rij, "Categorie", p.category);
        rij.appendChild(maakBeursRegel(p.excel_beurs, p.yahoo_beurs, p.beurs_klopt));
        const dagrange = laatsteDagrangeUitChecks(p.prijs_checks);
        if (dagrange) {
            voegInfoRegelToe(rij, "High/Low (laatste controle)", `${dagrange.high.toFixed(3)} / ${dagrange.low.toFixed(3)}`);
        }
    } else {
        // Een ETF heeft geen eigen land: toon het land van de grootste holding, of niets.
        if (p.land) {
            voegInfoRegelToe(rij, "Land", p.land);
        } else if (p.top_holding_land) {
            voegInfoRegelToe(rij, "Land grootste holding", p.top_holding_land);
        }
        voegInfoRegelToe(rij, "Sector", p.sector);
        voegInfoRegelToe(rij, "Valuta", p.valuta);
        voegInfoRegelToe(rij, "Fondsfamilie", p.fondsfamilie);
        voegInfoRegelToe(rij, "Categorie", p.category);
        rij.appendChild(maakBeursRegel(p.excel_beurs, p.yahoo_beurs, p.beurs_klopt));
    }

    const openfigiRegel = maakOpenfigiRegel(p);
    if (openfigiRegel) rij.appendChild(openfigiRegel);

    const openfigiKandidatenDebugBlok = maakOpenfigiKandidatenDebugBlok(p);
    if (openfigiKandidatenDebugBlok) rij.appendChild(openfigiKandidatenDebugBlok);

    const prijsKop = document.createElement("div");
    prijsKop.textContent = "Prijscontrole";
    prijsKop.className = "tickerKop";
    rij.appendChild(prijsKop);
    rij.appendChild(maakPrijscontroleTabel(p.prijs_checks));

    if (p.alternatieven && p.alternatieven.length > 0) {
        const altKop = document.createElement("div");
        altKop.textContent = "Alternatieve kandidaten";
        altKop.className = "tickerKop";
        rij.appendChild(altKop);

        // Per kandidaat: een alternatief kan een ander type zijn dan de positie.
        const aandeelAlternatieven = p.alternatieven.filter(alt => !alt.is_etf);
        const etfAlternatieven = p.alternatieven.filter(alt => alt.is_etf);
        if (aandeelAlternatieven.length > 0) {
            rij.appendChild(maakAlternatievenTabel(aandeelAlternatieven, p.aanbevolen_alternatief, false));
        }
        if (etfAlternatieven.length > 0) {
            rij.appendChild(maakAlternatievenTabel(etfAlternatieven, p.aanbevolen_alternatief, true));
        }
    }

    // Alleen met een code: bij 'niet opslaan' is er niets om te wijzigen.
    if (p.aanbevolen_alternatief && huidigeData.code && p.isin) {
        rij.appendChild(maakTickerWijzigKnop(p));
    }

    return rij;
}

function maakTickerWijzigKnop(p) {
    const blok = document.createElement("div");
    blok.className = "tickerWijzigBlok";

    const knop = document.createElement("button");
    knop.textContent = `Gebruik ${p.aanbevolen_alternatief} als ticker`;
    blok.appendChild(knop);

    const status = document.createElement("span");
    status.className = "kleinLabel tickerWijzigStatus";
    blok.appendChild(status);

    knop.addEventListener("click", async () => {
        knop.disabled = true;
        status.textContent = "Bezig...";
        status.classList.remove("negatief");
        try {
            const res = await fetch(`/api/portfolio/${huidigeData.code}/ticker-zekerheid/wijzig`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ isin: p.isin, beurs: p.excel_beurs, ticker: p.aanbevolen_alternatief }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || "Kon de ticker niet wijzigen.");
            knop.remove();
            status.textContent = `✓ Ticker gewijzigd van ${data.oude_ticker} naar ${data.ticker}. `
                + "Herlaad de pagina om alle tabbladen bij te werken.";
            status.classList.add("positief");
        } catch (e) {
            status.textContent = `⚠️ ${e.message}`;
            status.classList.add("negatief");
            knop.disabled = false;
        }
    });
    return blok;
}

// Maximaal 'limiet' taken tegelijk: niet alles tegelijk (rate limits), niet na elkaar (traag).
async function voerMetConcurrencyLimietUit(items, limiet, taakFn) {
    let volgendeIndex = 0;
    async function werker() {
        while (volgendeIndex < items.length) {
            const i = volgendeIndex++;
            await taakFn(items[i], i);
        }
    }
    const workers = Array.from({ length: Math.min(limiet, items.length) }, () => werker());
    await Promise.all(workers);
}

// Wordt vervangen door de volledige kaart zodra /positie antwoordt.
function maakTickerZekerheidPlaceholder(p) {
    const rij = document.createElement("div");
    rij.className = "tickerKaart";

    const titel = document.createElement("div");
    const naamStrong = document.createElement("strong");
    naamStrong.textContent = p.naam;
    titel.appendChild(naamStrong);
    rij.appendChild(titel);

    const status = document.createElement("p");
    status.className = "tickerZekerheidStatus tickerNotitie";
    status.textContent = "Bezig met controleren...";
    rij.appendChild(status);

    return rij;
}

function toonTickerZekerheidPositieFout(kaart, tekst, p) {
    const status = kaart.querySelector(".tickerZekerheidStatus");
    if (status) {
        status.textContent = `⚠️ ${tekst}`;
        status.classList.add("negatief");
    }

    const oudeKnop = kaart.querySelector(".tickerOpnieuwKnop");
    if (oudeKnop) oudeKnop.remove();
    const knop = document.createElement("button");
    knop.className = "tickerOpnieuwKnop";
    knop.textContent = "Opnieuw proberen";
    knop.addEventListener("click", () => {
        knop.disabled = true;
        knop.textContent = "Bezig...";
        controleerTickerZekerheidPositie(p, kaart);
    });
    kaart.appendChild(knop);
}

async function controleerTickerZekerheidPositie(p, kaart) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), TICKER_POSITIE_TIMEOUT_MS);
    try {
        const url = `/api/portfolio/${huidigeData.code}/ticker-zekerheid/positie`
            + `?isin=${encodeURIComponent(p.isin)}&beurs=${encodeURIComponent(p.beurs)}`;
        const res = await fetch(url, { signal: controller.signal });
        const resultaat = await res.json();
        if (!res.ok) {
            toonTickerZekerheidPositieFout(kaart, resultaat.error || "Kon deze positie niet controleren.", p);
            return;
        }
        kaart.replaceWith(maakTickerZekerheidKaart(resultaat));
    } catch (e) {
        toonTickerZekerheidPositieFout(
            kaart,
            e.name === "AbortError"
                ? "Duurde te lang en is afgebroken."
                : "Netwerkfout bij het controleren van deze positie.",
            p
        );
    } finally {
        clearTimeout(timeoutId);
    }
}

async function toonInstellingenTicker() {
    // 'Niet opslaan' heeft geen code: toon de lichte check die /upload al meestuurde.
    if (!huidigeData.code) {
        toonInstellingenTickerBasis();
        return;
    }

    const sectie = document.getElementById("instellingenTickerSectie");
    sectie.innerHTML = "";

    // Eerst de lijst, dan per positie een losse aanroep: één trage positie blokkeert de rest niet.
    toonLaadOverlay("Posities ophalen...");
    let data;
    try {
        const res = await fetch(`/api/portfolio/${huidigeData.code}/ticker-zekerheid/lijst`);
        data = await res.json();
        if (!res.ok) {
            const foutmelding = document.createElement("p");
            foutmelding.className = "foutTekst";
            foutmelding.textContent = data.error || "Kon de lijst met posities niet ophalen.";
            sectie.appendChild(foutmelding);
            return;
        }
    } catch (e) {
        const foutmelding = document.createElement("p");
        foutmelding.className = "foutTekst";
        foutmelding.textContent = "Kon de lijst met posities niet ophalen (netwerkfout).";
        sectie.appendChild(foutmelding);
        return;
    } finally {
        verbergLaadOverlay();
    }

    const posities = data.posities || [];
    if (posities.length === 0) {
        const p = document.createElement("p");
        p.textContent = "Geen posities gevonden.";
        sectie.appendChild(p);
        return;
    }

    const alleKnop = document.createElement("button");
    alleKnop.textContent = ALLE_PRIJZEN_KNOPTEKST;
    alleKnop.className = "tickerCheckKnop";
    const alleResultaten = document.createElement("div");
    alleResultaten.className = "allePrijzenResultaten";
    alleKnop.addEventListener("click", () => controleerAllePrijzen(posities, alleKnop, alleResultaten));
    sectie.appendChild(alleKnop);
    sectie.appendChild(alleResultaten);

    const kaarten = {};
    posities.forEach(p => {
        const kaart = maakTickerZekerheidPlaceholder(p);
        sectie.appendChild(kaart);
        kaarten[`${p.isin}|${p.beurs}`] = kaart;
    });

    // Max. 4 tegelijk; elke rij wordt bijgewerkt zodra zijn antwoord binnen is.
    await voerMetConcurrencyLimietUit(posities, 4, p => controleerTickerZekerheidPositie(p, kaarten[`${p.isin}|${p.beurs}`]));
}

const ALLE_PRIJZEN_KNOPTEKST = "Controleer alle aankoop-/verkoopprijzen";

function allePrijzenSamenvattingTekst(r) {
    const delen = [`${r.aantal_binnen}/${r.prijs_checks.length} binnen dagrange`];
    if (r.aantal_buiten) delen.push(`${r.aantal_buiten} buiten`);
    if (r.aantal_onbekend) delen.push(`${r.aantal_onbekend} zonder koersdata`);
    return delen.join(", ");
}

function maakAllePrijzenBlok(r) {
    const details = document.createElement("details");
    details.className = "allePrijzenBlok";
    // Alleen openklappen waar iets mis is: de rest is meestal lang en saai.
    details.open = r.aantal_buiten > 0;

    const summary = document.createElement("summary");
    summary.textContent = `${r.aantal_buiten > 0 ? "⚠️" : "✓"} ${r.naam} (${r.ticker || "geen ticker"}) — `
        + (r.ticker ? allePrijzenSamenvattingTekst(r) : "niet te controleren");
    if (r.aantal_buiten > 0) summary.className = "negatief";
    details.appendChild(summary);

    if (r.ticker) details.appendChild(maakPrijscontroleTabel(r.prijs_checks));
    return details;
}

async function controleerAllePrijzenPositie(p, plek, totaal) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), TICKER_POSITIE_TIMEOUT_MS);
    try {
        const url = `/api/portfolio/${huidigeData.code}/ticker-zekerheid/alle-prijzen`
            + `?isin=${encodeURIComponent(p.isin)}&beurs=${encodeURIComponent(p.beurs)}`;
        const res = await fetch(url, { signal: controller.signal });
        const r = await res.json();
        if (!res.ok) throw new Error(r.error || "Kon deze positie niet controleren.");
        plek.replaceWith(maakAllePrijzenBlok(r));
        totaal.binnen += r.aantal_binnen;
        totaal.buiten += r.aantal_buiten;
        totaal.onbekend += r.aantal_onbekend;
    } catch (e) {
        plek.textContent = `⚠️ ${p.naam}: ${e.name === "AbortError" ? "duurde te lang en is afgebroken." : e.message}`;
        plek.classList.add("negatief");
        totaal.mislukt += 1;
    } finally {
        clearTimeout(timeoutId);
    }
}

async function controleerAllePrijzen(posities, knop, resultaten) {
    knop.disabled = true;
    knop.textContent = "Bezig met controleren...";
    resultaten.innerHTML = "";

    const samenvatting = document.createElement("p");
    samenvatting.className = "kleinLabel vet";
    samenvatting.textContent = `Alle transacties van ${posities.length} posities controleren...`;
    resultaten.appendChild(samenvatting);

    const plekken = posities.map(p => {
        const plek = document.createElement("div");
        plek.className = "kleinLabel gedempt";
        plek.textContent = `${p.naam}: bezig...`;
        resultaten.appendChild(plek);
        return plek;
    });

    const totaal = { binnen: 0, buiten: 0, onbekend: 0, mislukt: 0 };
    await voerMetConcurrencyLimietUit(posities, 4, (p, i) => controleerAllePrijzenPositie(p, plekken[i], totaal));

    const delen = [`${totaal.binnen + totaal.buiten + totaal.onbekend} transacties gecontroleerd`,
        `${totaal.binnen} binnen dagrange`, `${totaal.buiten} buiten`];
    if (totaal.onbekend) delen.push(`${totaal.onbekend} zonder koersdata`);
    if (totaal.mislukt) delen.push(`${totaal.mislukt} posities mislukt`);
    samenvatting.textContent = `Klaar: ${delen.join(", ")}.`;
    samenvatting.classList.toggle("negatief", totaal.buiten > 0 || totaal.mislukt > 0);
    samenvatting.classList.toggle("positief", totaal.buiten === 0 && totaal.mislukt === 0);

    knop.disabled = false;
    knop.textContent = ALLE_PRIJZEN_KNOPTEKST;
}

// 'Niet opslaan': de lichte check uit /upload, met een knop voor de volledige check.
function toonInstellingenTickerBasis() {
    const sectie = document.getElementById("instellingenTickerSectie");
    sectie.innerHTML = "";

    const foutEl = document.createElement("p");
    foutEl.className = "foutTekst";
    foutEl.style.display = "none";

    const lijst = document.createElement("div");
    (huidigeData.ticker_zekerheid || []).forEach(p => lijst.appendChild(maakTickerZekerheidKaart(p)));

    const knop = document.createElement("button");
    knop.textContent = "Controleer ticker-zekerheid (uitgebreid)";
    knop.className = "tickerCheckKnop";
    knop.addEventListener("click", () => controleerTickerZekerheidUitgebreid(knop, foutEl, lijst));

    sectie.appendChild(knop);
    sectie.appendChild(foutEl);
    sectie.appendChild(lijst);
}

async function controleerTickerZekerheidUitgebreid(knop, foutEl, lijst) {
    foutEl.style.display = "none";
    knop.disabled = true;
    knop.textContent = "Bezig met controleren...";

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), TICKER_UITGEBREID_TIMEOUT_MS);
    try {
        const res = await fetch("/api/ticker-zekerheid-check", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ posities: huidigeData.ticker_posities_ruw || [] }),
            signal: controller.signal,
        });
        const data = await res.json();
        if (!res.ok) {
            foutEl.textContent = data.error || "Kon ticker-zekerheid niet controleren.";
            foutEl.style.display = "block";
            return;
        }
        huidigeData.ticker_zekerheid = data.posities;
        lijst.innerHTML = "";
        data.posities.forEach(p => lijst.appendChild(maakTickerZekerheidKaart(p)));
        knop.style.display = "none";
    } catch (e) {
        foutEl.textContent = e.name === "AbortError"
            ? "De uitgebreide controle duurt te lang en is afgebroken. Probeer het later opnieuw."
            : "Er ging iets mis bij het controleren (netwerkfout). Probeer het opnieuw.";
        foutEl.style.display = "block";
    } finally {
        clearTimeout(timeoutId);
        knop.disabled = false;
        if (knop.style.display !== "none") knop.textContent = "Controleer ticker-zekerheid (uitgebreid)";
    }
}

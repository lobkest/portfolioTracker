// Tabbladen Land, Sector, Valuta en Beurs: taart of gestapelde staaf, met een gedeelde weergavekeuze.

// "taart" of "staaf", gedeeld tussen Land, Sector, Valuta en Beurs.
let landSectorWeergave = "taart";

function toonPlatteVerdeling(verdelingObj) {
    if (chart) chart.destroy();

    const totaal = Object.values(verdelingObj || {}).filter(bedrag => bedrag > 0).reduce((som, bedrag) => som + bedrag, 0);
    const entries = zichtbareVerdeling(verdelingObj, totaal);
    if (entries.length === 0) {
        document.getElementById("geenData").style.display = "block";
        return;
    }
    document.getElementById("geenData").style.display = "none";

    // "Overig" en daarna "Unknown" altijd onderaan de legenda.
    const NEUTRALE_VOLGORDE = { "Overig": 1, "Unknown": 2 };
    entries.sort((a, b) => {
        const va = NEUTRALE_VOLGORDE[a[0]] || 0;
        const vb = NEUTRALE_VOLGORDE[b[0]] || 0;
        if (va !== vb) return va - vb;
        return b[1] - a[1];
    });

    let kleurIdx = 0;
    const kleuren = entries.map(([naam]) => (naam === "Unknown" || naam === "Overig") ? ONBEKEND_GRIJS : kleurVoorIndex(kleurIdx++));
    const labelMinPct = taartLabelMinPct();

    chart = new Chart(document.getElementById("rendementChart"), {
        type: "pie",
        data: {
            labels: entries.map(([naam]) => naam),
            datasets: [{
                data: entries.map(([, bedrag]) => bedrag),
                backgroundColor: kleuren,
                borderColor: "#fcfcfb",
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: legendaPositie() },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const pct = totaal ? ctx.parsed / totaal * 100 : 0;
                            return `${ctx.label}: ${formatteerEuro(ctx.parsed)} (${formatPct(pct, 1)})`;
                        }
                    }
                },
                datalabels: {
                    color: (ctx) => tekstKleurVoorVlak(kleuren[ctx.dataIndex]),
                    font: { weight: "bold", size: 11 },
                    formatter: (value, ctx) => {
                        const pct = totaal ? (value / totaal * 100) : 0;
                        if (pct < labelMinPct) return null;
                        return [kortNaam(entries[ctx.dataIndex][0]), formatPct(pct, 1)];
                    }
                }
            }
        }
    });
}

let regioCharts = [];

function toonRegioTaarten(regioVerdeling, europa, azie) {
    regioCharts.forEach(c => c.destroy());
    regioCharts = [];
    const regios = [
        { aan: europa, sleutel: "Europe", blok: "regioTaartEuropa", canvas: "regioChartEuropa" },
        { aan: azie, sleutel: "Asia", blok: "regioTaartAzie", canvas: "regioChartAzie" },
    ];
    let ietsGetoond = false;
    regios.forEach(({ aan, sleutel, blok, canvas }) => {
        const entries = Object.entries((regioVerdeling && regioVerdeling[sleutel]) || {}).filter(([, bedrag]) => bedrag > 0);
        const tonen = aan && entries.length > 0;
        document.getElementById(blok).style.display = tonen ? "block" : "none";
        if (!tonen) return;
        ietsGetoond = true;
        entries.sort((a, b) => ((a[0] === "Overig") - (b[0] === "Overig")) || (b[1] - a[1]));
        const totaal = entries.reduce((som, [, bedrag]) => som + bedrag, 0);
        let kleurIdx = 0;
        const kleuren = entries.map(([naam]) => naam === "Overig" ? ONBEKEND_GRIJS : kleurVoorIndex(kleurIdx++));
        const labelMinPct = taartLabelMinPct();
        regioCharts.push(new Chart(document.getElementById(canvas), {
            type: "pie",
            data: {
                labels: entries.map(([naam]) => naam),
                datasets: [{
                    data: entries.map(([, bedrag]) => bedrag),
                    backgroundColor: kleuren,
                    borderColor: "#fcfcfb",
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: "right", labels: { boxWidth: 12, font: { size: 11 } } },
                    datalabels: {
                        color: (ctx) => tekstKleurVoorVlak(kleuren[ctx.dataIndex]),
                        font: { weight: "bold", size: 11 },
                        formatter: (value, ctx) => {
                            const pct = totaal ? (value / totaal * 100) : 0;
                            if (pct < labelMinPct) return null;
                            return [kortNaam(entries[ctx.dataIndex][0]), formatPct(pct, 1)];
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => `${ctx.label}: ${formatteerEuro(ctx.parsed)} (${formatPct(ctx.parsed / totaal * 100, 1)})`
                        }
                    }
                }
            }
        }));
    });
    document.getElementById("landRegioTaarten").style.display = ietsGetoond ? "block" : "none";
}

function toonLand() {
    if (chart) chart.destroy();
    document.getElementById("geenData").style.display = "none";
    const europa = document.getElementById("europaCheckbox").checked;
    const azie = document.getElementById("azieCheckbox").checked;
    const regioWrappers = ["europaCheckboxWrapper", "azieCheckboxWrapper"].map(id => document.getElementById(id));
    toonRegioTaarten(null, false, false);
    if (toonOverTijdIndienGekozen("land", europa, azie)) {
        document.getElementById("landDekkingTekst").style.display = "none";
        regioWrappers.forEach(w => { w.style.display = "flex"; });
        return;
    }
    if (toonVerrijkingWachtstatusIndienNodig()) {
        regioWrappers.forEach(w => { w.style.display = "none"; });
        return;
    }

    const lsv = huidigeData.land_sector_verdeling;
    regioWrappers.forEach(w => { w.style.display = "flex"; });
    toonRegioTaarten(lsv && lsv.regio_verdeling, europa, azie);
    const regioSuffix = europa && azie ? "europa_azie" : europa ? "europa" : azie ? "azie" : "";

    if (landSectorWeergave === "staaf") {
        const tickerNamen = {};
        (huidigeData.tickers || []).forEach(t => { tickerNamen[t.ticker] = t.naam; });
        const totaal = Object.values((lsv && lsv.land) || {}).reduce((s, w) => s + w, 0);
        // Top 10 + "Overig" komt al uit de backend (bewust anders dan de taart).
        const perBron = lsv && lsv[regioSuffix ? `land_per_bron_${regioSuffix}_top` : "land_per_bron_top"];
        chart = renderGestapeldeStaafgrafiek("rendementChart", perBron, tickerNamen, { totaal });
    } else {
        // De samengevoegde varianten komen al uit de backend; de toggle kost geen request.
        toonPlatteVerdeling(lsv && lsv[regioSuffix ? `land_${regioSuffix}` : "land"]);
    }

    // Verklaart een deel van "Unknown", en waar het land via een proxy-ETF benaderd is.
    const dekkingTekst = document.getElementById("landDekkingTekst");
    const tickerNamen = {};
    (huidigeData.tickers || []).forEach(t => { tickerNamen[t.ticker] = t.naam; });
    const regels = landDekkingRegels(lsv && lsv.per_etf, tickerNamen);
    dekkingTekst.replaceChildren(...regels.flatMap((regel, i) => (i > 0 ? [document.createElement("br"), regel] : [regel])));
    dekkingTekst.style.display = regels.length > 0 ? "block" : "none";
}

function toonSector() {
    if (chart) chart.destroy();
    document.getElementById("geenData").style.display = "none";
    if (toonOverTijdIndienGekozen("sector")) return;
    if (toonVerrijkingWachtstatusIndienNodig()) return;

    const lsv = huidigeData.land_sector_verdeling;
    if (landSectorWeergave === "staaf") {
        const tickerNamen = {};
        (huidigeData.tickers || []).forEach(t => { tickerNamen[t.ticker] = t.naam; });
        const totaal = Object.values((lsv && lsv.sector) || {}).reduce((s, w) => s + w, 0);
        chart = renderGestapeldeStaafgrafiek("rendementChart", lsv && lsv.sector_per_bron, tickerNamen, { totaal });
    } else {
        toonPlatteVerdeling(lsv && lsv.sector);
    }
}

function toonValuta() {
    if (chart) chart.destroy();
    document.getElementById("geenData").style.display = "none";
    if (toonOverTijdIndienGekozen("valuta")) return;
    if (toonVerrijkingWachtstatusIndienNodig()) return;

    const vv = huidigeData.valuta_verdeling;
    if (landSectorWeergave === "staaf") {
        const tickerNamen = {};
        (huidigeData.tickers || []).forEach(t => { tickerNamen[t.ticker] = t.naam; });
        const totaal = Object.values((vv && vv.valuta) || {}).reduce((s, w) => s + w, 0);
        chart = renderGestapeldeStaafgrafiek("rendementChart", vv && vv.valuta_per_bron, tickerNamen, { totaal });
    } else {
        toonPlatteVerdeling(vv && vv.valuta);
    }
}

function maakBeursNaamCel(r) {
    const td = maakCel(r.beurs);
    if (r.inBezit === false) {
        const badge = document.createElement("span");
        badge.className = "badge badgeGesloten";
        badge.textContent = "geen positie";
        td.appendChild(badge);
    }
    return td;
}

function maakGetalCel(tekst, klasse) {
    const td = maakCel(tekst);
    td.className = `getalRechts ${klasse || ""}`.trim();
    return td;
}

function maakRegel(tekst) {
    const regel = document.createElement("div");
    regel.className = "kleineMelding";
    regel.textContent = tekst;
    return regel;
}

function maakBeursUitklapCel(r) {
    const td = maakBeursNaamCel(r);
    const pijl = document.createElement("button");
    pijl.type = "button";
    pijl.className = "uitklapPijl";
    pijl.textContent = "▸";
    pijl.setAttribute("aria-expanded", "false");
    pijl.setAttribute("aria-label", "Details per beurs");
    td.prepend(pijl);
    if (r.positieRegel) td.appendChild(maakPositieRegel(r.positieRegel));
    const details = document.createElement("div");
    details.className = "aansluitingDetails";
    details.hidden = true;
    r.jaarRegels.forEach(tekst => details.appendChild(maakRegel(tekst)));
    if (r.betaaldDitJaar) details.appendChild(maakRegel(DIT_JAAR_BETAALD_TEKST));
    details.appendChild(maakRegel(`Transactiekosten (ter info): ${r.transactiekostenTekst}`));
    const knop = maakAlternatievenKnop(r);
    if (knop) details.appendChild(knop);
    td.appendChild(details);
    return td;
}

function zetUitklapRijOpen(tr, open) {
    tr.querySelector(".uitklapPijl").setAttribute("aria-expanded", String(open));
    tr.querySelector(".uitklapPijl").textContent = open ? "▾" : "▸";
    tr.querySelector(".aansluitingDetails").hidden = !open;
}

function koppelUitklapRijen(tabelEl) {
    const tbody = tabelEl.querySelector("tbody");
    tbody.addEventListener("click", e => {
        const tr = e.target.closest("tr");
        if (!tr || !tr.querySelector(".uitklapPijl")) return;
        if (e.target.closest("button:not(.uitklapPijl), a")) return;
        const open = tr.querySelector(".uitklapPijl").getAttribute("aria-expanded") !== "true";
        zetUitklapRijOpen(tr, open);
    });
}

function maakPositieItem(item) {
    const span = document.createElement("span");
    span.textContent = item.tekst;
    if (item.verkocht) span.className = "positieVerkocht";
    return span;
}

function voegItemsToe(ouder, items, metKommaVoorAfsluiting) {
    items.forEach((item, i) => {
        if (i > 0 || metKommaVoorAfsluiting) ouder.append(", ");
        ouder.appendChild(maakPositieItem(item));
    });
}

function maakPositieRegel(regel) {
    const div = document.createElement("div");
    div.className = "kleineMelding";
    div.append("Posities: ");
    voegItemsToe(div, regel.zichtbaar, false);
    if (regel.restLabel) {
        const rest = document.createElement("span");
        rest.hidden = true;
        voegItemsToe(rest, regel.rest, true);
        const knop = document.createElement("button");
        knop.type = "button";
        knop.className = "linkKnop";
        knop.textContent = regel.restLabel;
        knop.setAttribute("aria-expanded", "false");
        knop.addEventListener("click", () => {
            rest.hidden = !rest.hidden;
            knop.setAttribute("aria-expanded", String(!rest.hidden));
        });
        div.append(" ", knop, rest);
    }
    return div;
}

function maakDetailRegelsCel(regels) {
    const td = document.createElement("td");
    regels.forEach(tekst => td.appendChild(maakRegel(tekst)));
    return td;
}

function maakAansluitingskostenTabel(rijen, totalen) {
    const kolommen = [
        { label: "Beurs", alleenTabel: true, renderTd: maakBeursUitklapCel },
        { label: "Beurs", alleenMobiel: true, mobielRol: "titel", renderTd: maakBeursNaamCel },
        {
            label: "W/V posities",
            alleenTabel: true,
            waarde: r => r.wv_eur,
            renderTd: r => maakGetalCel(r.wvTekst),
        },
        {
            label: "Aansluitingskosten",
            alleenTabel: true,
            waarde: r => r.totaal_eur,
            renderTd: r => {
                const td = maakGetalCel(r.kostenTekst);
                td.appendChild(maakRegel(r.aantalJarenTekst));
                return td;
            },
        },
        {
            label: "Netto",
            alleenTabel: true,
            waarde: r => r.wv_na_eur,
            renderTd: r => maakGetalCel(r.wvNaTekst, `aansluitingNetto ${klasseVoorRendement(r.wv_na_eur)}`),
        },
        { label: "Aansluitingskosten", alleenMobiel: true, mobielRol: "waarde", renderTd: r => maakGetalCel(r.kostenTekst) },
        {
            label: "Netto",
            alleenMobiel: true,
            renderTd: r => maakGetalCel(r.wvNaTekst, `aansluitingNetto ${klasseVoorRendement(r.wv_na_eur)}`),
        },
        { label: "W/V posities", alleenMobiel: true, renderTd: r => maakGetalCel(r.wvTekst) },
        {
            label: "Posities",
            alleenMobiel: true,
            renderTd: r => {
                const td = document.createElement("td");
                if (r.positieRegel) td.textContent = r.positieRegel.volledigeTekst;
                return td;
            },
        },
        { label: "Transactiekosten (ter info)", alleenMobiel: true, renderTd: r => maakGetalCel(r.transactiekostenTekst) },
        {
            label: "Alternatieven",
            alleenMobiel: true,
            renderTd: r => {
                const td = document.createElement("td");
                const knop = maakAlternatievenKnop(r);
                if (knop) td.appendChild(knop);
                return td;
            },
        },
        {
            label: "Per jaar",
            alleenMobiel: true,
            renderTd: r => maakDetailRegelsCel(r.betaaldDitJaar ? [...r.jaarRegels, DIT_JAAR_BETAALD_TEKST] : r.jaarRegels),
        },
    ];
    const resultaat = maakSorteerbareTabel(kolommen, rijen, { klasse: "compacteTabel aansluitingTabel", compactOpMobiel: true });
    const tabelEl = resultaat.querySelector("table");
    if (tabelEl) koppelUitklapRijen(tabelEl);
    if (totalen && tabelEl) {
        const tfoot = document.createElement("tfoot");
        const tr = document.createElement("tr");
        tr.append(maakCel("Totaal"), maakGetalCel(totalen.wvTekst), maakGetalCel(totalen.kostenTekst),
                  maakGetalCel(totalen.wvNaTekst, klasseVoorRendement(totalen.wv_na_eur)));
        tfoot.appendChild(tr);
        tabelEl.appendChild(tfoot);
    }
    return resultaat;
}

function maakAansluitingskostenMeldingen(rijen) {
    const container = document.createElement("div");
    rijen.flatMap(r => r.meldingen).forEach(tekst => {
        const melding = document.createElement("p");
        melding.className = "kleineMelding";
        melding.textContent = tekst;
        container.appendChild(melding);
    });
    return container;
}

// Kerndata: staat er ook als de verrijking (beurs_verdeling) nog laadt, dan zonder "geen positie".
function toonAansluitingskosten() {
    const sectie = document.getElementById("aansluitingskostenSectie");
    const rijen = aansluitingskostenRijen(huidigeData.aansluitingskosten, huidigeData.beurs_verdeling);
    sectie.style.display = rijen.length > 0 ? "block" : "none";
    if (rijen.length > 0) {
        document.getElementById("aansluitingskostenTabel").replaceChildren(
            maakAansluitingskostenTabel(rijen, aansluitingskostenTotalen(huidigeData.aansluitingskosten, rijen.length)),
            maakAansluitingskostenMeldingen(rijen));
    }
}

function toonBeurs() {
    if (chart) chart.destroy();
    document.getElementById("geenData").style.display = "none";
    const tekst = document.getElementById("beursTekst");
    tekst.style.display = "none";
    toonAansluitingskosten();
    if (toonOverTijdIndienGekozen("beurs")) return;
    if (toonVerrijkingWachtstatusIndienNodig()) return;

    const bv = huidigeData.beurs_verdeling;
    const verdeling = bv && bv.beurs;

    if (landSectorWeergave === "staaf") {
        const tickerNamen = {};
        (huidigeData.tickers || []).forEach(t => { tickerNamen[t.ticker] = t.naam; });
        const totaal = Object.values(verdeling || {}).reduce((s, w) => s + w, 0);
        const perBron = bv && bv.beurs_per_bron;
        chart = renderGestapeldeStaafgrafiek("rendementChart", perBron, tickerNamen, { totaal });
    } else {
        toonPlatteVerdeling(verdeling);
    }

    const aantal = bv ? bv.aantal_beurzen : 0;
    if (aantal > 0) {
        tekst.textContent = `Je posities staan op ${aantal} ${aantal === 1 ? "beurs" : "beurzen"}.`;
        tekst.style.display = "block";
    }
}

["europaCheckbox", "azieCheckbox"].forEach(id => {
    document.getElementById(id).addEventListener("change", () => {
        toonLand();
    });
});

// De keuze geldt voor Land, Sector, Valuta én Beurs; de data staat al in huidigeData.
document.getElementById("weergaveToggleBtn").addEventListener("click", () => {
    landSectorWeergave = landSectorWeergave === "taart" ? "staaf" : "taart";
    const view = actieveViewNaam();
    if (view === "land") toonLand();
    else if (view === "sector") toonSector();
    else if (view === "valuta") toonValuta();
    else if (view === "beurs") toonBeurs();
});

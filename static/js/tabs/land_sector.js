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
        regioCharts.push(new Chart(document.getElementById(canvas), {
            type: "pie",
            data: {
                labels: entries.map(([naam]) => naam),
                datasets: [{
                    data: entries.map(([, bedrag]) => bedrag),
                    backgroundColor: entries.map(([naam]) => naam === "Overig" ? ONBEKEND_GRIJS : kleurVoorIndex(kleurIdx++)),
                    borderColor: "#fcfcfb",
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { position: "right", labels: { boxWidth: 12, font: { size: 11 } } },
                    datalabels: { display: false },
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

function maakAansluitingskostenTabel(rijen, totaalTekst) {
    const kolommen = [
        {
            label: "Beurs",
            alleenTabel: true,
            renderTd: r => {
                const td = maakBeursNaamCel(r);
                const perJaar = document.createElement("div");
                perJaar.className = "kleineMelding";
                perJaar.textContent = r.perJaar;
                td.appendChild(perJaar);
                return td;
            },
        },
        { label: "Beurs", alleenMobiel: true, mobielRol: "titel", renderTd: maakBeursNaamCel },
        {
            label: "Totaal betaald",
            mobielRol: "waarde",
            waarde: r => r.totaal_eur,
            renderTd: r => {
                const td = maakCel(r.totaalTekst);
                td.className = "getalRechts";
                return td;
            },
        },
        { label: "Per jaar", alleenMobiel: true, renderTd: r => maakCel(r.perJaar) },
    ];
    const resultaat = maakSorteerbareTabel(kolommen, rijen, { klasse: "compacteTabel aansluitingTabel", compactOpMobiel: true });
    if (totaalTekst) {
        const tfoot = document.createElement("tfoot");
        const tr = document.createElement("tr");
        const totaalCel = maakCel(totaalTekst);
        totaalCel.className = "getalRechts";
        tr.append(maakCel("Totaal"), totaalCel);
        tfoot.appendChild(tr);
        resultaat.querySelector("table").appendChild(tfoot);
    }
    return resultaat;
}

// Kerndata: staat er ook als de verrijking (beurs_verdeling) nog laadt, dan zonder "geen positie".
function toonAansluitingskosten(samenvoegen) {
    const sectie = document.getElementById("aansluitingskostenSectie");
    const rijen = aansluitingskostenRijen(huidigeData.aansluitingskosten, huidigeData.beurs_verdeling, samenvoegen);
    sectie.style.display = rijen.length > 0 ? "block" : "none";
    if (rijen.length > 0) {
        document.getElementById("aansluitingskostenTabel").replaceChildren(
            maakAansluitingskostenTabel(rijen, aansluitingskostenTotaalTekst(huidigeData.aansluitingskosten, rijen.length)));
    }
}

function toonBeurs() {
    if (chart) chart.destroy();
    document.getElementById("geenData").style.display = "none";
    const tekst = document.getElementById("beursTekst");
    tekst.style.display = "none";
    const samenvoegen = document.getElementById("euronextCheckbox").checked;
    toonAansluitingskosten(samenvoegen);
    if (toonOverTijdIndienGekozen("beurs", samenvoegen)) {
        document.getElementById("euronextCheckboxWrapper").style.display = "flex";
        return;
    }
    if (toonVerrijkingWachtstatusIndienNodig()) {
        document.getElementById("euronextCheckboxWrapper").style.display = "none";
        return;
    }

    const bv = huidigeData.beurs_verdeling;
    document.getElementById("euronextCheckboxWrapper").style.display = "flex";
    const verdeling = bv && (samenvoegen ? bv.beurs_euronext : bv.beurs);

    if (landSectorWeergave === "staaf") {
        const tickerNamen = {};
        (huidigeData.tickers || []).forEach(t => { tickerNamen[t.ticker] = t.naam; });
        const totaal = Object.values(verdeling || {}).reduce((s, w) => s + w, 0);
        const perBron = bv && (samenvoegen ? bv.beurs_euronext_per_bron : bv.beurs_per_bron);
        chart = renderGestapeldeStaafgrafiek("rendementChart", perBron, tickerNamen, { totaal });
    } else {
        toonPlatteVerdeling(verdeling);
    }

    const aantal = bv ? (samenvoegen ? bv.aantal_beurzen_euronext : bv.aantal_beurzen) : 0;
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

document.getElementById("euronextCheckbox").addEventListener("change", () => {
    toonBeurs();
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

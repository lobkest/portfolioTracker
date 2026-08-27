let chart = null;
let huidigeData = null;

Chart.register(ChartDataLabels);

// Chart.js herschaalt niet altijd meteen na een rotatie op mobiele Safari;
// de setTimeout is nodig omdat de nieuwe viewport-afmetingen niet altijd
// al klaar staan op het exacte moment van het orientationchange-event.
window.addEventListener("orientationchange", () => {
    setTimeout(() => { if (chart) chart.resize(); }, 200);
});

function formatDatum(isoDatum) {
    const [jaar, maand, dag] = isoDatum.split("-");
    return `${dag}-${maand}-${jaar}`;
}

// Vaste categorische volgorde (nooit cyclisch bedoeld te lezen tot 8 items;
// bij meer holdings dan dat herhalen kleuren, maar elke taart-punt heeft dan
// nog steeds een eigen naam+percentage-label, dus identiteit blijft niet
// afhankelijk van kleur alleen).
const CATEGORISCH_PALET = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
// Vlakken die te licht zijn voor witte tekst erop (donkere inkt leest daar beter).
const LICHTE_VLAKKEN = new Set(["#eda100", "#e87ba4"]);

function kleurVoorIndex(i) {
    return CATEGORISCH_PALET[i % CATEGORISCH_PALET.length];
}

function tekstKleurVoorVlak(hex) {
    return LICHTE_VLAKKEN.has(hex) ? "#0b0b0b" : "#ffffff";
}

function kortNaam(naam, maxLen = 14) {
    return naam.length > maxLen ? naam.slice(0, maxLen - 1) + "…" : naam;
}

// ETF-vlakken krijgen lichte diagonale strepen bovenop hun kleur (i.p.v. een
// rand) als onderscheid t.o.v. aandelen — een herhalend canvas-patroon dat
// Chart.js als backgroundColor accepteert.
function maakStrepenPatroon(kleurHex) {
    const c = document.createElement("canvas");
    c.width = 10;
    c.height = 10;
    const pctx = c.getContext("2d");
    pctx.fillStyle = kleurHex;
    pctx.fillRect(0, 0, 10, 10);
    pctx.strokeStyle = "rgba(255, 255, 255, 0.6)";
    pctx.lineWidth = 2;
    pctx.beginPath();
    [-2, 8].forEach(offset => {
        pctx.moveTo(offset, 10);
        pctx.lineTo(offset + 10, 0);
    });
    pctx.stroke();
    return pctx.createPattern(c, "repeat");
}

function updateChart(labels, datasets) {
    if (chart) chart.destroy();
    const labelsNL = labels.map(formatDatum);
    datasets = datasets.map(ds => ({ pointRadius: 0, pointHoverRadius: 4, borderWidth: 1.5, ...ds }));

    chart = new Chart(document.getElementById("rendementChart"), {
        type: "line",
        data: { labels: labelsNL, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: { y: { beginAtZero: false } },
            plugins: {
                zoom: {
                    pan: { enabled: true, mode: "x" },
                    zoom: {
                        wheel: { enabled: true },
                        pinch: { enabled: true },
                        mode: "x"
                    }
                },
                datalabels: { display: false }
            }
        }
    });
}

function toonPortfolio() {
    const d = huidigeData.chart_data;
    updateChart(d.labels, [
        { label: "Waarde (€)", data: d.waarde, borderColor: "#2c7a4b" },
        { label: "Geïnvesteerd (€)", data: d.geinvesteerd, borderColor: "#3182bd" }
    ]);
}

function toonRendement() {
    const d = huidigeData.chart_data;
    updateChart(d.labels, [
        { label: "Rendement (€)", data: d.rendement, borderColor: "#2c7a4b" }
    ]);
}

function toonPerAandeel(ticker) {
    const d = huidigeData.per_ticker[ticker];
    updateChart(d.labels, [
        { label: "Waarde (€)", data: d.waarde, borderColor: "#2c7a4b" },
        { label: "Geïnvesteerd (€)", data: d.geinvesteerd, borderColor: "#3182bd" }
    ]);
    toonEtfDrilldown(ticker);
}

function maakVerdelingLijst(titel, verdelingObj) {
    const wrapper = document.createElement("div");
    wrapper.style.minWidth = "180px";

    const kop = document.createElement("div");
    kop.textContent = titel;
    kop.style.fontWeight = "bold";
    kop.style.marginBottom = "4px";
    wrapper.appendChild(kop);

    const entries = Object.entries(verdelingObj || {}).sort((a, b) => {
        if (a[0] === "Unknown") return 1;
        if (b[0] === "Unknown") return -1;
        return b[1] - a[1];
    });

    const lijst = document.createElement("ul");
    lijst.style.margin = "0";
    lijst.style.paddingLeft = "18px";
    entries.forEach(([naam, fractie]) => {
        const li = document.createElement("li");
        li.style.fontSize = "0.9em";
        if (naam === "Unknown") li.style.color = "#888";
        li.textContent = `${naam}: ${(fractie * 100).toFixed(1)}%`;
        lijst.appendChild(li);
    });
    wrapper.appendChild(lijst);

    return wrapper;
}

// Alleen aandelen die getagd zijn als ETF hebben een per_etf-entry (zie
// analysis.compute_land_sector_verdeling) — dat gebruiken we hier als
// signaal of dit een ETF is, in plaats van een los "is_etf"-veld door te
// geven: als er geen entry is, is het gewoon een los aandeel/n.v.t.
function toonEtfDrilldown(ticker) {
    const container = document.getElementById("etfDrilldown");
    const lsv = huidigeData.land_sector_verdeling;
    const info = lsv && lsv.per_etf && lsv.per_etf[ticker];

    if (!info) {
        container.style.display = "none";
        container.innerHTML = "";
        return;
    }

    container.innerHTML = "";
    const titel = document.createElement("div");
    titel.textContent = "Land- en sectorverdeling van deze ETF";
    titel.style.fontWeight = "bold";
    titel.style.marginBottom = "4px";
    container.appendChild(titel);

    const brontekst = document.createElement("div");
    brontekst.style.fontSize = "0.8em";
    brontekst.style.marginBottom = "10px";
    if (info.land_bron === "provider_csv") {
        brontekst.style.color = "#2c7a4b";
        brontekst.textContent = "Land: op basis van de volledige holdings-lijst van de fondsprovider.";
    } else {
        brontekst.style.color = "#888";
        brontekst.textContent = "Land: op basis van top-10-holdings (beperkte dekking) — grotendeels \"Unknown\".";
    }
    container.appendChild(brontekst);

    const rij = document.createElement("div");
    rij.style.display = "flex";
    rij.style.gap = "40px";
    rij.appendChild(maakVerdelingLijst("Land", info.land));
    rij.appendChild(maakVerdelingLijst("Sector", info.sector));
    container.appendChild(rij);

    container.style.display = "block";
}

function toonVerdeling() {
    const items = huidigeData.verdeling;
    if (chart) chart.destroy();

    if (!items || items.length === 0) {
        document.getElementById("geenData").style.display = "block";
        document.getElementById("verdelingTekst").style.display = "none";
        return;
    }
    document.getElementById("geenData").style.display = "none";

    const totaal = items.reduce((som, i) => som + i.waarde, 0);
    const etfWaarde = items.filter(i => i.is_etf).reduce((som, i) => som + i.waarde, 0);
    const aandeelWaarde = totaal - etfWaarde;
    const etfPct = totaal ? (etfWaarde / totaal * 100).toFixed(1) : 0;
    const aandeelPct = totaal ? (aandeelWaarde / totaal * 100).toFixed(1) : 0;

    const tekst = document.getElementById("verdelingTekst");
    tekst.style.display = "block";
    tekst.textContent = `ETF's (streeppatroon): ${etfPct}% — Aandelen: ${aandeelPct}%`;

    const kleuren = items.map((_, i) => kleurVoorIndex(i));
    const vlakken = items.map((item, i) => item.is_etf ? maakStrepenPatroon(kleuren[i]) : kleuren[i]);

    chart = new Chart(document.getElementById("rendementChart"), {
        type: "pie",
        data: {
            labels: items.map(i => i.naam),
            datasets: [{
                data: items.map(i => i.waarde),
                backgroundColor: vlakken,
                borderColor: "#fcfcfb",
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { position: "right" },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const pct = totaal ? (ctx.parsed / totaal * 100).toFixed(1) : 0;
                            return `${ctx.label}: €${ctx.parsed.toFixed(2)} (${pct}%)`;
                        }
                    }
                },
                datalabels: {
                    color: (ctx) => tekstKleurVoorVlak(kleuren[ctx.dataIndex]),
                    font: { weight: "bold", size: 11 },
                    formatter: (value, ctx) => {
                        const pct = totaal ? (value / totaal * 100) : 0;
                        if (pct < 3) return null;
                        return [kortNaam(items[ctx.dataIndex].naam), `${pct.toFixed(1)}%`];
                    }
                }
            }
        }
    });
}

// Grijs voor de "Unknown"-bucket in Land/Sector — bewust GEEN kleur uit het
// categorische palet, zodat "we weten dit gewoon niet" nooit opgaat in de
// rest van de kleuren en altijd als aparte, herkenbare categorie oogt.
const ONBEKEND_GRIJS = "#6b6b66";

function toonPlatteVerdeling(verdelingObj) {
    if (chart) chart.destroy();

    const entries = Object.entries(verdelingObj || {}).filter(([, bedrag]) => bedrag > 0);
    if (entries.length === 0) {
        document.getElementById("geenData").style.display = "block";
        return;
    }
    document.getElementById("geenData").style.display = "none";

    // "Unknown" altijd als laatste in de legenda, de rest aflopend op bedrag.
    entries.sort((a, b) => {
        if (a[0] === "Unknown") return 1;
        if (b[0] === "Unknown") return -1;
        return b[1] - a[1];
    });

    const totaal = entries.reduce((som, [, bedrag]) => som + bedrag, 0);
    let kleurIdx = 0;
    const kleuren = entries.map(([naam]) => naam === "Unknown" ? ONBEKEND_GRIJS : kleurVoorIndex(kleurIdx++));

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
                legend: { position: "right" },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const pct = totaal ? (ctx.parsed / totaal * 100).toFixed(1) : 0;
                            return `${ctx.label}: €${ctx.parsed.toFixed(2)} (${pct}%)`;
                        }
                    }
                },
                datalabels: {
                    color: (ctx) => tekstKleurVoorVlak(kleuren[ctx.dataIndex]),
                    font: { weight: "bold", size: 11 },
                    formatter: (value, ctx) => {
                        const pct = totaal ? (value / totaal * 100) : 0;
                        if (pct < 3) return null;
                        return [kortNaam(entries[ctx.dataIndex][0]), `${pct.toFixed(1)}%`];
                    }
                }
            }
        }
    });
}

function toonLand() {
    const lsv = huidigeData.land_sector_verdeling;
    toonPlatteVerdeling(lsv && lsv.land);

    // Welke ETF's hebben nog de beperkte (top-10-only) landdekking? Puur
    // informatief, zodat duidelijk is welk deel van "Unknown" hier
    // structureel is (geen bekende provider-bron) i.p.v. een bug.
    const dekkingTekst = document.getElementById("landDekkingTekst");
    const perEtf = (lsv && lsv.per_etf) || {};
    const beperkt = Object.entries(perEtf)
        .filter(([, info]) => info.land_bron !== "provider_csv")
        .map(([ticker]) => ticker);
    if (beperkt.length > 0) {
        dekkingTekst.textContent = `Beperkte landdekking (alleen top-10-holdings) voor: ${beperkt.join(", ")}.`;
        dekkingTekst.style.display = "block";
    } else {
        dekkingTekst.style.display = "none";
    }
}

function toonSector() {
    const lsv = huidigeData.land_sector_verdeling;
    toonPlatteVerdeling(lsv && lsv.sector);
}

function ververAandeelSelect() {
    const select = document.getElementById("aandeelSelect");
    const huidigeKeuze = select.value;
    select.innerHTML = "";
    huidigeData.tickers.forEach(t => {
        const option = document.createElement("option");
        option.value = t.ticker;
        option.textContent = t.naam;
        select.appendChild(option);
    });
    if (huidigeData.tickers.some(t => t.ticker === huidigeKeuze)) {
        select.value = huidigeKeuze;
    }
}

async function slaBijnaamOp(ticker, bijnaam) {
    const res = await fetch(`/api/portfolio/${huidigeData.code}/bijnaam`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker, bijnaam })
    });
    const data = await res.json();
    if (res.ok) {
        huidigeData = data;
        ververAandeelSelect();
        toonInstellingen();
        const msg = document.getElementById("instellingenMsg");
        msg.textContent = "Bijnaam opgeslagen.";
        msg.style.display = "block";
    }
}

async function resetBijnaam(ticker) {
    const res = await fetch(`/api/portfolio/${huidigeData.code}/reset-bijnaam`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker })
    });
    const data = await res.json();
    if (res.ok) {
        huidigeData = data;
        ververAandeelSelect();
        toonInstellingen();
    }
}

function toonInstellingen() {
    document.getElementById("instellingenMsg").style.display = "none";
    const sectie = document.getElementById("instellingenSectie");
    sectie.innerHTML = "<p>Geef per aandeel/ETF optioneel een eigen bijnaam op.</p>";

    huidigeData.tickers.forEach(t => {
        const rij = document.createElement("div");
        rij.style.marginBottom = "12px";

        const label = document.createElement("div");
        label.textContent = `${t.ticker} (origineel: ${t.echte_naam})`;
        label.style.fontSize = "0.85em";
        label.style.color = "#666";

        const input = document.createElement("input");
        input.type = "text";
        input.value = t.naam;
        input.style.width = "300px";
        input.style.marginRight = "8px";

        const opslaanBtn = document.createElement("button");
        opslaanBtn.textContent = "Opslaan";
        opslaanBtn.onclick = () => slaBijnaamOp(t.ticker, input.value.trim());

        const resetBtn = document.createElement("button");
        resetBtn.textContent = "Reset";
        resetBtn.style.marginLeft = "6px";
        resetBtn.onclick = () => resetBijnaam(t.ticker);

        rij.appendChild(label);
        rij.appendChild(input);
        rij.appendChild(opslaanBtn);
        rij.appendChild(resetBtn);
        sectie.appendChild(rij);
    });
}

const ZEKERHEID_LABELS = {
    zeker: "Zeker",
    onzeker: "Onzeker",
    geen_match: "Geen match gevonden",
    onbekend: "Onbekend (opnieuw uploaden om te verversen)"
};

function voegInfoRegelToe(container, label, waarde) {
    const regel = document.createElement("div");
    regel.style.fontSize = "0.85em";
    regel.style.color = waarde ? "#666" : "#999";
    const labelSpan = document.createElement("span");
    labelSpan.textContent = `${label}: `;
    regel.appendChild(labelSpan);
    regel.appendChild(document.createTextNode(waarde || "onbekend"));
    container.appendChild(regel);
}

function maakBeursRegel(excelBeurs, yahooBeurs, beursKlopt) {
    const beursRegel = document.createElement("div");
    beursRegel.style.fontSize = "0.85em";
    beursRegel.style.marginTop = "2px";
    const tekst = `Beurs — Excel: ${excelBeurs || "onbekend"}, Yahoo: ${yahooBeurs || "onbekend"}`;
    if (beursKlopt === false) {
        beursRegel.style.color = "#9C0006";
        beursRegel.textContent = `⚠️ ${tekst} — komt niet overeen`;
    } else if (beursKlopt === true) {
        beursRegel.style.color = "#2c7a4b";
        beursRegel.textContent = `✓ ${tekst}`;
    } else {
        beursRegel.style.color = "#666";
        beursRegel.textContent = tekst;
    }
    return beursRegel;
}

function maakPrijscontroleTabel(prijsChecks) {
    if (!prijsChecks || prijsChecks.length === 0) {
        const p = document.createElement("p");
        p.style.fontSize = "0.85em";
        p.style.color = "#999";
        p.textContent = "Geen prijscontrole beschikbaar (geen transacties met een koers > 0 gevonden).";
        return p;
    }

    const tabel = document.createElement("table");
    tabel.style.fontSize = "0.85em";
    tabel.style.borderCollapse = "collapse";
    tabel.style.marginTop = "4px";

    const kop = document.createElement("tr");
    ["Datum", "Excel-koers", "Yahoo-koers", "Afwijking", ""].forEach(tekst => {
        const th = document.createElement("th");
        th.textContent = tekst;
        th.style.textAlign = "left";
        th.style.padding = "2px 14px 2px 0";
        th.style.borderBottom = "1px solid #ddd";
        kop.appendChild(th);
    });
    tabel.appendChild(kop);

    prijsChecks.forEach(c => {
        const rij = document.createElement("tr");
        [
            c.datum,
            c.bekende_koers != null ? c.bekende_koers.toFixed(3) : "-",
            c.yahoo_koers != null ? c.yahoo_koers.toFixed(3) : "onbekend",
            c.afwijking_pct != null ? `${c.afwijking_pct.toFixed(1)}%` : "-",
        ].forEach(tekst => {
            const td = document.createElement("td");
            td.textContent = tekst;
            td.style.padding = "2px 14px 2px 0";
            rij.appendChild(td);
        });

        const iconTd = document.createElement("td");
        if (c.match === true) {
            iconTd.textContent = "✓";
            iconTd.style.color = "#2c7a4b";
        } else if (c.match === false) {
            iconTd.textContent = "⚠️";
            iconTd.style.color = "#9C0006";
        } else {
            iconTd.textContent = "?";
            iconTd.style.color = "#999";
        }
        rij.appendChild(iconTd);
        tabel.appendChild(rij);
    });

    return tabel;
}

function maakTickerZekerheidKaart(p) {
    const rij = document.createElement("div");
    rij.style.marginBottom = "18px";
    rij.style.paddingBottom = "14px";
    rij.style.borderBottom = "1px solid #eee";

    if (p.waarschuwing) {
        const banner = document.createElement("div");
        banner.textContent = `⚠️ ${p.waarschuwing}`;
        banner.style.background = "#fdecea";
        banner.style.color = "#9C0006";
        banner.style.padding = "8px 10px";
        banner.style.borderRadius = "4px";
        banner.style.marginBottom = "8px";
        banner.style.fontSize = "0.9em";
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
        geenTicker.style.fontSize = "0.85em";
        geenTicker.style.color = "#999";
        geenTicker.textContent = "Geen ticker gevonden voor deze positie.";
        rij.appendChild(geenTicker);
        return rij;
    }

    // Een ETF heeft geen eigen "Land" (te weinig precisie voor een
    // wereldwijd fonds) — dan tonen we in plaats daarvan het land van de
    // grootste holding, expliciet als apart, anders genoemd veld. Is geen
    // van beide bekend, dan de regel gewoon weglaten i.p.v. "onbekend" te
    // tonen voor iets dat sowieso geen zinnig enkelvoudig antwoord heeft.
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

    const prijsKop = document.createElement("div");
    prijsKop.textContent = "Prijscontrole";
    prijsKop.style.fontWeight = "bold";
    prijsKop.style.marginTop = "8px";
    rij.appendChild(prijsKop);
    rij.appendChild(maakPrijscontroleTabel(p.prijs_checks));

    if (p.alternatieven && p.alternatieven.length > 0) {
        const altKop = document.createElement("div");
        altKop.textContent = "Alternatieve kandidaten";
        altKop.style.fontWeight = "bold";
        altKop.style.marginTop = "8px";
        rij.appendChild(altKop);

        const lijst = document.createElement("ul");
        lijst.style.margin = "4px 0";
        p.alternatieven.forEach(alt => {
            const li = document.createElement("li");
            const afwijkingTekst = alt.gemiddelde_afwijking_pct != null
                ? `${alt.gemiddelde_afwijking_pct.toFixed(1)}% gem. afwijking (${alt.aantal_matches} match(es))`
                : "geen prijsdata";
            li.textContent = `${alt.ticker} (${alt.beurs || "onbekend"}) — land: ${alt.land || "onbekend"}, `
                + `sector: ${alt.sector || "onbekend"}, valuta: ${alt.valuta || "onbekend"} — ${afwijkingTekst}`;
            if (p.aanbevolen_alternatief === alt.ticker) {
                li.style.fontWeight = "bold";
                li.style.color = "#2c7a4b";
                li.textContent += "  ← aanbevolen alternatief";
            }
            lijst.appendChild(li);
        });
        rij.appendChild(lijst);
    }

    return rij;
}

function renderTickerZekerheid(posities) {
    const sectie = document.getElementById("instellingenTickerSectie");
    sectie.innerHTML = "";

    const intro = document.createElement("p");
    intro.textContent = "Hoe zeker is de gevonden ticker per positie? De prijs op een paar transactiedatums wordt "
        + "vergeleken met de historische Yahoo-koers — dat is een sterker signaal dan alleen de beurs-match.";
    sectie.appendChild(intro);

    if (!posities || posities.length === 0) {
        const p = document.createElement("p");
        p.textContent = "Geen posities gevonden.";
        sectie.appendChild(p);
        return;
    }

    posities.forEach(p => sectie.appendChild(maakTickerZekerheidKaart(p)));
}

async function toonInstellingenTicker() {
    // Een eenmalige ("niet opslaan") analyse heeft geen code om de losse
    // /ticker-zekerheid-endpoint mee aan te roepen (die leest transacties
    // uit de database) — maar verifieer_ticker_met_prijs() is een pure
    // functie, dus /upload heeft de volledige, prijsgeverifieerde data toen
    // al meegestuurd onder huidigeData.ticker_zekerheid. Zelfde renderer,
    // geen aparte lichtgewicht weergave nodig.
    if (!huidigeData.code) {
        renderTickerZekerheid(huidigeData.ticker_zekerheid || []);
        return;
    }

    const sectie = document.getElementById("instellingenTickerSectie");
    sectie.innerHTML = "<p>Bezig met controleren van tickers (prijsvergelijking met Yahoo Finance)... "
        + "dit kan een paar seconden duren.</p>";

    let res, data;
    try {
        res = await fetch(`/api/portfolio/${huidigeData.code}/ticker-zekerheid`);
        data = await res.json();
    } catch (e) {
        sectie.innerHTML = "";
        const foutmelding = document.createElement("p");
        foutmelding.style.color = "#9C0006";
        foutmelding.textContent = "Kon ticker-zekerheid niet ophalen.";
        sectie.appendChild(foutmelding);
        return;
    }

    if (!res.ok) {
        sectie.innerHTML = "";
        const foutmelding = document.createElement("p");
        foutmelding.style.color = "#9C0006";
        foutmelding.textContent = data.error || "Kon ticker-zekerheid niet ophalen.";
        sectie.appendChild(foutmelding);
        return;
    }

    renderTickerZekerheid(data.posities);
}

function wisselView(view) {
    const isInstellingenView = view === "instellingen" || view === "instellingen-bijnamen" || view === "instellingen-ticker";

    document.querySelectorAll(".menuBtn[data-view]").forEach(btn => {
        btn.classList.toggle("actief", btn.dataset.view === view);
    });
    document.getElementById("aandeelSelect").style.display = view === "peraandeel" ? "block" : "none";
    document.getElementById("codeText").style.display = (view === "portfolio" && huidigeData.code) ? "block" : "none";
    document.getElementById("nietOpgeslagenText").style.display = (view === "portfolio" && !huidigeData.code) ? "block" : "none";
    document.getElementById("resetZoomBtn").style.display = (view === "verdeling" || view === "land" || view === "sector" || isInstellingenView) ? "none" : "block";
    document.getElementById("chartWrapper").style.display = isInstellingenView ? "none" : "block";
    document.getElementById("instellingenHoofdSectie").style.display = view === "instellingen" ? "block" : "none";
    document.getElementById("instellingenSectie").style.display = view === "instellingen-bijnamen" ? "block" : "none";
    document.getElementById("instellingenTickerSectie").style.display = view === "instellingen-ticker" ? "block" : "none";

    if (view !== "instellingen-bijnamen") {
        document.getElementById("instellingenMsg").style.display = "none";
    }
    if (view !== "verdeling") {
        document.getElementById("verdelingTekst").style.display = "none";
    }
    if (view !== "land") {
        document.getElementById("landDekkingTekst").style.display = "none";
    }
    if (view !== "peraandeel") {
        document.getElementById("etfDrilldown").style.display = "none";
    }

    if (view === "portfolio") toonPortfolio();
    else if (view === "rendement") toonRendement();
    else if (view === "verdeling") toonVerdeling();
    else if (view === "land") toonLand();
    else if (view === "sector") toonSector();
    else if (view === "instellingen-bijnamen") toonInstellingen();
    else if (view === "instellingen-ticker") toonInstellingenTicker();
    else if (view === "peraandeel") {
        const select = document.getElementById("aandeelSelect");
        toonPerAandeel(select.value);
    }
}

function toonDashboard(data) {
    huidigeData = data;
    document.getElementById("uploadSection").style.display = "none";
    document.getElementById("dashboardSection").style.display = "flex";
    document.getElementById("dashCode").textContent = data.code || "";

    const instellingenBtn = document.querySelector('.menuBtn[data-view="instellingen"]');
    const bijnamenBtn = document.querySelector('.menuBtn[data-view="instellingen-bijnamen"]');
    instellingenBtn.style.display = data.code ? "" : "none";
    bijnamenBtn.style.display = data.code ? "" : "none";

    if (!data.chart_data) {
        document.getElementById("geenData").style.display = "block";
        return;
    }

    ververAandeelSelect();
    wisselView("portfolio");
}

document.querySelectorAll(".menuBtn[data-view]").forEach(btn => {
    btn.addEventListener("click", () => wisselView(btn.dataset.view));
});

document.getElementById("aandeelSelect").addEventListener("change", (e) => {
    toonPerAandeel(e.target.value);
});

document.getElementById("resetZoomBtn").addEventListener("click", () => {
    if (chart) chart.resetZoom();
});

document.getElementById("uploadForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    document.getElementById("errorMsg").textContent = "Bezig met verwerken...";
    const formData = new FormData(e.target);
    const res = await fetch("/upload", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) {
        document.getElementById("errorMsg").textContent = data.error || "Er ging iets mis.";
        return;
    }
    document.getElementById("errorMsg").textContent = "";
    toonDashboard(data);
});

document.getElementById("codeForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = document.getElementById("codeInput").value.trim().toUpperCase();
    if (!code) return;
    document.getElementById("errorMsg").textContent = "Bezig met laden...";
    const res = await fetch(`/api/portfolio/${code}`);
    const data = await res.json();
    if (!res.ok) {
        document.getElementById("errorMsg").textContent = data.error || "Code niet gevonden.";
        return;
    }
    document.getElementById("errorMsg").textContent = "";
    toonDashboard(data);
});

document.getElementById("terugKnop").addEventListener("click", () => {
    document.getElementById("dashboardSection").style.display = "none";
    document.getElementById("uploadSection").style.display = "block";
});

document.getElementById("verwijderPortfolioBtn").addEventListener("click", async () => {
    if (!huidigeData || !huidigeData.code) return;
    const zeker = confirm(`Weet je zeker dat je portfolio ${huidigeData.code} permanent wilt verwijderen? Dit kan niet ongedaan worden gemaakt.`);
    if (!zeker) return;

    const res = await fetch(`/api/portfolio/${huidigeData.code}`, { method: "DELETE" });
    if (res.ok) {
        huidigeData = null;
        document.getElementById("dashboardSection").style.display = "none";
        document.getElementById("uploadSection").style.display = "block";
        alert("Portfolio verwijderd.");
    } else {
        alert("Verwijderen is niet gelukt, probeer het later opnieuw.");
    }
});
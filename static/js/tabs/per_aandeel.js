// Tabblad Per aandeel: waarde en geinvesteerd van een positie, met land en sector (bij een ETF de verdeling).

const ETF_HOLDINGS_PAGINA_GROOTTE = 25;

// Per ticker het antwoord van /api/etf-holdings; weg bij een nieuwe portfolio.
const etfHoldingsCache = new Map();

function resetPerAandeel() {
    etfHoldingsCache.clear();
}

function ververAandeelSelect() {
    const select = document.getElementById("aandeelSelect");
    const huidigeKeuze = select.value;
    select.innerHTML = "";
    huidigeData.tickers.forEach(t => {
        const option = document.createElement("option");
        option.value = t.ticker;
        option.textContent = t.nog_in_bezit === false ? `${t.naam} (oud)` : t.naam;
        select.appendChild(option);
    });
    if (huidigeData.tickers.some(t => t.ticker === huidigeKeuze)) {
        select.value = huidigeKeuze;
    }
    bouwAandeelKnoppen();
}

// Website: knoppen naast de grafiek in plaats van de dropdown; die blijft de bron van de keuze (CSS verbergt de een of de ander).
function bouwAandeelKnoppen() {
    const select = document.getElementById("aandeelSelect");
    document.querySelectorAll("[data-aandeel-knoppen]").forEach(houder => {
        const knoppen = Array.from(select.options).map(optie => {
            const knop = document.createElement("button");
            knop.type = "button";
            knop.className = "keuzeKnop";
            knop.dataset.ticker = optie.value;
            knop.textContent = optie.textContent;
            knop.addEventListener("click", () => {
                select.value = optie.value;
                select.dispatchEvent(new Event("change"));
            });
            return knop;
        });
        houder.replaceChildren(...knoppen);
    });
    markeerAandeelKnoppen();
}

function markeerAandeelKnoppen() {
    const gekozen = document.getElementById("aandeelSelect").value;
    document.querySelectorAll("[data-aandeel-knoppen] .keuzeKnop").forEach(knop => {
        knop.classList.toggle("actief", knop.dataset.ticker === gekozen);
    });
}

function toonPerAandeel(ticker) {
    const d = huidigeData.per_ticker[ticker];
    updateChart(d.labels, [
        { label: "Waarde (€)", data: d.waarde, borderColor: "#2c7a4b" },
        { label: "Geïnvesteerd (€)", data: d.geinvesteerd, borderColor: "#3182bd" }
    ]);
    toonPositieLandSector(ticker);
}

function toonPositieLandSector(ticker) {
    toonEtfDrilldown(ticker);
    toonAandeelLandSector(ticker);
}

function toonAandeelLandSector(ticker) {
    const container = document.getElementById("aandeelLandSector");
    const lsv = huidigeData.land_sector_verdeling;
    const regels = aandeelLandSectorRegels(lsv && lsv.per_aandeel, ticker);
    if (!regels) {
        container.style.display = "none";
        return;
    }
    document.getElementById("aandeelLandSectorLijst").replaceChildren(...regels.map(regel => {
        const li = document.createElement("li");
        if (regel.onbekend) li.className = "grijsTekst";
        li.textContent = `${regel.label}: ${regel.tekst}`;
        return li;
    }));
    container.style.display = "block";
}

function maakVerdelingLijst(titel, verdelingObj) {
    const wrapper = document.createElement("div");
    wrapper.className = "verdelingLijst";

    const kop = document.createElement("div");
    kop.textContent = titel;
    kop.className = "sectieTitel";
    wrapper.appendChild(kop);

    const entries = zichtbareVerdeling(verdelingObj, 1).sort((a, b) => {
        if (a[0] === "Unknown") return 1;
        if (b[0] === "Unknown") return -1;
        return b[1] - a[1];
    });

    const lijst = document.createElement("ul");
    entries.forEach(([naam, fractie]) => {
        const li = document.createElement("li");
        if (naam === "Unknown") li.className = "grijsTekst";
        li.textContent = `${naam}: ${formatPct(fractie * 100, 1)}`;
        lijst.appendChild(li);
    });
    wrapper.appendChild(lijst);

    return wrapper;
}

// Een per_etf-entry betekent: dit is een ETF.
function toonEtfDrilldown(ticker) {
    const container = document.getElementById("etfDrilldown");
    const lsv = huidigeData.land_sector_verdeling;
    const info = lsv && lsv.per_etf && lsv.per_etf[ticker];

    if (!info) {
        container.style.display = "none";
        return;
    }

    const brontekst = document.getElementById("etfDrilldownBron");
    const proxyBijschrift = landProxyBijschrift(info);
    if (proxyBijschrift) {
        brontekst.className = "drilldownBron waarschuwingTekst";
        brontekst.textContent = `${proxyBijschrift}.`;
    } else if (info.land_bron === "provider_csv") {
        brontekst.className = "drilldownBron positief";
        brontekst.textContent = "Land: op basis van de volledige holdings-lijst van de fondsprovider.";
    } else {
        brontekst.className = "drilldownBron grijsTekst";
        brontekst.textContent = "Land: op basis van top-10-holdings (beperkte dekking) — grotendeels \"Unknown\".";
    }

    document.getElementById("etfDrilldownLijsten").replaceChildren(
        maakVerdelingLijst("Land", info.land),
        maakVerdelingLijst("Sector", info.sector),
    );

    container.style.display = "block";
    toonEtfHoldings(ticker);
}

async function toonEtfHoldings(ticker) {
    const tabelPlek = document.getElementById("etfHoldingsTabel");
    const bijschrift = document.getElementById("etfHoldingsBijschrift");
    const toonFout = tekst => {
        bijschrift.textContent = "";
        const p = document.createElement("p");
        p.className = "foutTekst";
        p.textContent = tekst;
        tabelPlek.replaceChildren(p);
    };

    if (!etfHoldingsCache.has(ticker)) {
        bijschrift.textContent = "";
        const laadTekst = document.createElement("p");
        laadTekst.className = "grijsTekst";
        laadTekst.textContent = "Bedrijven ophalen...";
        tabelPlek.replaceChildren(laadTekst);

        const waarde = etfPositieWaarde(huidigeData.statistieken, ticker);
        const query = `ticker=${encodeURIComponent(ticker)}` + (waarde !== null ? `&waarde=${waarde}` : "");
        let data;
        try {
            const res = await fetch(`/api/etf-holdings?${query}`);
            data = await res.json();
            if (!res.ok) {
                if (document.getElementById("aandeelSelect").value === ticker) toonFout(data.error || "Bedrijven ophalen is mislukt.");
                return;
            }
        } catch (e) {
            if (document.getElementById("aandeelSelect").value === ticker) toonFout("Bedrijven ophalen is mislukt.");
            return;
        }
        etfHoldingsCache.set(ticker, data);
    }

    if (document.getElementById("aandeelSelect").value !== ticker) return;
    renderEtfHoldings(etfHoldingsCache.get(ticker));
}

function renderEtfHoldings(data) {
    const tabelPlek = document.getElementById("etfHoldingsTabel");
    const bijschrift = document.getElementById("etfHoldingsBijschrift");
    const tekst = etfHoldingsBijschrift(data);
    if (!tekst) {
        bijschrift.textContent = "";
        const p = document.createElement("p");
        p.className = "grijsTekst";
        p.textContent = "Geen holdings gevonden voor deze ETF.";
        tabelPlek.replaceChildren(p);
        return;
    }
    bijschrift.className = tekst.grijs ? "drilldownBron grijsTekst" : "drilldownBron";
    bijschrift.textContent = `${tekst.tekst}.`;

    const weergaveNamen = maakUniekeWeergaveNamen(data.holdings.map(h => h.naam));
    const weergaveNaamPerRuw = {};
    data.holdings.forEach((h, i) => { weergaveNaamPerRuw[h.naam] = weergaveNamen[i]; });

    const kolommen = [
        {
            label: "Bedrijf",
            mobielRol: "titel",
            mobielTekst: h => weergaveNaamPerRuw[h.naam],
            renderTd: h => {
                const td = maakCel(weergaveNaamPerRuw[h.naam]);
                td.title = h.naam;
                return td;
            },
        },
        {
            label: "Weging (%)",
            mobielRol: "subregel",
            mobielTekst: h => formatPct(h.gewicht * 100, 2),
            waarde: h => h.gewicht,
            renderTd: h => maakCel(formatPct(h.gewicht * 100, 2)),
        },
    ];
    if (data.holdings.some(h => h.bedrag_eur !== null)) {
        kolommen.push({
            label: "Jouw deel (€)",
            mobielRol: "waarde",
            waarde: h => h.bedrag_eur,
            renderTd: h => maakCel(formatteerEuro(h.bedrag_eur)),
        });
    }

    const staat = { sorteerKolom: null, sorteerRichting: "desc", pagina: 1, paginaGrootte: ETF_HOLDINGS_PAGINA_GROOTTE };
    tabelPlek.replaceChildren(maakSorteerbareTabel(kolommen, data.holdings, {
        legeTekst: "Geen holdings gevonden.",
        compactOpMobiel: true,
        staat,
    }));
}

document.getElementById("aandeelSelect").addEventListener("change", (e) => {
    markeerAandeelKnoppen();
    if (actieveViewNaam() === "peraandeelaankoop") {
        toonPerAandeelAankoop(e.target.value);
    } else {
        toonPerAandeel(e.target.value);
    }
});

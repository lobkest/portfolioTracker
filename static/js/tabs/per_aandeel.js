// Tabblad Per aandeel: waarde en geinvesteerd van een positie, met land en sector (bij een ETF de verdeling).

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
}

document.getElementById("aandeelSelect").addEventListener("change", (e) => {
    markeerAandeelKnoppen();
    if (actieveViewNaam() === "peraandeelaankoop") {
        toonPerAandeelAankoop(e.target.value);
    } else {
        toonPerAandeel(e.target.value);
    }
});

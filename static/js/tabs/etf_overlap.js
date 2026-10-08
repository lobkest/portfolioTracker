// Tabblad ETF-overlap: matrix van ETF x ETF, met per klik de gedeelde holdings.

let etfOverlapMetVerkocht = true;

function resetEtfOverlap() {
    etfOverlapMetVerkocht = true;
}

function renderEtfOverlapTabel() {
    const sectie = document.getElementById("etfOverlapSectie");
    const wacht = toonVerrijkingWachtstatusIndienNodig();
    sectie.style.display = wacht ? "none" : "block";
    if (wacht) return;

    const matrix = huidigeData.etf_overlap || {};
    const verkocht = new Set((huidigeData.tickers || []).filter(t => t.nog_in_bezit === false).map(t => t.ticker));
    const alleEtfs = Object.keys(matrix);
    const etfs = etfOverlapMetVerkocht ? alleEtfs : alleEtfs.filter(t => !verkocht.has(t));

    const verkochtKnop = document.getElementById("etfOverlapVerkochtBtn");
    verkochtKnop.hidden = !alleEtfs.some(t => verkocht.has(t));
    verkochtKnop.classList.toggle("actief", etfOverlapMetVerkocht);
    verkochtKnop.textContent = `Verkochte ETF's meenemen (${etfOverlapMetVerkocht ? "aan" : "uit"})`;
    verkochtKnop.setAttribute("aria-pressed", String(etfOverlapMetVerkocht));

    document.getElementById("etfOverlapTeWeinig").hidden = etfs.length >= 2;
    document.getElementById("etfOverlapMatrixBlok").hidden = etfs.length < 2;
    if (etfs.length < 2) return;

    const tickerNamen = {};
    (huidigeData.tickers || []).forEach(t => { tickerNamen[t.ticker] = t.naam; });

    // De matrix is symmetrisch: alleen de driehoek boven de diagonaal, dus geen eerste kolom en laatste rij.
    const rijEtfs = etfs.slice(0, -1);
    const kolomEtfs = etfs.slice(1);

    const tabel = document.createElement("table");
    tabel.className = "overlapMatrix";

    const kopRij = document.createElement("tr");
    const hoek = document.createElement("th");
    hoek.className = "rijKop";
    kopRij.appendChild(hoek);
    kolomEtfs.forEach(ticker => {
        const th = document.createElement("th");
        th.textContent = tickerNamen[ticker] || ticker;
        th.title = tickerNamen[ticker] || ticker;
        kopRij.appendChild(th);
    });
    tabel.appendChild(kopRij);

    rijEtfs.forEach((rijTicker, rijIndex) => {
        const tr = document.createElement("tr");
        const rijKop = document.createElement("th");
        rijKop.textContent = tickerNamen[rijTicker] || rijTicker;
        rijKop.title = tickerNamen[rijTicker] || rijTicker;
        rijKop.className = "rijKop";
        tr.appendChild(rijKop);

        kolomEtfs.forEach((kolTicker, kolIndex) => {
            const td = document.createElement("td");
            if (kolIndex + 1 <= rijIndex) {
                td.className = "leeg";
            } else {
                const pct = matrix[rijTicker][kolTicker] * 100;
                td.textContent = formatPct(pct, 0);
                td.style.backgroundColor = `rgba(44, 122, 75, ${Math.min(pct / 100, 1) * 0.7 + (pct > 0 ? 0.1 : 0)})`;
                td.style.color = pct > 50 ? "#fff" : "#333";
                td.className = "klikbaar";
                td.addEventListener("click", () => {
                    toonEtfOverlapDetail(
                        rijTicker, tickerNamen[rijTicker] || rijTicker,
                        kolTicker, tickerNamen[kolTicker] || kolTicker,
                    );
                });
            }
            tr.appendChild(td);
        });
        tabel.appendChild(tr);
    });

    document.getElementById("etfOverlapMatrix").replaceChildren(tabel);
    document.getElementById("etfOverlapDetailContainer").innerHTML = "";
}

// De holdings zitten niet in huidigeData: eigen fetch per klik.
async function toonEtfOverlapDetail(tickerA, naamA, tickerB, naamB) {
    const container = document.getElementById("etfOverlapDetailContainer");
    if (!container) return;
    container.innerHTML = "";

    const kop = document.createElement("h3");
    kop.textContent = `${naamA} vs. ${naamB}`;
    container.appendChild(kop);

    const laadTekst = document.createElement("p");
    laadTekst.className = "grijsTekst";
    laadTekst.textContent = "Holdings ophalen...";
    container.appendChild(laadTekst);

    try {
        const res = await fetch(`/api/etf-overlap-detail?a=${encodeURIComponent(tickerA)}&b=${encodeURIComponent(tickerB)}`);
        const data = await res.json();
        container.innerHTML = "";
        container.appendChild(kop);
        if (!res.ok) {
            const foutP = document.createElement("p");
            foutP.className = "foutTekst";
            foutP.textContent = data.error || "Holdings ophalen is mislukt.";
            container.appendChild(foutP);
            return;
        }
        container.appendChild(maakEtfOverlapDetailTabel(data.holdings || [], naamA, naamB));
    } catch (e) {
        container.innerHTML = "";
        container.appendChild(kop);
        const foutP = document.createElement("p");
        foutP.className = "foutTekst";
        foutP.textContent = "Holdings ophalen is mislukt.";
        container.appendChild(foutP);
    }
}

function maakEtfOverlapDetailTabel(holdings, naamA, naamB) {
    const formatGewicht = w => (w === null || w === undefined) ? "" : formatPct(w * 100, 2);
    // In beide ETF's: zelfde groen als de matrix, maar lichter voor leesbaarheid.
    const isOverlapRij = r => r.gewicht_a !== null && r.gewicht_a !== undefined
        && r.gewicht_b !== null && r.gewicht_b !== undefined;
    const maakRijCel = (r, tekst) => {
        const td = maakCel(tekst);
        if (isOverlapRij(r)) td.className = "overlapBeide";
        return td;
    };

    // Alleen weergave; de ruwe naam staat in de tooltip.
    const weergaveNamen = maakUniekeWeergaveNamen(holdings.map(h => h.holding_naam));
    const weergaveNaamPerRuw = {};
    holdings.forEach((h, i) => { weergaveNaamPerRuw[h.holding_naam] = weergaveNamen[i]; });

    const kolommen = [
        {
            label: "Holding",
            renderTd: r => {
                const td = maakRijCel(r, weergaveNaamPerRuw[r.holding_naam]);
                td.title = r.holding_naam;
                return td;
            },
        },
        {
            label: naamA,
            waarde: r => r.gewicht_a,
            renderTd: r => maakRijCel(r, formatGewicht(r.gewicht_a)),
        },
        {
            label: naamB,
            waarde: r => r.gewicht_b,
            renderTd: r => maakRijCel(r, formatGewicht(r.gewicht_b)),
        },
    ];

    const wrapper = maakSorteerbareTabel(kolommen, holdings, { legeTekst: "Geen holdings gevonden." });
    if (holdings && holdings.length > 0) {
        wrapper.classList.add("scrollbareTabel");
    }
    return wrapper;
}

document.getElementById("etfOverlapVerkochtBtn").addEventListener("click", () => {
    etfOverlapMetVerkocht = !etfOverlapMetVerkocht;
    renderEtfOverlapTabel();
});

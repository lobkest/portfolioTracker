// Tabblad Top-bedrijven: eigen grafiek met een instelbare top-N. De rekenkern staat in bedrijven.js.

// Gekozen N; de backend levert tot BEDRIJVEN_TOP_N_MAX, hier ingekort zonder request.
// null = nog niet gekozen (dan top_n_standaard).
let bedrijvenTopN = null;

// Eigen grafiek op #bedrijvenChart: de hoogte groeit mee met het aantal bedrijven.
let bedrijvenChart = null;

function wisBedrijvenChart() {
    if (bedrijvenChart) { bedrijvenChart.destroy(); bedrijvenChart = null; }
}

function toonBedrijven() {
    const sectie = document.getElementById("bedrijvenSectie");
    const wrapper = document.getElementById("bedrijvenChartWrapper");
    sectie.style.display = "none";
    document.getElementById("geenData").style.display = "none";
    if (toonVerrijkingWachtstatusIndienNodig()) {
        wisBedrijvenChart();
        wrapper.style.display = "none";
        return;
    }

    const data = huidigeData.bedrijven_verdeling;
    if (!data || !data.top || data.top.length === 0) {
        document.getElementById("geenData").style.display = "block";
        wisBedrijvenChart();
        wrapper.style.display = "none";
        return;
    }
    wrapper.style.display = "block";
    sectie.style.display = "block";
    document.getElementById("bedrijvenTopNKeuze").replaceChildren(maakBedrijvenTopNKeuze(data.top.length));

    tekenBedrijven();
}

function maakBedrijvenTopNKeuze(beschikbaar) {
    const rij = document.createElement("div");
    rij.className = "topNRij";

    const label = document.createElement("span");
    label.textContent = "Aantal bedrijven:";
    rij.appendChild(label);

    BEDRIJVEN_TOP_N_KNOPPEN.forEach(n => {
        const knop = document.createElement("button");
        knop.type = "button";
        knop.className = "keuzeKnop";
        knop.dataset.n = String(n);
        knop.textContent = String(n);
        knop.addEventListener("click", () => zetBedrijvenTopN(n));
        rij.appendChild(knop);
    });

    const input = document.createElement("input");
    input.type = "number";
    input.id = "bedrijvenTopNInput";
    input.min = "1";
    input.max = String(beschikbaar);
    input.step = "1";
    input.setAttribute("aria-label", "Zelfgekozen aantal bedrijven");
    const verwerkInvoer = () => zetBedrijvenTopN(kiesTopN(input.value, beschikbaar, bedrijvenTopN));
    input.addEventListener("change", verwerkInvoer);
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            verwerkInvoer();
        }
    });
    rij.appendChild(input);

    return rij;
}

function zetBedrijvenTopN(n) {
    bedrijvenTopN = n;
    tekenBedrijven();
}

// Hertekent zonder de sectie (en het invulveld) te vervangen.
function tekenBedrijven() {
    const data = huidigeData.bedrijven_verdeling;
    const beschikbaar = data.top.length;
    if (bedrijvenTopN === null) bedrijvenTopN = data.top_n_standaard || BEDRIJVEN_TOP_N_KNOPPEN[0];
    bedrijvenTopN = effectieveTopN(bedrijvenTopN, beschikbaar);
    const n = bedrijvenTopN;
    const { getoond, overigPct } = snijTopBedrijven(data, n);

    const subTab = document.querySelector('#subTabs [data-view="bedrijven"]');
    if (subTab) subTab.textContent = bedrijvenTitel(n);
    document.getElementById("bedrijvenDekkingTekst").textContent =
        `Dekking: ${(data.dekking_pct * 100).toFixed(1)}% van de portfoliowaarde is toegewezen aan een bekend bedrijf. Het restant (bedrijven buiten de top ${n} + niet-gedekte ETF-holdings, samen ${overigPct.toFixed(1)}%) is hier niet in weergegeven.`;

    document.querySelectorAll("#bedrijvenSectie .keuzeKnop").forEach(knop => {
        const knopN = Number(knop.dataset.n);
        knop.classList.toggle("actief", knopN === n);
        knop.setAttribute("aria-pressed", String(knopN === n));
        knop.disabled = knopN > beschikbaar;
        knop.title = knop.disabled ? `Er zijn maar ${beschikbaar} bedrijven beschikbaar.` : "";
    });
    document.getElementById("bedrijvenTopNInput").value = String(n);

    const ruweNamen = getoond.map(e => e.bedrijf);
    const weergaveNamen = maakUniekeWeergaveNamen(ruweNamen);
    const weergaveNaamPerRuw = {};
    ruweNamen.forEach((ruw, i) => { weergaveNaamPerRuw[ruw] = weergaveNamen[i]; });

    const smalScherm = window.innerWidth <= 768;
    const horizontaal = gebruikHorizontaleStaven(n, window.innerWidth);
    const maxTekensPerRegel = horizontaal ? (smalScherm ? 20 : 28) : 14;
    // Liggend: ~36px per bedrijf plus ruimte voor as en legenda; staand: de CSS-hoogte.
    document.getElementById("bedrijvenChartWrapper").style.height = horizontaal
        ? `${Math.max(400, getoond.length * 36 + 140)}px`
        : "";

    const bronNamen = {};
    (data.bronnen || []).forEach(b => { bronNamen[b.ticker] = b.naam; });

    const categorieData = {};
    getoond.forEach(entry => { categorieData[entry.bedrijf] = entry.per_bron; });

    wisBedrijvenChart();
    bedrijvenChart = renderGestapeldeStaafgrafiek("bedrijvenChart", categorieData, bronNamen, {
        horizontaal,
        labelVoorCategorie: cat => breekLabelAf(weergaveNaamPerRuw[cat], maxTekensPerRegel),
        tooltipTitel: cat => weergaveNaamPerRuw[cat],
        tooltipFooter: cat => weergaveNaamPerRuw[cat] === cat ? [] : [`Origineel: ${cat}`],
    });
}

// Top-bedrijven opnieuw tekenen (staand/liggend) bij het passeren van het mobiele breakpoint.
window.matchMedia("(max-width: 768px)").addEventListener("change", () => {
    const data = huidigeData && huidigeData.bedrijven_verdeling;
    if (actieveViewNaam() === "bedrijven" && data && data.top && data.top.length) {
        tekenBedrijven();
    }
});

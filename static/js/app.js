
let chart = null;
let huidigeData = null;

// null (geen aparte /verrijking, bv. bij 'niet opslaan'), "laden", "fout" of "klaar".
let verrijkingStatus = null;

// setTimeout: bij orientationchange zijn de nieuwe afmetingen nog niet klaar (mobiele Safari).
// window is hier als je je telefoon kantelt bv. 
window.addEventListener("orientationchange", () => {
    setTimeout(() => {
        if (chart) chart.resize();
        if (bedrijvenChart) bedrijvenChart.resize();
    }, 200); 
});

const TAB_FADE_MS = 90;

const wisselView = maakTabWisselaar(
    pasViewToe,
    () => document.querySelector(".content").classList.add("tabWisselt"),
    TAB_FADE_MS,
);

// Tabbladen die een opgeslagen code nodig hebben (verborgen bij 'niet opslaan').
const VIEWS_MET_CODE = ["instellingen", "instellingen-bijnamen"];

const TOON_PER_VIEW = {
    "portfolio": toonPortfolio,
    "rendement": toonRendement,
    "peraandeel": () => toonPerAandeel(document.getElementById("aandeelSelect").value),
    "peraandeelaankoop": () => toonPerAandeelAankoop(document.getElementById("aandeelSelect").value),
    "verdeling": toonVerdeling,
    "land": toonLand,
    "sector": toonSector,
    "valuta": toonValuta,
    "bedrijven": toonBedrijven,
    "etfoverlap": renderEtfOverlapTabel,
    "statistieken": toonStatistieken,
    "transacties": toonTransacties,
    "prognose": toonPrognose,
    "dividend": toonDividend,
    "instellingen": () => {},  // leeg, want de instellingen zijn altijd hetzelfde voor elk portfolio
    "instellingen-bijnamen": toonInstellingen,
    "instellingen-ticker": toonInstellingenTicker,
    "instellingen-diagnostiek": toonDiagnostiek,
};

function toegestaneViews() {
    return Object.keys(TOON_PER_VIEW).filter(v => huidigeData.code || !VIEWS_MET_CODE.includes(v));
}

function viewUitUrl() {
    return viewUitHash(location.hash, toegestaneViews());
}

let actieveView = null;

function maakHoofdTabKnop(groep) {
    const knop = document.createElement("button");
    knop.type = "button";
    knop.className = "hoofdTab";
    knop.dataset.groep = groep.id;
    const icoon = document.createElement("span");
    icoon.className = "hoofdTabIcoon";
    icoon.textContent = groep.icoon;
    const label = document.createElement("span");
    label.textContent = groep.label;
    knop.append(icoon, label);
    return knop;
}

// Hoofdtabs en tandwiel komen uit MENU_GROEPEN; het tandwiel staat al in de HTML.
MENU_GROEPEN.filter(g => !g.tandwiel).forEach(g => document.getElementById("hoofdTabs").appendChild(maakHoofdTabKnop(g)));

document.querySelectorAll("[data-groep]").forEach(knop => {
    knop.addEventListener("click", () => gaNaarView(eersteView(knop.dataset.groep, toegestaneViews())));
});

function ververMenu(view) {
    const toegestaan = toegestaneViews();
    const groepId = groepVanView(view);
    const zichtbaar = zichtbareGroepen(toegestaan).map(g => g.id);
    document.querySelectorAll("[data-groep]").forEach(knop => {
        knop.hidden = !zichtbaar.includes(knop.dataset.groep);
        knop.classList.toggle("actief", knop.dataset.groep === groepId);
    });

    const groep = MENU_GROEPEN.find(g => g.id === groepId);
    const chips = (groep ? zichtbareViews(groep, toegestaan) : []).map(v => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "subTab" + (v.view === view ? " actief" : "");
        chip.dataset.view = v.view;
        chip.textContent = v.label;
        chip.addEventListener("click", () => gaNaarView(v.view));
        return chip;
    });
    document.getElementById("subTabs").replaceChildren(...chips);
}

// Het tabblad staat in de URL-hash; de hashchange-listener wisselt de view.
function gaNaarView(view) {
    if (viewUitUrl() === view) {
        wisselView(view); // dezelfde hash geeft geen hashchange
    } else {
        location.hash = view; // hashchange-listener wisselt de view
    }
}

// Verhuist de ene gedeelde grafiek naar het data-grafiek-plek van het tabblad; zonder plek is hij verborgen.
function plaatsGrafiek(view) {
    const wrapper = document.getElementById("chartWrapper");
    const plek = document.querySelector(`#tab-${view} [data-grafiek-plek]`);
    wrapper.style.display = plek ? "block" : "none";
    const zoomKnop = document.getElementById("resetZoomBtn");
    zoomKnop.style.display = plek && plek.hasAttribute("data-zoombaar") ? "block" : "none";
    if (plek) plek.appendChild(wrapper);
}

function pasViewToe(view) {
    const content = document.querySelector(".content");

    actieveView = view;
    ververMenu(view);

    // data-views / data-vereist-code bepalen de zichtbaarheid; data-verberg-buiten: de toon-functie zet het zelf aan als dat nodig is.
    const heeftCode = Boolean(huidigeData.code);
    document.querySelectorAll("[data-views], [data-vereist-code]").forEach(el => {
        el.style.display = elementZichtbaar(el.dataset.views, el.dataset.vereistCode, view, heeftCode) ? "block" : "none";
    });
    document.querySelectorAll("[data-verberg-buiten]").forEach(el => {
        if (!viewInLijst(el.dataset.verbergBuiten, view)) el.style.display = "none";
    });

    plaatsGrafiek(view);

    TOON_PER_VIEW[view]?.();

    content.classList.remove("tabWisselt");
}

// Wist per tab-bestand de toestand van de vorige portfolio (zie CLAUDE.md: Frontend).
const RESET_PER_TAB = [resetDiagnostiek, resetPrognose, resetRendement, resetPerAandeelAankoop, resetTransacties, resetEtfOverlap];

function toonDashboard(data) {
    huidigeData = data;
    RESET_PER_TAB.forEach(reset => reset());
    voegDiagnostiekToe(data);
    document.getElementById("dashboardSection").style.display = "flex";
    document.getElementById("dashCode").textContent = data.code || "";

    toonTickerWaarschuwingBanner(data.ticker_waarschuwingen || []);

    if (!data.chart_data) {
        document.getElementById("geenData").style.display = "block";
        return;
    }

    ververAandeelSelect();
    ververEigenAandeelSelect();
    wisselView(viewUitUrl());

    // Bij 'niet opslaan' zit de verrijking al in het antwoord.
    if (data.verdeling !== undefined) {
        verrijkingStatus = "klaar";
    } else if (data.code) {
        laadVerrijking(data.code);
    } else {
        verrijkingStatus = "klaar";
    }
}

function actieveViewNaam() {
    return actieveView;
}

// Anders gebeurt het tekenen vanzelf bij de volgende tabwissel.
function herTekenVerrijkingTabbladIndienActief() {
    const view = actieveViewNaam();
    if (view === "verdeling") toonVerdeling();
    else if (view === "land") toonLand();
    else if (view === "sector") toonSector();
    else if (view === "valuta") toonValuta();
    else if (view === "bedrijven") toonBedrijven();
    else if (view === "etfoverlap") renderEtfOverlapTabel();
}

// true = de aanroeper moet stoppen (nog aan het laden of mislukt).
function toonVerrijkingWachtstatusIndienNodig() {
    const laadt = document.getElementById("verrijkingLaadt");
    const fout = document.getElementById("verrijkingFout");
    laadt.style.display = verrijkingStatus === "laden" ? "block" : "none";
    fout.style.display = verrijkingStatus === "fout" ? "block" : "none";
    return verrijkingStatus === "laden" || verrijkingStatus === "fout";
}

async function laadVerrijking(code) {
    verrijkingStatus = "laden";
    herTekenVerrijkingTabbladIndienActief();
    try {
        const res = await fetchMetTimeout(`/api/portfolio/${code}/verrijking`);
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || "Verrijking ophalen mislukt.");
        }
        Object.assign(huidigeData, data);
        voegDiagnostiekToe(data);
        verrijkingStatus = "klaar";
    } catch (err) {
        console.error("[verrijking] ophalen mislukt:", err.message);
        verrijkingStatus = "fout";
    }
    herTekenVerrijkingTabbladIndienActief();
}

document.getElementById("verrijkingOpnieuwBtn").addEventListener("click", () => {
    if (huidigeData && huidigeData.code) {
        laadVerrijking(huidigeData.code);
    }
});

// Wijst door naar Ticker-zekerheid; kiest zelf nooit een alternatieve ticker.
function toonTickerWaarschuwingBanner(waarschuwingen) {
    const banner = document.getElementById("tickerWaarschuwingBanner");
    if (!waarschuwingen || waarschuwingen.length === 0) {
        banner.style.display = "none";
        return;
    }

    const namen = waarschuwingen.map(w => w.naam || w.ticker).join(", ");
    const tekst = waarschuwingen.length === 1
        ? `⚠️ Bij 1 positie (${namen}) wijkt de koers meer dan verwacht af van Yahoo Finance — controleer het Ticker-zekerheid-tabblad.`
        : `⚠️ Bij ${waarschuwingen.length} posities (${namen}) wijkt de koers meer dan verwacht af van Yahoo Finance — controleer het Ticker-zekerheid-tabblad.`;
    document.getElementById("tickerWaarschuwingTekst").textContent = tekst;
    banner.style.display = "block";
}

// Ook de terug- en vooruit-knop van de browser komen hier langs.
window.addEventListener("hashchange", () => {
    if (huidigeData && huidigeData.chart_data) wisselView(viewUitUrl());
});

document.getElementById("resetZoomBtn").addEventListener("click", () => {
    if (chart) chart.resetZoom();
});

document.getElementById("tickerWaarschuwingKnop").addEventListener("click", () => {
    gaNaarView("instellingen-ticker");
});

// De code komt uit Flask (data-code); leeg op /analyse ('niet opslaan').
const paginaCode = document.body.dataset.code || "";

async function haalPortfolioOp() {
    document.getElementById("laadFout").hidden = true;
    toonLaadOverlay("Ophalen...");
    try {
        const res = await fetchMetTimeout(`/api/portfolio/${paginaCode}`);
        if (res.status === 404) {
            location.replace(startPadMetMelding(MELDING_ONBEKENDE_CODE));
            return;
        }
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Ophalen mislukt.");
        toonDashboard(data);
    } catch (err) {
        console.error("[portfolio] ophalen mislukt:", err.message);
        document.getElementById("laadFout").hidden = false;
    } finally {
        verbergLaadOverlay();
    }
}

document.getElementById("laadOpnieuwBtn").addEventListener("click", haalPortfolioOp);

// Direct na een upload of 'ophalen met code' ligt het antwoord al klaar (eenmalig);
// bij een refresh of bookmark haalt de pagina zelf op.
function startPortfolioPagina() {
    const overdracht = haalOverdracht(sessieOpslag(), paginaCode);
    if (overdracht) {
        toonDashboard(overdracht);
    } else if (paginaCode) {
        haalPortfolioOp();
    } else {
        location.replace(START_PAD);
    }
}

startPortfolioPagina();

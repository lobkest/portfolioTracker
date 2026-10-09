// Tabblad Instellingen > Bijnamen: per positie de Excel-, Yahoo- of korte naam kiezen, of dat in één keer voor alle posities.

// Uit GET /korte-namen: {ticker: {long_name, voorstel}} (null zonder Yahoo-naam).
let yahooNamen = {};
let yahooNamenStatus = "leeg"; // leeg | laden | klaar | fout
let yahooNamenFout = "";

const OPGESLAGEN_ZICHTBAAR_MS = 2000;
let opgeslagenTicker = null;
let opgeslagenTimer = null;

function resetKorteNamen() {
    yahooNamen = {};
    yahooNamenStatus = "leeg";
    yahooNamenFout = "";
    opgeslagenTicker = null;
    clearTimeout(opgeslagenTimer);
}

// Gedeeld met ETF-overlap: één request voor beide tabbladen.
async function laadYahooNamen() {
    if (yahooNamenStatus === "laden") return;
    const code = huidigeData.code;
    yahooNamenStatus = "laden";
    try {
        const res = await fetchMetTimeout(`/api/portfolio/${code}/korte-namen`);
        const data = await res.json();
        if (huidigeData.code !== code) return;
        if (!res.ok) {
            throw new Error(data.error || "Namen ophalen mislukt.");
        }
        yahooNamen = {};
        data.namen.forEach(n => { yahooNamen[n.ticker] = { long_name: n.long_name, voorstel: n.voorstel }; });
        yahooNamenStatus = "klaar";
    } catch (e) {
        if (huidigeData.code !== code) return;
        yahooNamenStatus = "fout";
        yahooNamenFout = e.message === "TIMEOUT" ? "Yahoo reageerde niet op tijd." : e.message;
    }
    const view = actieveViewNaam();
    if (view === "instellingen-bijnamen") renderBijnamen();
    else if (view === "etfoverlap") renderEtfOverlapTabel();
}

// Zonder succesTekst geen melding bovenaan (één positie krijgt een vinkje naast het veld); geeft true bij succes.
async function pasBijnamenToe(namen, laadTekst, succesTekst) {
    toonLaadOverlay(laadTekst);
    try {
        const res = await fetch(`/api/portfolio/${huidigeData.code}/bijnamen`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ namen })
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || "Opslaan mislukt.");
        }
        // Object.assign (zie CLAUDE.md: Frontend); daarna de verrijking opnieuw, want daar staan ook namen in.
        Object.assign(huidigeData, data);
        ververAandeelSelect();
        toonInstellingen();
        toonTickerWaarschuwingBanner(data.ticker_waarschuwingen || []);
        laadVerrijking(huidigeData.code);
        if (succesTekst) {
            const msg = document.getElementById("instellingenMsg");
            msg.className = "melding positief";
            msg.textContent = succesTekst;
            msg.style.display = "block";
        }
        return true;
    } catch (e) {
        const msg = document.getElementById("instellingenMsg");
        msg.className = "melding foutTekst";
        msg.textContent = e.message || "Bijnaam opslaan mislukt. Probeer het opnieuw.";
        msg.style.display = "block";
        return false;
    } finally {
        verbergLaadOverlay();
    }
}

function toonInstellingen() {
    document.getElementById("instellingenMsg").style.display = "none";
    if (yahooNamenStatus === "leeg" || yahooNamenStatus === "fout") {
        laadYahooNamen();
    }
    renderBijnamen();
}

// Tickers zonder naam in de gekozen bron vallen weg en blijven dus ongewijzigd.
function verzamelNamen(naamVan) {
    const namen = {};
    huidigeData.tickers.forEach(t => {
        const naam = naamVan(t);
        if (naam) namen[t.ticker] = naam;
    });
    return namen;
}

const NAAM_BRONNEN = [
    { label: "Excel", titel: "Excel-naam", alles: "Alle Excel-namen gebruiken", naamVan: t => t.echte_naam, vereistYahoo: false },
    { label: "Yahoo", titel: "Yahoo-naam", alles: "Alle Yahoo-namen gebruiken", naamVan: t => (yahooNamen[t.ticker] || {}).long_name, vereistYahoo: true },
    { label: "Kort", titel: "Korte naam", alles: "Alle korte namen gebruiken", naamVan: t => (yahooNamen[t.ticker] || {}).voorstel, vereistYahoo: true },
];

function maakYahooStatus() {
    const status = document.createElement("span");
    status.className = "kleinLabel bijnaamStatus";
    if (yahooNamenStatus === "fout") {
        status.className = "melding foutTekst bijnaamStatus";
        status.textContent = `Yahoo-namen ophalen mislukt: ${yahooNamenFout}`;
    } else if (yahooNamenStatus !== "klaar") {
        status.textContent = "Namen ophalen bij Yahoo...";
    }
    return status;
}

function maakBronKnop(label, uitleg) {
    const knop = document.createElement("button");
    knop.type = "button";
    knop.className = "bijnaamChip";
    knop.textContent = label;
    knop.title = uitleg;
    knop.setAttribute("aria-label", uitleg);
    return knop;
}

function maakAllesBalk() {
    const balk = document.createElement("div");
    balk.className = "bijnaamAlles";

    const kop = document.createElement("span");
    kop.className = "kleinLabel";
    kop.textContent = "Alles:";
    balk.appendChild(kop);

    NAAM_BRONNEN.forEach(bron => {
        const namen = verzamelNamen(bron.naamVan);
        const knop = maakBronKnop(bron.label, bron.alles);
        knop.disabled = Object.keys(namen).length === 0;
        knop.onclick = () => pasBijnamenToe(namen, "Namen toepassen...", "Namen toegepast.");
        balk.appendChild(knop);
    });

    balk.appendChild(maakYahooStatus());
    return balk;
}

async function slaBijnaamOp(ticker, naam) {
    const gelukt = await pasBijnamenToe({ [ticker]: naam }, "Aanpassen...", null);
    if (!gelukt) return;
    opgeslagenTicker = ticker;
    clearTimeout(opgeslagenTimer);
    opgeslagenTimer = setTimeout(() => {
        opgeslagenTicker = null;
        if (actieveViewNaam() === "instellingen-bijnamen") renderBijnamen();
    }, OPGESLAGEN_ZICHTBAAR_MS);
    renderBijnamen();
}

function maakNaamChips(t) {
    const chips = document.createElement("div");
    chips.className = "bijnaamChips";
    const yahooWacht = yahooNamenStatus !== "klaar";
    const bronNamen = NAAM_BRONNEN.map(bron => (bron.vereistYahoo && yahooWacht ? null : bron.naamVan(t)));
    const actief = actieveNaamBron(bronNamen, t.naam);

    NAAM_BRONNEN.forEach((bron, i) => {
        const naam = bronNamen[i];
        const uitleg = naam ? `${bron.titel}: ${naam}` : (bron.vereistYahoo && yahooWacht ? `${bron.titel}: nog niet opgehaald` : `Geen ${bron.titel.toLowerCase()}`);
        const chip = maakBronKnop(bron.label, uitleg);
        chip.disabled = !naam;
        if (i === actief) chip.classList.add("actief");
        chip.setAttribute("aria-pressed", i === actief ? "true" : "false");
        chip.onclick = () => {
            if (naam !== t.naam) slaBijnaamOp(t.ticker, naam);
        };
        chips.appendChild(chip);
    });
    return chips;
}

function maakBijnaamInvoer(t) {
    const regel = document.createElement("div");
    regel.className = "bijnaamInvoer";

    const input = document.createElement("input");
    input.type = "text";
    input.value = t.naam;
    input.setAttribute("aria-label", `Bijnaam voor ${t.ticker}`);
    // Enter slaat op en de re-render kan daarna nog een blur geven: niet dubbel opslaan.
    let bezig = false;
    const opslaan = () => {
        if (bezig || !bijnaamGewijzigd(input.value, t.naam)) return;
        bezig = true;
        slaBijnaamOp(t.ticker, input.value.trim()).finally(() => { bezig = false; });
    };
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            opslaan();
        }
    });
    input.addEventListener("blur", opslaan);
    regel.appendChild(input);

    if (opgeslagenTicker === t.ticker) {
        const ok = document.createElement("span");
        ok.className = "kleinLabel positief bijnaamOk";
        ok.textContent = "✓ opgeslagen";
        regel.appendChild(ok);
    }
    return regel;
}

function renderBijnamen() {
    const sectie = document.getElementById("instellingenSectie");
    sectie.innerHTML = "";
    sectie.appendChild(maakAllesBalk());

    const lijst = document.createElement("div");
    lijst.className = "bijnaamLijst";
    huidigeData.tickers.forEach(t => {
        const rij = document.createElement("div");
        rij.className = "bijnaamRij";

        const kop = document.createElement("div");
        kop.className = "bijnaamKop";
        const naam = document.createElement("span");
        naam.className = "bijnaamNaam";
        naam.textContent = t.naam;
        naam.title = t.naam;
        const ticker = document.createElement("span");
        ticker.className = "kleinLabel";
        ticker.textContent = t.ticker;
        kop.append(naam, ticker);

        rij.append(kop, maakNaamChips(t), maakBijnaamInvoer(t));
        lijst.appendChild(rij);
    });
    sectie.appendChild(lijst);
}

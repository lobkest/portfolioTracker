// Tabblad Instellingen > Bijnamen: per positie de Excel-, Yahoo- of korte naam kiezen, of dat in één keer voor alle posities.

// Uit GET /korte-namen: {ticker: {long_name, voorstel}} (null zonder Yahoo-naam).
let yahooNamen = {};
let yahooNamenStatus = "leeg"; // leeg | laden | klaar | fout
let yahooNamenFout = "";

function resetKorteNamen() {
    yahooNamen = {};
    yahooNamenStatus = "leeg";
    yahooNamenFout = "";
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
        const msg = document.getElementById("instellingenMsg");
        msg.className = "melding positief";
        msg.textContent = succesTekst;
        msg.style.display = "block";
    } catch (e) {
        const msg = document.getElementById("instellingenMsg");
        msg.className = "melding foutTekst";
        msg.textContent = e.message || "Bijnaam opslaan mislukt. Probeer het opnieuw.";
        msg.style.display = "block";
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
    { label: "Excel naam", alles: "Alle Excel namen", naamVan: t => t.echte_naam, vereistYahoo: false },
    { label: "Yahoo naam", alles: "Alle Yahoo namen", naamVan: t => (yahooNamen[t.ticker] || {}).long_name, vereistYahoo: true },
    { label: "Korte naam", alles: "Alle korte namen", naamVan: t => (yahooNamen[t.ticker] || {}).voorstel, vereistYahoo: true },
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

function maakAllesBalk() {
    const balk = document.createElement("div");
    balk.className = "bijnaamRij";

    const kop = document.createElement("span");
    kop.className = "kleinLabel";
    kop.textContent = "Alles:";
    balk.appendChild(kop);

    NAAM_BRONNEN.forEach(bron => {
        const namen = verzamelNamen(bron.naamVan);
        const knop = document.createElement("button");
        knop.className = "bijnaamKnop";
        knop.textContent = bron.alles;
        knop.disabled = Object.keys(namen).length === 0;
        knop.onclick = () => pasBijnamenToe(namen, "Namen toepassen...", "Namen toegepast.");
        balk.appendChild(knop);
    });

    balk.appendChild(maakYahooStatus());
    return balk;
}

function maakNaamKnop(bron, t) {
    const naam = bron.naamVan(t);
    const knop = document.createElement("button");
    knop.className = "bijnaamKnop";
    knop.textContent = naam || "-";
    knop.disabled = !naam || naam === t.naam;
    knop.onclick = () => pasBijnamenToe({ [t.ticker]: naam }, "Aanpassen...", "Bijnaam opgeslagen.");
    return knop;
}

function renderBijnamen() {
    const sectie = document.getElementById("instellingenSectie");
    sectie.innerHTML = "";
    sectie.appendChild(maakAllesBalk());

    huidigeData.tickers.forEach(t => {
        const rij = document.createElement("div");
        rij.className = "bijnaamRij";

        const kop = document.createElement("div");
        kop.textContent = t.ticker;
        kop.className = "bijnaamKop";
        rij.appendChild(kop);

        NAAM_BRONNEN.forEach(bron => {
            const regel = document.createElement("div");
            regel.className = "bijnaamRegel";
            const label = document.createElement("span");
            label.className = "kleinLabel";
            label.textContent = `${bron.label}:`;
            regel.appendChild(label);
            regel.appendChild(bron.vereistYahoo && yahooNamenStatus !== "klaar" ? document.createTextNode("...") : maakNaamKnop(bron, t));
            rij.appendChild(regel);
        });

        const input = document.createElement("input");
        input.type = "text";
        input.value = t.naam;
        input.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                const naam = input.value.trim();
                if (naam && naam !== t.naam) {
                    pasBijnamenToe({ [t.ticker]: naam }, "Aanpassen...", "Bijnaam opgeslagen.");
                }
            }
        });
        rij.appendChild(input);
        sectie.appendChild(rij);
    });
}

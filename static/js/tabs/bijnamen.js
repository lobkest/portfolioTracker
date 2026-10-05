// Tabblad Instellingen > Bijnamen: per positie een eigen naam opslaan of terugzetten, of alle korte namen van Yahoo toepassen.

// Voorstellen uit GET /korte-namen: {ticker: korte naam of null}.
let korteNamen = {};
let korteNamenStatus = "leeg"; // leeg | laden | klaar | fout
let korteNamenFout = "";

function resetKorteNamen() {
    korteNamen = {};
    korteNamenStatus = "leeg";
    korteNamenFout = "";
}

async function laadKorteNamen() {
    const code = huidigeData.code;
    korteNamenStatus = "laden";
    try {
        const res = await fetchMetTimeout(`/api/portfolio/${code}/korte-namen`);
        const data = await res.json();
        if (huidigeData.code !== code) return;
        if (!res.ok) {
            throw new Error(data.error || "Namen ophalen mislukt.");
        }
        korteNamen = {};
        data.namen.forEach(n => { korteNamen[n.ticker] = n.voorstel; });
        korteNamenStatus = "klaar";
    } catch (e) {
        if (huidigeData.code !== code) return;
        korteNamenStatus = "fout";
        korteNamenFout = e.message === "TIMEOUT" ? "Yahoo reageerde niet op tijd." : e.message;
    }
    renderBijnamen();
}

async function pasKorteNamenToe() {
    toonLaadOverlay("Korte namen toepassen...");
    try {
        const res = await fetchMetTimeout(`/api/portfolio/${huidigeData.code}/korte-namen`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || "Toepassen mislukt.");
        }
        // Object.assign (zie CLAUDE.md: Frontend); daarna de verrijking opnieuw, want daar staan ook namen in.
        Object.assign(huidigeData, data);
        ververAandeelSelect();
        toonInstellingen();
        toonTickerWaarschuwingBanner(data.ticker_waarschuwingen || []);
        laadVerrijking(huidigeData.code);
        const msg = document.getElementById("instellingenMsg");
        msg.className = "melding positief";
        msg.textContent = "Korte namen toegepast.";
        msg.style.display = "block";
    } catch (e) {
        const msg = document.getElementById("instellingenMsg");
        msg.className = "melding foutTekst";
        msg.textContent = e.message === "TIMEOUT" ? "Yahoo reageerde niet op tijd." : (e.message || "Korte namen toepassen mislukt.");
        msg.style.display = "block";
    } finally {
        verbergLaadOverlay();
    }
}

async function slaBijnaamOp(ticker, bijnaam) {
    toonLaadOverlay("Aanpassen...");
    try {
        const res = await fetch(`/api/portfolio/${huidigeData.code}/bijnaam`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ticker, bijnaam })
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
        msg.textContent = "Bijnaam opgeslagen.";
        msg.style.display = "block";
    } catch (e) {
        const msg = document.getElementById("instellingenMsg");
        msg.className = "melding foutTekst";
        msg.textContent = "Bijnaam opslaan mislukt. Probeer het opnieuw.";
        msg.style.display = "block";
    } finally {
        verbergLaadOverlay();
    }
}

async function resetBijnaam(ticker) {
    toonLaadOverlay("Aanpassen...");
    try {
        const res = await fetch(`/api/portfolio/${huidigeData.code}/reset-bijnaam`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ticker })
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || "Reset mislukt.");
        }
        // Zie slaBijnaamOp().
        Object.assign(huidigeData, data);
        ververAandeelSelect();
        toonInstellingen();
        toonTickerWaarschuwingBanner(data.ticker_waarschuwingen || []);
        laadVerrijking(huidigeData.code);
    } catch (e) {
        const msg = document.getElementById("instellingenMsg");
        msg.className = "melding foutTekst";
        msg.textContent = "Bijnaam resetten mislukt. Probeer het opnieuw.";
        msg.style.display = "block";
    } finally {
        verbergLaadOverlay();
    }
}

function toonInstellingen() {
    document.getElementById("instellingenMsg").style.display = "none";
    if (korteNamenStatus === "leeg" || korteNamenStatus === "fout") {
        laadKorteNamen();
    }
    renderBijnamen();
}

function maakKorteNamenBalk() {
    const balk = document.createElement("div");
    balk.className = "bijnaamRij";

    const knop = document.createElement("button");
    knop.textContent = "Korte namen toepassen";
    knop.disabled = korteNamenStatus !== "klaar" || !Object.values(korteNamen).some(Boolean);
    knop.onclick = pasKorteNamenToe;

    const status = document.createElement("span");
    status.className = "kleinLabel bijnaamStatus";
    if (korteNamenStatus === "fout") {
        status.className = "melding foutTekst bijnaamStatus";
        status.textContent = `Korte namen ophalen mislukt: ${korteNamenFout}`;
    } else if (korteNamenStatus !== "klaar") {
        status.textContent = "Korte namen ophalen bij Yahoo...";
    }

    balk.appendChild(knop);
    balk.appendChild(status);
    return balk;
}

function maakVoorstelRegel(ticker, huidigeNaam) {
    if (korteNamenStatus !== "klaar") return null;
    const regel = document.createElement("div");
    regel.className = "kleinLabel";
    const voorstel = korteNamen[ticker];
    if (!voorstel) {
        regel.textContent = "Korte naam: geen Yahoo-naam, blijft ongewijzigd";
    } else if (voorstel === huidigeNaam) {
        regel.textContent = `Korte naam: ${voorstel} (staat al zo)`;
    } else {
        regel.textContent = `Korte naam: ${voorstel}`;
    }
    return regel;
}

function renderBijnamen() {
    const sectie = document.getElementById("instellingenSectie");
    sectie.innerHTML = "";
    sectie.appendChild(maakKorteNamenBalk());

    huidigeData.tickers.forEach(t => {
        const rij = document.createElement("div");
        rij.className = "bijnaamRij";

        const label = document.createElement("div");
        label.textContent = `${t.ticker} (origineel: ${t.echte_naam})`;
        label.className = "kleinLabel";

        const voorstelRegel = maakVoorstelRegel(t.ticker, t.naam);

        const input = document.createElement("input");
        input.type = "text";
        input.value = t.naam;

        const opslaanBtn = document.createElement("button");
        opslaanBtn.textContent = "Opslaan";
        opslaanBtn.onclick = () => slaBijnaamOp(t.ticker, input.value.trim());

        input.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                opslaanBtn.click();
            }
        });

        const resetBtn = document.createElement("button");
        resetBtn.textContent = "Reset";
        resetBtn.className = "bijnaamReset";
        resetBtn.onclick = () => resetBijnaam(t.ticker);

        rij.appendChild(label);
        if (voorstelRegel) rij.appendChild(voorstelRegel);
        rij.appendChild(input);
        rij.appendChild(opslaanBtn);
        rij.appendChild(resetBtn);
        sectie.appendChild(rij);
    });
}

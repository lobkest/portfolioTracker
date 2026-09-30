// Tabblad Instellingen > Bijnamen: per positie een eigen naam opslaan of terugzetten.

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
        msg.className = "positief";
        msg.textContent = "Bijnaam opgeslagen.";
        msg.style.display = "block";
    } catch (e) {
        const msg = document.getElementById("instellingenMsg");
        msg.className = "foutTekst";
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
        msg.className = "foutTekst";
        msg.textContent = "Bijnaam resetten mislukt. Probeer het opnieuw.";
        msg.style.display = "block";
    } finally {
        verbergLaadOverlay();
    }
}

function toonInstellingen() {
    document.getElementById("instellingenMsg").style.display = "none";
    const sectie = document.getElementById("instellingenSectie");
    sectie.innerHTML = "";

    huidigeData.tickers.forEach(t => {
        const rij = document.createElement("div");
        rij.className = "bijnaamRij";

        const label = document.createElement("div");
        label.textContent = `${t.ticker} (origineel: ${t.echte_naam})`;
        label.className = "kleinLabel";

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
        rij.appendChild(input);
        rij.appendChild(opslaanBtn);
        rij.appendChild(resetBtn);
        sectie.appendChild(rij);
    });
}

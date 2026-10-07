// Tabblad Box 3: alleen tonen. De basis komt van /box3 (of uit 'niet opslaan'), de belasting van /api/box3/bereken.

const BOX3_TOESTANDEN = ["box3Laden", "box3Fout", "box3Inhoud"];
const BOX3_DEBOUNCE_MS = 400;
const BOX3_VELDEN = {
    banktegoeden: "box3Bank",
    spaarrente_pct: "box3Spaarrente",
    overige_bezittingen: "box3Overig",
    schulden: "box3Schulden",
    rendement_ander_vermogen: "box3RendementAnder",
};
const BOX3_VELD_LABELS = {
    banktegoeden: "spaargeld/banktegoeden",
    spaarrente_pct: "gemiddelde spaarrente",
    overige_bezittingen: "overige bezittingen",
    schulden: "schulden",
    rendement_ander_vermogen: "overig rendement op ander vermogen",
};
const BOX3_STELSEL_LABELS = {
    huidig: "Huidig stelsel",
    aanwas: "A: vermogensaanwas",
    vermogenswinst: "B: vermogenswinst",
};

let box3Basis = null;
// Voor het aan/uit zetten van de grijze staaf zonder nieuwe aanvraag.
let box3LaatsteBerekening = null;
let box3Timer = null;
// Alleen het antwoord op de laatste aanvraag tekenen (typen geeft meerdere aanvragen kort na elkaar).
let box3Volgnummer = 0;

function resetBox3() {
    box3Basis = null;
    box3LaatsteBerekening = null;
    clearTimeout(box3Timer);
    box3Volgnummer++;
}

function box3Euro(bedrag) {
    return bedrag === null || bedrag === undefined ? "—" : formatteerEuro(bedrag);
}

function box3Badge(tekst, titel) {
    const badge = document.createElement("span");
    badge.className = "badge badgeBox3";
    badge.textContent = tekst;
    if (titel) badge.title = titel;
    return badge;
}

function maakBox3JaarCel(rij, metHuidig) {
    const td = maakCel(String(rij.jaar));
    if (metHuidig && rij.lopend && rij.huidig.berekend) {
        td.appendChild(box3Badge("tegenbewijs tot vandaag",
            "Werkelijk rendement tot vandaag, forfaitair over het hele jaar; het tegenbewijs valt hierdoor te gunstig uit."));
    } else if (rij.lopend) {
        td.appendChild(box3Badge("tot vandaag", "Het lopende jaar, tot de laatste koersdatum"));
    }
    if (metHuidig && rij.huidig.geschat) {
        td.appendChild(box3Badge("geschat", "Percentages van het laatst bekende jaar"));
    } else if (metHuidig && rij.huidig.berekend && !rij.huidig.definitief) {
        td.appendChild(box3Badge("voorlopig", "(Een deel van) de percentages is nog niet definitief"));
    }
    if (rij.kosten_onvolledig) {
        td.appendChild(box3Badge("kosten onvolledig", "Bij sommige transacties ontbreken de kosten"));
    }
    return td;
}

function box3EuroKolom(label, waarde) {
    return { label, waarde, renderTd: r => maakCel(box3Euro(waarde(r))) };
}

function box3DividendKolom(waarde) {
    return {
        label: "Dividend (bruto)",
        waarde,
        renderTd: r => maakCel(waarde(r) === null ? "niet meegenomen" : box3Euro(waarde(r))),
    };
}

function maakBox3HuidigTabel(jaren) {
    // Jaren vóór 2023: alleen het jaartal en de melding in de laatste kolom.
    const h = (veld) => (r => (r.huidig.berekend ? r.huidig[veld] : null));
    const kolommen = [
        { label: "Jaar", waarde: r => r.jaar, renderTd: r => maakBox3JaarCel(r, true) },
        box3EuroKolom("Overige bezittingen 1-1", h("overig")),
        box3EuroKolom("Banktegoeden", h("bank")),
        box3EuroKolom("Schulden boven drempel", h("schulden_boven_drempel")),
        box3EuroKolom("Forfaitair rendement", h("forfaitair_rendement")),
        box3EuroKolom("Grondslag na vrijstelling", h("grondslag_na_vrijstelling")),
        box3EuroKolom("Belasting forfaitair", h("belasting_forfaitair")),
        box3EuroKolom("Koersresultaat", h("koersresultaat")),
        box3EuroKolom("Kosten (terug)", h("kosten")),
        box3DividendKolom(r => (r.huidig.berekend ? r.huidig.dividend_bruto : undefined)),
        box3EuroKolom("Spaarrente", h("spaarrente")),
        box3EuroKolom("Overig ander vermogen", h("rendement_ander_vermogen")),
        box3EuroKolom("Werkelijk rendement", h("werkelijk_rendement")),
        box3EuroKolom("Belasting tegenbewijs", h("belasting_tegenbewijs")),
        {
            label: "Belasting",
            waarde: h("belasting"),
            renderTd: r => {
                if (!r.huidig.berekend) return maakCel("ander stelsel, niet berekend");
                const geldt = r.huidig.geldt === "tegenbewijs" ? "tegenbewijs" : "forfaitair";
                const td = maakCel(`${box3Euro(r.huidig.belasting)} (${geldt})`);
                td.className = "vet";
                return td;
            },
        },
    ];
    return maakSorteerbareTabel(kolommen, jaren, { legeTekst: "Geen jaren om te berekenen." });
}

function maakBox3AanwasTabel(jaren, basisPerJaar) {
    const a = (veld) => (r => r.aanwas[veld]);
    const b = (veld) => (r => basisPerJaar[r.jaar][veld]);
    const kolommen = [
        { label: "Jaar", waarde: r => r.jaar, renderTd: r => maakBox3JaarCel(r, false) },
        box3EuroKolom("Waarde begin", b("waarde_begin")),
        box3EuroKolom("Waarde eind", b("waarde_eind")),
        box3EuroKolom("Netto inleg", b("netto_inleg")),
        box3EuroKolom("Koersresultaat", a("koersresultaat")),
        box3DividendKolom(a("dividend_bruto")),
        box3EuroKolom("Spaarrente", a("spaarrente")),
        box3EuroKolom("Overig ander vermogen", a("rendement_ander_vermogen")),
        box3EuroKolom("Rendement", a("rendement")),
        box3EuroKolom("Verlies erbij", a("verlies_erbij")),
        box3EuroKolom("Verrekend verlies", a("verrekend_verlies")),
        box3EuroKolom("Heffingsvrij", a("heffingsvrij")),
        box3EuroKolom("Belastbaar", a("belastbaar")),
        box3EuroKolom("Belasting", a("belasting")),
    ];
    return maakSorteerbareTabel(kolommen, jaren, { legeTekst: "Geen jaren om te berekenen." });
}

function maakBox3VermogenswinstTabel(jaren) {
    const w = (veld) => (r => r.vermogenswinst[veld]);
    const kolommen = [
        { label: "Jaar", waarde: r => r.jaar, renderTd: r => maakBox3JaarCel(r, false) },
        box3EuroKolom("Gerealiseerd", w("gerealiseerd")),
        box3DividendKolom(w("dividend_bruto")),
        box3EuroKolom("Spaarrente", w("spaarrente")),
        box3EuroKolom("Overig ander vermogen", w("rendement_ander_vermogen")),
        box3EuroKolom("Rendement", w("rendement")),
        box3EuroKolom("Verlies erbij", w("verlies_erbij")),
        box3EuroKolom("Verrekend verlies", w("verrekend_verlies")),
        box3EuroKolom("Heffingsvrij", w("heffingsvrij")),
        box3EuroKolom("Belastbaar", w("belastbaar")),
        box3EuroKolom("Belasting", w("belasting")),
    ];
    return maakSorteerbareTabel(kolommen, jaren, { legeTekst: "Geen jaren om te berekenen." });
}

function maakBox3VerkopenTabel(verkopen) {
    const kolommen = [
        { label: "Datum", waarde: r => new Date(r.datum).getTime(), renderTd: r => maakCel(formatDatum(r.datum)) },
        { label: "Positie", renderTd: r => maakCel(r.bijnaam || r.ticker) },
        { label: "Aantal", waarde: r => r.aantal, renderTd: r => maakCel(r.aantal.toLocaleString("nl-NL")) },
        box3EuroKolom("Opbrengst", r => r.opbrengst),
        box3EuroKolom("Kostenbasis", r => r.kostenbasis),
        {
            label: "Winst",
            waarde: r => r.winst,
            renderTd: r => {
                const td = maakCel(box3Euro(r.winst));
                td.className = klasseVoorRendement(r.winst);
                return td;
            },
        },
    ];
    return maakSorteerbareTabel(kolommen, verkopen, { klasse: "compacteTabel", legeTekst: "Nog niets verkocht." });
}

function renderBox3Tegels(data) {
    const lopend = data.jaren.find(j => j.lopend);
    const rij = document.createElement("div");
    rij.className = "tegelRij tegelRijCompact";
    const bedrag = (x) => (x === null || x === undefined ? "niet berekend" : formatteerEuro(x));
    BOX3_STELSELS.forEach(s => {
        const label = lopend ? `${BOX3_STELSEL_LABELS[s]}, ${lopend.jaar} tot nu` : `${BOX3_STELSEL_LABELS[s]}, lopend jaar`;
        rij.appendChild(maakStatTegel(label, bedrag(data.lopend_jaar[s])));
    });
    BOX3_STELSELS.forEach(s => {
        rij.appendChild(maakStatTegel(`${BOX3_STELSEL_LABELS[s]}, totaal`, bedrag(data.totaal[s])));
    });
    const variant = data.b_alles_verkopen;
    if (variant) {
        const tegel = maakStatTegel("B als je nu alles verkoopt (dit jaar)", bedrag(variant.belasting));
        const extra = document.createElement("div");
        extra.className = "kleineMelding";
        extra.textContent = `${variant.extra_belasting >= 0 ? "+" : ""}${formatteerEuro(variant.extra_belasting)} t.o.v. niet verkopen`;
        tegel.appendChild(extra);
        rij.appendChild(tegel);
    }
    document.getElementById("box3Tegels").replaceChildren(rij);
}

function toonBox3Grafiek(data) {
    const reeksen = box3GrafiekReeksen(data);
    const datasets = BOX3_STELSELS.map((s, i) => ({
        label: BOX3_STELSEL_LABELS[s],
        data: reeksen[s],
        backgroundColor: kleurVoorIndex(i),
    }));
    const variant = data.b_alles_verkopen;
    if (variant && document.getElementById("box3AllesVerkopen").checked) {
        datasets.push({
            label: "B, alles nu verkocht",
            data: box3AllesVerkopenReeks(data),
            backgroundColor: ONBEKEND_GRIJS,
            tooltipLabel: () => `Alles vandaag verkocht: ${formatteerEuro(variant.belasting)} belasting `
                + `(waarvan ${formatteerEuro(variant.extra_belasting)} extra door ongerealiseerde winst)`,
        });
    }
    updateStaafChart(reeksen.labels, datasets);
}

function vulBox3Parameters(data) {
    const pct = `${Math.round(data.tarief_wwr * 100)}%`;
    document.querySelectorAll(".box3Tarief").forEach(el => { el.textContent = pct; });
    document.querySelectorAll(".box3Heffingsvrij").forEach(el => { el.textContent = formatteerEuro(data.heffingsvrij_wwr, 0); });
    document.querySelectorAll(".box3HeffingsvrijPartner").forEach(el => { el.textContent = formatteerEuro(data.heffingsvrij_wwr * 2, 0); });
    document.querySelectorAll(".box3Drempel").forEach(el => { el.textContent = formatteerEuro(data.verliesdrempel, 0); });
    document.getElementById("box3Stand").textContent = formatDatum(data.parameters_stand);
}

function renderBox3AllesVerkopenZin(variant) {
    const zin = document.getElementById("box3AllesVerkopenZin");
    zin.hidden = !variant;
    if (!variant) return;
    const verschil = variant.extra_belasting >= 0
        ? `${formatteerEuro(variant.extra_belasting)} meer`
        : `${formatteerEuro(-variant.extra_belasting)} minder`;
    zin.textContent = `Nog niet verkocht: ${formatteerEuro(variant.latente_winst)} latente winst. Verkoop je vandaag alles, `
        + `dan komt B voor ${variant.jaar} uit op ${formatteerEuro(variant.belasting)} belasting, ${verschil} dan zonder verkopen. `
        + `Daarin zijn de verliesverrekening en ${formatteerEuro(variant.heffingsvrij, 0)} heffingsvrij resultaat al meegenomen.`;
}

function renderBox3(data) {
    box3LaatsteBerekening = data;
    toonAlleen(BOX3_TOESTANDEN, "box3Inhoud");
    vulBox3Parameters(data);
    renderBox3Tegels(data);
    toonBox3Grafiek(data);

    const basisPerJaar = Object.fromEntries(box3Basis.jaren.map(j => [j.jaar, j]));
    document.getElementById("box3HuidigTabel").replaceChildren(maakBox3HuidigTabel(data.jaren));
    document.getElementById("box3AanwasTabel").replaceChildren(maakBox3AanwasTabel(data.jaren, basisPerJaar));
    document.getElementById("box3WinstTabel").replaceChildren(maakBox3VermogenswinstTabel(data.jaren));
    document.getElementById("box3VerkopenTabel").replaceChildren(maakBox3VerkopenTabel(box3Basis.verkopen));
    renderBox3AllesVerkopenZin(data.b_alles_verkopen);
    document.getElementById("box3GeenDividend").hidden = box3Basis.dividend_beschikbaar;
    document.getElementById("box3LopendTegenbewijs").hidden = !data.jaren.some(j => j.lopend && j.huidig.berekend);
}

function leesBox3Velden() {
    const velden = { fiscale_partner: document.getElementById("box3Partner").checked };
    Object.entries(BOX3_VELDEN).forEach(([sleutel, id]) => { velden[sleutel] = document.getElementById(id).value; });
    return velden;
}

function toonBox3InvoerFout(tekst) {
    const el = document.getElementById("box3InvoerFout");
    el.textContent = tekst || "";
    el.hidden = !tekst;
}

async function berekenBox3() {
    if (!box3Basis) return;
    const { invoer, ongeldig } = bouwBox3Invoer(leesBox3Velden());
    document.getElementById("box3RenteWaarschuwing").hidden = ongeldig.length > 0 || !box3RenteWaarschuwing(invoer);
    if (ongeldig.length) {
        toonBox3InvoerFout(`Vul een bedrag in bij ${ongeldig.map(s => BOX3_VELD_LABELS[s]).join(", ")} (bijvoorbeeld 12.500).`);
        return;
    }
    const nummer = ++box3Volgnummer;
    let res, data;
    try {
        res = await fetch("/api/box3/bereken", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ basis: box3Basis, invoer }),
        });
        data = await res.json();
    } catch (e) {
        if (nummer === box3Volgnummer) toonAlleen(BOX3_TOESTANDEN, "box3Fout");
        return;
    }
    if (nummer !== box3Volgnummer || actieveViewNaam() !== "box3") return;
    if (!res.ok) {
        // De invoer blijft zichtbaar, zodat hij te verbeteren is.
        toonAlleen(BOX3_TOESTANDEN, "box3Inhoud");
        toonBox3InvoerFout(data.error || "Berekenen mislukt.");
        return;
    }
    toonBox3InvoerFout(null);
    renderBox3(data);
}

async function haalBox3BasisOp() {
    if (box3Basis) return;
    if (!huidigeData.code) {
        box3Basis = huidigeData.box3_basis || null;
        return;
    }
    const res = await fetch(`/api/portfolio/${huidigeData.code}/box3`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Box 3-gegevens ophalen mislukt.");
    box3Basis = data;
}

async function toonBox3() {
    if (!box3Basis) toonAlleen(BOX3_TOESTANDEN, "box3Laden");
    try {
        await haalBox3BasisOp();
    } catch (e) {
        console.error("[box3] basis ophalen mislukt:", e.message);
    }
    if (!box3Basis) {
        toonAlleen(BOX3_TOESTANDEN, "box3Fout");
        return;
    }
    await berekenBox3();
}

function planBox3Berekening() {
    clearTimeout(box3Timer);
    box3Timer = setTimeout(berekenBox3, BOX3_DEBOUNCE_MS);
}

Object.values(BOX3_VELDEN).forEach(id => document.getElementById(id).addEventListener("input", planBox3Berekening));
document.getElementById("box3Partner").addEventListener("change", planBox3Berekening);
document.getElementById("box3AllesVerkopen").addEventListener("change", () => {
    if (box3LaatsteBerekening) toonBox3Grafiek(box3LaatsteBerekening);
});

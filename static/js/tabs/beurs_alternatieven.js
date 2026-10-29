// DOM voor "Alternatieven op een thuisbeurs" onder Verdeling → Beurs (teksten: beurs_alternatieven.js). Lui: pas bij een klik.

function nieuweAlternatievenToestand() {
    return { open: null, volgnr: 0, vergelijkVolgnr: {} };
}

let alternatievenToestand = nieuweAlternatievenToestand();

function alternatievenPlek() {
    return document.getElementById("aansluitingskostenAlternatieven");
}

function resetBeursAlternatieven() {
    alternatievenToestand = nieuweAlternatievenToestand();
    const plek = alternatievenPlek();
    if (plek) {
        plek.replaceChildren();
        plek.hidden = true;
    }
}

function maakAlternatievenKnop(rij) {
    if (!rij.heeftOpenPositie) return null;
    const knop = document.createElement("button");
    knop.type = "button";
    knop.className = "linkKnop alternatievenKnop";
    knop.textContent = KNOP_TEKST;
    knop.setAttribute("aria-expanded", String(alternatievenToestand.open === rij.code));
    knop.addEventListener("click", () => wisselAlternatieven(rij));
    return knop;
}

function maakAlternatievenRegel(klasse, tekst) {
    const p = document.createElement("p");
    p.className = klasse;
    p.textContent = tekst;
    return p;
}

function maakVergelijkLijst(titel, regels) {
    const kolom = document.createElement("div");
    const kop = document.createElement("strong");
    kop.textContent = titel;
    const ol = document.createElement("ol");
    regels.forEach(tekst => {
        const li = document.createElement("li");
        li.textContent = tekst;
        ol.appendChild(li);
    });
    kolom.append(kop, ol);
    return kolom;
}

function maakVergelijkKaart(kaart, bronTitel) {
    const el = document.createElement("div");
    el.className = "vergelijkKaart";
    const kop = document.createElement("div");
    kop.className = "vergelijkKop";
    const titel = document.createElement("strong");
    titel.textContent = kaart.titel;
    const hoofd = document.createElement("span");
    hoofd.className = "vergelijkOverlap";
    hoofd.textContent = `${kaart.hoofdgetal} ${kaart.hoofdLabel}`;
    kop.append(titel, hoofd);
    const notering = maakAlternatievenRegel("kleineMelding", kaart.notering);
    const details = document.createElement("details");
    details.open = !window.matchMedia("(max-width: 600px)").matches;
    const samenvatting = document.createElement("summary");
    samenvatting.textContent = "Land, sector en holdings";
    const regels = document.createElement("ul");
    kaart.regels.forEach(r => {
        const li = document.createElement("li");
        li.textContent = `${r.label}: ${r.tekst}`;
        regels.appendChild(li);
    });
    details.append(samenvatting, regels);
    const topDetails = document.createElement("details");
    const topSamenvatting = document.createElement("summary");
    topSamenvatting.textContent = "Top 10 naast elkaar";
    const naast = document.createElement("div");
    naast.className = "vergelijkTop10";
    naast.append(maakVergelijkLijst(bronTitel, kaart.top10.bron), maakVergelijkLijst(kaart.titel, kaart.top10.alternatief));
    topDetails.append(topSamenvatting, naast);
    el.append(kop, notering, details, topDetails);
    return el;
}

function renderVergelijkbaarPaneel(antwoord, bronNaam) {
    const paneel = vergelijkbaarPaneel(antwoord, bronNaam);
    const nodes = [];
    if (paneel.melding) nodes.push(maakAlternatievenRegel("", paneel.melding));
    if (paneel.waarschuwing) nodes.push(maakAlternatievenRegel("kleineMelding", paneel.waarschuwing));
    paneel.kaarten.forEach(k => nodes.push(maakVergelijkKaart(k, paneel.bronTop10Titel)));
    if (paneel.telling) nodes.push(maakAlternatievenRegel("kleineMelding", paneel.telling));
    if (paneel.onvolledig) nodes.push(maakAlternatievenRegel("kleineMelding", paneel.onvolledig));
    paneel.vasteRegels.forEach(tekst => nodes.push(maakAlternatievenRegel("kleineMelding", tekst)));
    return nodes;
}

function haalVergelijkbaar(p) {
    if (huidigeData.code) {
        return fetchMetTimeout(`/api/portfolio/${encodeURIComponent(huidigeData.code)}/vergelijkbare-etfs?ticker=${encodeURIComponent(p.ticker)}`);
    }
    return fetchMetTimeout("/api/vergelijkbare-etfs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: p.ticker, isin: p.isin, naam: p.echteNaam || p.naam }),
    });
}

async function laadVergelijkbaar(p, knop, uitkomstPlek) {
    const t = alternatievenToestand;
    t.vergelijkVolgnr[p.ticker] = (t.vergelijkVolgnr[p.ticker] || 0) + 1;
    const mijn = t.vergelijkVolgnr[p.ticker];
    const actueel = () => t === alternatievenToestand && mijn === t.vergelijkVolgnr[p.ticker];
    knop.disabled = true;
    uitkomstPlek.replaceChildren(maakAlternatievenRegel("kleineMelding", VERGELIJK_BEZIG_TEKST));
    try {
        const res = await haalVergelijkbaar(p);
        const data = await res.json().catch(() => null);
        if (!actueel()) return;
        uitkomstPlek.replaceChildren(...(res.ok ? renderVergelijkbaarPaneel(data, p.naam)
            : [maakAlternatievenRegel("kleineMelding", vergelijkFoutTekst(res.status, data))]));
    } catch (fout) {
        if (!actueel()) return;
        uitkomstPlek.replaceChildren(maakAlternatievenRegel("kleineMelding", vergelijkFoutTekst(fout.message === "TIMEOUT" ? 504 : 0, null)));
    } finally {
        if (actueel()) knop.disabled = false;
    }
}

function maakVergelijkKnop(p) {
    const knop = document.createElement("button");
    knop.type = "button";
    knop.className = "linkKnop vergelijkKnop";
    knop.textContent = VERGELIJK_KNOP_TEKST;
    const uitkomstPlek = document.createElement("div");
    uitkomstPlek.className = "vergelijkUitkomst";
    knop.addEventListener("click", () => laadVergelijkbaar(p, knop, uitkomstPlek));
    return [knop, uitkomstPlek];
}

function renderAlternatievenPaneel(antwoord) {
    const paneel = alternatievenPaneel(antwoord);
    const lijst = document.createElement("ul");
    paneel.posities.forEach(p => {
        const li = document.createElement("li");
        const naam = document.createElement("strong");
        naam.textContent = p.naam;
        li.append(naam, ` – ${p.tekst}`);
        [p.waarschuwing, p.extra].filter(Boolean).forEach(tekst => li.appendChild(maakAlternatievenRegel("kleineMelding", tekst)));
        if (p.faseB) {
            if (p.alGevonden) li.appendChild(maakAlternatievenRegel("kleineMelding", VERGELIJK_AL_GEVONDEN_TEKST));
            li.append(...maakVergelijkKnop(p));
        }
        lijst.appendChild(li);
    });
    const regels = document.createElement("ul");
    paneel.regels.forEach(r => {
        const li = document.createElement("li");
        li.textContent = `${r.label}: ${r.tekst}`;
        regels.appendChild(li);
    });
    const nodes = [maakAlternatievenRegel("", paneel.samenvatting), lijst, regels];
    if (paneel.blijftBetalen) nodes.push(maakAlternatievenRegel("", paneel.blijftBetalen));
    if (paneel.ditJaarAlBetaald) nodes.push(maakAlternatievenRegel("kleineMelding", paneel.ditJaarAlBetaald));
    paneel.vasteRegels.forEach(tekst => nodes.push(maakAlternatievenRegel("kleineMelding", tekst)));
    return nodes;
}

async function haalAlternatieven(rij) {
    if (huidigeData.code) {
        return fetchMetTimeout(`/api/portfolio/${encodeURIComponent(huidigeData.code)}/beurs-alternatieven?beurs=${encodeURIComponent(rij.code)}`);
    }
    // Bij "niet opslaan" bestaat de portfolio alleen in dit antwoord: de invoer komt mee uit het kernantwoord.
    return fetchMetTimeout("/api/beurs-alternatieven", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ invoer: rij.alternatievenInvoer }),
    });
}

async function wisselAlternatieven(rij) {
    const t = alternatievenToestand;
    const plek = alternatievenPlek();
    if (t.open === rij.code) {
        resetBeursAlternatieven();
        toonAansluitingskosten(document.getElementById("euronextCheckbox").checked);
        return;
    }
    t.open = rij.code;
    t.volgnr += 1;
    const mijn = t.volgnr;
    plek.hidden = false;
    plek.replaceChildren(maakAlternatievenRegel("kleineMelding", "Alternatieven opzoeken..."));
    toonAansluitingskosten(document.getElementById("euronextCheckbox").checked);
    const actueel = () => t === alternatievenToestand && mijn === t.volgnr;
    try {
        const res = await haalAlternatieven(rij);
        const data = await res.json().catch(() => null);
        if (!actueel()) return;
        plek.replaceChildren(...(res.ok ? renderAlternatievenPaneel(data)
            : [maakAlternatievenRegel("kleineMelding", alternatievenFoutTekst(res.status, data))]));
    } catch (fout) {
        if (!actueel()) return;
        plek.replaceChildren(maakAlternatievenRegel("kleineMelding", alternatievenFoutTekst(fout.message === "TIMEOUT" ? 504 : 0, null)));
    }
}

// Tabblad Instellingen > Diagnostiek: meldingen van deze laadbeurt. Tellen en groeperen staat in diagnostiek.js.

// Gereset bij een nieuwe portfolio; bijnaam/code wijzigen laat de meldingen staan.
let diagnostiekMeldingen = [];
// Open/dicht-keuze van de gebruiker per categorie (overschrijft de
// standaard uit categorieStandaardOpen tot een nieuwe upload/code).
let diagnostiekOpenKeuze = new Map();

function resetDiagnostiek() {
    diagnostiekMeldingen = [];
    diagnostiekOpenKeuze = new Map();
}

function voegDiagnostiekToe(data) {
    diagnostiekMeldingen = voegMeldingenSamen(diagnostiekMeldingen, data && data.diagnostiek);
    if (actieveViewNaam() === "instellingen-diagnostiek") toonDiagnostiek();
}

function maakDiagnostiekTabel(tabel) {
    const wrapper = document.createElement("div");
    wrapper.className = "tabelWrapper scrollbareTabel diagnostiekTabel";
    const tabelEl = document.createElement("table");
    tabelEl.className = "dataTabel";
    const kopRij = document.createElement("tr");
    tabel.kolommen.forEach(kolom => {
        const th = document.createElement("th");
        th.textContent = kolom;
        kopRij.appendChild(th);
    });
    const thead = document.createElement("thead");
    thead.appendChild(kopRij);
    const tbody = document.createElement("tbody");
    tabel.rijen.forEach(rij => {
        const tr = document.createElement("tr");
        rij.forEach(cel => tr.appendChild(maakCel(cel)));
        tbody.appendChild(tr);
    });
    tabelEl.appendChild(thead);
    tabelEl.appendChild(tbody);
    wrapper.appendChild(tabelEl);
    return wrapper;
}

// Teller bovenaan, daaronder per categorie een uitklapblok (standaard open bij LET_OP/FOUT).
function toonDiagnostiek() {
    const teller = document.getElementById("diagnostiekTeller");
    const lijst = document.getElementById("diagnostiekLijst");
    lijst.innerHTML = "";

    if (diagnostiekMeldingen.length === 0) {
        teller.textContent = "Nog geen meldingen voor deze laadbeurt.";
        return;
    }
    teller.textContent = diagnostiekTellerTekst(telPerNiveau(diagnostiekMeldingen));

    groepeerPerCategorie(diagnostiekMeldingen).forEach(groep => {
        const blok = document.createElement("details");
        blok.className = "diagnostiekBlok";
        blok.open = diagnostiekOpenKeuze.has(groep.categorie)
            ? diagnostiekOpenKeuze.get(groep.categorie)
            : categorieStandaardOpen(groep.meldingen);

        const kop = document.createElement("summary");
        kop.className = "diagnostiekCategorie";
        // Via de klik (ook Enter/Spatie) i.p.v. het toggle-event: dat gaat
        // ook af op het programmatisch zetten van blok.open hierboven.
        kop.addEventListener("click", () => diagnostiekOpenKeuze.set(groep.categorie, !blok.open));
        const hoogste = hoogsteNiveau(groep.meldingen);
        kop.textContent = `${groep.categorie} (${groep.meldingen.length})`;
        const kopBadge = document.createElement("span");
        kopBadge.className = `badge badge${hoogste}`;
        kopBadge.textContent = DIAGNOSTIEK_NIVEAU_LABEL[hoogste];
        kop.appendChild(kopBadge);
        blok.appendChild(kop);

        const ul = document.createElement("ul");
        ul.className = "diagnostiekLijst";
        groep.meldingen.forEach(melding => {
            const niveau = DIAGNOSTIEK_NIVEAU_LABEL[melding.niveau] ? melding.niveau : "INFO";
            const li = document.createElement("li");
            const badge = document.createElement("span");
            badge.className = `badge badge${niveau}`;
            badge.textContent = DIAGNOSTIEK_NIVEAU_LABEL[niveau];
            const tekst = document.createElement("span");
            tekst.textContent = melding.tekst;
            li.appendChild(badge);
            const tabel = diagnostiekTabelRijen(melding.tabel);
            if (tabel) {
                const inhoud = document.createElement("div");
                inhoud.className = "diagnostiekInhoud";
                inhoud.appendChild(tekst);
                inhoud.appendChild(maakDiagnostiekTabel(tabel));
                li.appendChild(inhoud);
            } else {
                li.appendChild(tekst);
            }
            ul.appendChild(li);
        });
        blok.appendChild(ul);
        lijst.appendChild(blok);
    });
}

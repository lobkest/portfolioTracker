// De tabelbouwer en zijn cellen. Sorteren en pagineren van Transacties komt uit transacties.js.

function maakCel(tekst) {
    const td = document.createElement("td");
    td.textContent = tekst;
    return td;
}

// "€ (pct%)", groen/rood op het €-bedrag.
function maakRendementCel(eurWaarde, pctWaarde) {
    const td = maakCel(`${formatteerEuro(eurWaarde)} (${formatPct(pctWaarde)})`);
    td.className = `rendementCel ${klasseVoorRendement(eurWaarde)}`;
    return td;
}

// kolommen: [{label, sleutel?, waarde?: rij => getal|null, renderTd: rij => td}].
// Zonder opts.staat: begint in de aangeleverde volgorde; een kolom met 'waarde' is sorteerbaar
// (eerste klik aflopend, null altijd onderaan).
// opts.staat {sorteerKolom, sorteerRichting, pagina, paginaGrootte}: van de aanroeper, zodat de keuze
// een tabwissel overleeft; met paginaGrootte komt er paginanavigatie onder de tabel.
// opts.sorteer(rijen, sleutel, richting): eigen sortering; elke kolom met een sleutel is dan sorteerbaar.
// opts.klasse: CSS-klasse van de tabel (standaard dataTabel).
// opts.kaartenOpMobiel: op mobiel elke rij als kaart (CSS), met een "Sorteer op"-keuze i.p.v. de kolomkoppen;
// een kolom met kaartTitel is dan de titel van de kaart.
function maakSorteerbareTabel(kolommen, rijen, opts) {
    opts = opts || {};
    if (!rijen || rijen.length === 0) {
        const p = document.createElement("p");
        p.className = "grijsTekst";
        p.textContent = opts.legeTekst || "Geen data beschikbaar.";
        return p;
    }

    const staat = opts.staat || { sorteerKolom: null, sorteerRichting: "desc" };

    const tabel = document.createElement("table");
    tabel.className = opts.klasse || "dataTabel";
    if (opts.kaartenOpMobiel) tabel.classList.add("kaartTabel");
    const thead = document.createElement("thead");
    const kopRij = document.createElement("tr");
    const tbody = document.createElement("tbody");
    const koppen = [];

    const nav = staat.paginaGrootte ? document.createElement("div") : null;
    if (nav) nav.className = "paginaNavigatie";

    const sorteerSelect = opts.kaartenOpMobiel ? document.createElement("select") : null;

    function sorteer() {
        if (staat.sorteerKolom === null) return rijen;
        if (opts.sorteer) return opts.sorteer(rijen, staat.sorteerKolom, staat.sorteerRichting);
        const kol = kolommen[staat.sorteerKolom];
        const metWaarde = [];
        const zonderWaarde = [];
        rijen.forEach(rij => {
            const w = kol.waarde(rij);
            (w === null || w === undefined ? zonderWaarde : metWaarde).push(rij);
        });
        metWaarde.sort((a, b) => {
            const wa = kol.waarde(a), wb = kol.waarde(b);
            return staat.sorteerRichting === "asc" ? wa - wb : wb - wa;
        });
        return metWaarde.concat(zonderWaarde);
    }

    function gaNaarPagina(pagina) {
        staat.pagina = pagina;
        teken();
    }

    function maakPaginaKnop(tekst, pagina, uit) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = tekst;
        btn.disabled = uit;
        btn.addEventListener("click", () => gaNaarPagina(pagina));
        return btn;
    }

    function tekenPaginaNavigatie(totPag) {
        const knoppen = [maakPaginaKnop("Vorige", staat.pagina - 1, staat.pagina === 1)];
        for (let p = 1; p <= totPag; p++) {
            const btn = maakPaginaKnop(String(p), p, p === staat.pagina);
            if (p === staat.pagina) btn.className = "actief";
            knoppen.push(btn);
        }
        knoppen.push(maakPaginaKnop("Volgende", staat.pagina + 1, staat.pagina === totPag));
        nav.replaceChildren(...knoppen);
    }

    function teken() {
        koppen.forEach(({ th, label, sleutel }) => {
            const indicator = staat.sorteerKolom === sleutel ? (staat.sorteerRichting === "asc" ? " ▲" : " ▼") : "";
            th.textContent = label + indicator;
        });
        if (sorteerSelect) {
            sorteerSelect.value = staat.sorteerKolom === null ? "" : `${staat.sorteerKolom}|${staat.sorteerRichting}`;
        }

        let getoond = sorteer();
        if (nav) {
            const totPag = totaalPaginas(getoond.length, staat.paginaGrootte);
            if (staat.pagina > totPag) staat.pagina = totPag;
            getoond = pagineer(getoond, staat.paginaGrootte, staat.pagina);
            tekenPaginaNavigatie(totPag);
        }

        tbody.replaceChildren(...getoond.map(rij => {
            const tr = document.createElement("tr");
            kolommen.forEach(kol => {
                const td = kol.renderTd(rij);
                if (opts.kaartenOpMobiel) {
                    td.setAttribute("data-label", kol.label);
                    if (kol.kaartTitel) td.classList.add("kaartTitel");
                }
                tr.appendChild(td);
            });
            return tr;
        }));
    }

    kolommen.forEach((kol, index) => {
        const th = document.createElement("th");
        const sleutel = kol.sleutel ?? index;
        const sorteerbaar = Boolean(kol.waarde || (opts.sorteer && kol.sleutel));
        if (sorteerbaar) {
            th.className = "sorteerbaar";
            th.addEventListener("click", () => {
                if (staat.sorteerKolom === sleutel) {
                    staat.sorteerRichting = staat.sorteerRichting === "asc" ? "desc" : "asc";
                } else {
                    staat.sorteerKolom = sleutel;
                    staat.sorteerRichting = "desc";
                }
                if (nav) staat.pagina = 1;
                teken();
            });
        }
        kopRij.appendChild(th);
        koppen.push({ th, label: kol.label, sleutel, sorteerbaar });
    });

    const sorteerKeuze = sorteerSelect ? maakSorteerKeuze() : null;

    thead.appendChild(kopRij);
    tabel.appendChild(thead);
    tabel.appendChild(tbody);
    teken();

    // Wrapper: op smalle schermen scrolt de tabel zelf, niet de hele pagina.
    const wrapper = document.createElement("div");
    wrapper.className = "tabelWrapper";
    wrapper.appendChild(tabel);
    if (!nav && !sorteerKeuze) return wrapper;

    const geheel = document.createElement("div");
    if (sorteerKeuze) geheel.appendChild(sorteerKeuze);
    geheel.appendChild(wrapper);
    if (nav) geheel.appendChild(nav);
    return geheel;

    // Waarde "<sleutel>|<richting>"; zet dezelfde staat als een klik op de kolomkop.
    function maakSorteerKeuze() {
        const opties = [new Option("Standaardvolgorde", "")];
        koppen.forEach(({ label, sleutel, sorteerbaar }) => {
            if (!sorteerbaar) return;
            opties.push(new Option(`${label} (hoog → laag)`, `${sleutel}|desc`));
            opties.push(new Option(`${label} (laag → hoog)`, `${sleutel}|asc`));
        });
        sorteerSelect.replaceChildren(...opties);
        sorteerSelect.addEventListener("change", () => {
            if (sorteerSelect.value === "") {
                staat.sorteerKolom = null;
            } else {
                const [sleutel, richting] = sorteerSelect.value.split("|");
                const kop = koppen.find(k => String(k.sleutel) === sleutel);
                staat.sorteerKolom = kop.sleutel;
                staat.sorteerRichting = richting;
            }
            if (nav) staat.pagina = 1;
            teken();
        });

        const label = document.createElement("label");
        label.className = "sorteerKeuze";
        label.append("Sorteer op ", sorteerSelect);
        return label;
    }
}

if (typeof module !== "undefined") module.exports = { maakSorteerbareTabel, maakCel };

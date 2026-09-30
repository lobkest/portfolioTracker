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
    const kopRij = document.createElement("tr");
    const tbody = document.createElement("tbody");
    const koppen = [];

    const nav = staat.paginaGrootte ? document.createElement("div") : null;
    if (nav) nav.className = "paginaNavigatie";

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

        let getoond = sorteer();
        if (nav) {
            const totPag = totaalPaginas(getoond.length, staat.paginaGrootte);
            if (staat.pagina > totPag) staat.pagina = totPag;
            getoond = pagineer(getoond, staat.paginaGrootte, staat.pagina);
            tekenPaginaNavigatie(totPag);
        }

        tbody.replaceChildren(...getoond.map(rij => {
            const tr = document.createElement("tr");
            kolommen.forEach(kol => tr.appendChild(kol.renderTd(rij)));
            return tr;
        }));
    }

    kolommen.forEach((kol, index) => {
        const th = document.createElement("th");
        const sleutel = kol.sleutel ?? index;
        if (kol.waarde || (opts.sorteer && kol.sleutel)) {
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
        koppen.push({ th, label: kol.label, sleutel });
    });

    tabel.appendChild(kopRij);
    tabel.appendChild(tbody);
    teken();

    // Wrapper: op smalle schermen scrolt de tabel zelf, niet de hele pagina.
    const wrapper = document.createElement("div");
    wrapper.className = "tabelWrapper";
    wrapper.appendChild(tabel);
    if (!nav) return wrapper;

    const metNavigatie = document.createElement("div");
    metNavigatie.append(wrapper, nav);
    return metNavigatie;
}

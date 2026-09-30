// Tabblad Statistieken: totalen, posities, verkochte posities, rendement per jaar en samengesteld rendement.

function maakPositieTabel(posities, tickerNamen) {
    const kolommen = [
        {
            label: "Naam/ticker",
            renderTd: p => {
                const td = document.createElement("td");
                const naam = (tickerNamen && tickerNamen[p.ticker]) || p.ticker;
                td.textContent = `${naam} (${p.ticker})`;
                return td;
            },
        },
        {
            label: "Aantal",
            waarde: p => p.aantal,
            renderTd: p => maakCel(p.aantal.toLocaleString("nl-NL", { maximumFractionDigits: 4 })),
        },
        {
            label: "Huidige waarde",
            waarde: p => p.huidige_waarde,
            renderTd: p => maakCel(formatteerEuro(p.huidige_waarde)),
        },
        {
            label: "GAK",
            waarde: p => p.gak,
            renderTd: p => maakCel(formatteerEuro(p.gak)),
        },
        {
            label: "Huidige koers",
            waarde: p => p.huidige_koers,
            renderTd: p => maakCel(formatteerEuro(p.huidige_koers)),
        },
        {
            // Op het getal, niet op de weergavetekst.
            label: "Rendement",
            waarde: p => p.rendement_pct,
            renderTd: p => maakRendementCel(p.rendement_eur, p.rendement_pct),
        },
        {
            label: "Dividend ontvangen",
            waarde: p => p.dividend_ontvangen || 0,
            renderTd: p => maakCel(formatteerEuro(p.dividend_ontvangen || 0)),
        },
    ];
    return maakSorteerbareTabel(kolommen, posities, { legeTekst: "Geen open posities." });
}

function maakGeslotenPositiesTabel(geslotenPosities) {
    const kolommen = [
        {
            label: "Status",
            renderTd: p => {
                const td = document.createElement("td");
                const badge = document.createElement("span");
                badge.textContent = p.nog_in_bezit ? "Deels verkocht" : "Gesloten";
                badge.className = p.nog_in_bezit ? "badge badgeLos badgeDeelsVerkocht" : "badge badgeLos badgeGesloten";
                td.appendChild(badge);
                return td;
            },
        },
        {
            label: "Naam/ticker",
            renderTd: p => maakCel(`${p.naam} (${p.ticker})`),
        },
        {
            label: "Aantal",
            waarde: p => p.aantal,
            renderTd: p => {
                const td = document.createElement("td");
                const aantalTekst = p.aantal.toLocaleString("nl-NL", { maximumFractionDigits: 4 });
                if (p.nog_in_bezit) {
                    const resterendTekst = (p.resterend_aantal || 0).toLocaleString("nl-NL", { maximumFractionDigits: 4 });
                    td.textContent = `${aantalTekst} verkocht, ${resterendTekst} nog in bezit`;
                } else {
                    td.textContent = aantalTekst;
                }
                return td;
            },
        },
        {
            label: "Gem. aankoopkoers",
            waarde: p => p.gemiddelde_aankoopkoers,
            renderTd: p => maakCel(formatteerEuro(p.gemiddelde_aankoopkoers)),
        },
        {
            label: "Gem. verkoopkoers",
            waarde: p => p.gemiddelde_verkoopkoers,
            renderTd: p => maakCel(p.gemiddelde_verkoopkoers !== null ? formatteerEuro(p.gemiddelde_verkoopkoers) : "onbekend"),
        },
        {
            label: "Rendement (koers)",
            waarde: p => p.rendement_pct,
            renderTd: p => maakRendementCel(p.rendement_eur, p.rendement_pct),
        },
        {
            label: "Dividend ontvangen",
            waarde: p => p.dividend_ontvangen || 0,
            renderTd: p => maakCel(formatteerEuro(p.dividend_ontvangen || 0)),
        },
    ];
    return maakSorteerbareTabel(kolommen, geslotenPosities, { legeTekst: "Geen verkochte of deels verkochte posities." });
}

function maakJarenTabel(jaren) {
    const kolommen = [
        {
            label: "Jaar",
            waarde: j => j.jaar,
            renderTd: j => {
                const td = document.createElement("td");
                const jaarStrong = document.createElement("strong");
                jaarStrong.textContent = j.jaar;
                td.appendChild(jaarStrong);
                const detail = document.createElement("div");
                detail.className = "kleineMelding";
                detail.textContent = `${j.dagen_verstreken}d, ${j.pct_van_jaar}% van jaar`;
                td.appendChild(detail);
                return td;
            },
        },
        {
            label: "Startwaarde",
            waarde: j => j.startwaarde,
            renderTd: j => maakCel(formatteerEuro(j.startwaarde)),
        },
        {
            label: "Ingelegd",
            waarde: j => j.ingelegd,
            renderTd: j => maakCel(formatteerEuro(j.ingelegd)),
        },
        {
            label: "Eindwaarde",
            waarde: j => j.eindwaarde,
            renderTd: j => maakCel(formatteerEuro(j.eindwaarde)),
        },
        {
            label: "Winst",
            waarde: j => j.winst_eur,
            renderTd: j => maakRendementCel(j.winst_eur, j.winst_pct),
        },
    ];

    // Standaard nieuwste jaar eerst.
    const rijen = [...jaren].reverse();
    return maakSorteerbareTabel(kolommen, rijen, { legeTekst: "Geen jaargegevens beschikbaar." });
}

function maakGeavanceerdSectie(geavanceerd) {
    const rij = document.createElement("div");
    rij.className = "tegelRij tegelRijCompact";

    rij.appendChild(maakStatTegel("Gemiddeld jaarrendement", formatPct(geavanceerd.gemiddeld_jaarrendement_pct), klasseVoorRendement(geavanceerd.gemiddeld_jaarrendement_pct)));
    rij.appendChild(maakStatTegel("XIRR", formatPct(geavanceerd.xirr_pct), klasseVoorRendement(geavanceerd.xirr_pct)));
    rij.appendChild(maakStatTegel("TWR", formatPct(geavanceerd.twr_pct), klasseVoorRendement(geavanceerd.twr_pct)));
    rij.appendChild(maakStatTegel("Aantal jaren", geavanceerd.aantal_jaren !== null ? geavanceerd.aantal_jaren.toFixed(2) : "onbekend"));

    return rij;
}

function toonStatistieken() {
    const stats = huidigeData.statistieken;
    document.getElementById("statistiekenGeen").hidden = Boolean(stats);
    document.getElementById("statistiekenInhoud").hidden = !stats;
    if (!stats) return;

    const tickerNamen = {};
    (huidigeData.tickers || []).forEach(t => { tickerNamen[t.ticker] = t.naam; });

    document.getElementById("statistiekenTotalen").replaceChildren(maakTotalenSectie(stats.totalen));
    document.getElementById("statistiekenPosities").replaceChildren(maakPositieTabel(stats.posities, tickerNamen));
    document.getElementById("statistiekenGesloten").replaceChildren(maakGeslotenPositiesTabel(stats.gesloten_posities));
    document.getElementById("statistiekenJaren").replaceChildren(maakJarenTabel(stats.jaren));
    document.getElementById("statistiekenGeavanceerd").replaceChildren(maakGeavanceerdSectie(stats.geavanceerd));
}

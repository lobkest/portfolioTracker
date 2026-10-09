// Tabblad Dividend: totalen, grafiek en alle uitkeringen. De datasets van de grafiek komen uit dividend.js.

const DIVIDEND_TOESTANDEN = ["dividendLaden", "dividendFout", "dividendGeenOverzicht", "dividendStatsSectie"];

function maakDividendTotalenTabel(perTicker) {
    const kolommen = [
        { label: "Aandeel/ETF", renderTd: item => maakCel(item.bijnaam) },
        { label: "Netto dividend", renderTd: item => maakCel(formatteerEuro(item.totaal_netto)) },
    ];
    return maakSorteerbareTabel(kolommen, perTicker, { klasse: "compacteTabel", legeTekst: "Nog geen dividend ontvangen." });
}

// Bedragen zijn altijd EUR; 'valuta' is de oorspronkelijke valuta en staat alleen tussen haakjes.
function formatteerDividendBedrag(bedragEur, valuta) {
    const basis = formatteerEuro(bedragEur);
    if (basis === "onbekend" || !valuta || valuta === "EUR") return basis;
    return `${basis} (${valuta})`;
}

function maakDividendUitkeringenTabel(lijst) {
    const kolommen = [
        {
            label: "Datum",
            // Timestamp: maakSorteerbareTabel rekent wa - wb.
            waarde: r => new Date(r.datum).getTime(),
            renderTd: r => maakCel(formatDatum(r.datum)),
        },
        {
            label: "Aandeel",
            renderTd: r => {
                const td = document.createElement("td");
                td.textContent = r.bijnaam;
                // Herbelegd door DeGiro: verklaart een klein of negatief bedrag.
                if (r.herinvesteerd === true) {
                    const badge = document.createElement("span");
                    badge.className = "badge badgeHerinvesteerd";
                    badge.textContent = "herinvesteerd";
                    badge.title = "Automatisch herbelegd i.p.v. uitgekeerd (DeGiro: 'Dividend Herinvestering')";
                    td.appendChild(badge);
                }
                return td;
            },
        },
        {
            label: "Bruto",
            waarde: r => r.bruto_eur,
            renderTd: r => maakCel(formatteerDividendBedrag(r.bruto_eur, r.valuta)),
        },
        {
            label: "Belasting",
            waarde: r => r.belasting_eur,
            renderTd: r => maakCel(formatteerDividendBedrag(r.belasting_eur, r.valuta)),
        },
        {
            label: "Netto",
            waarde: r => r.netto_eur,
            renderTd: r => maakCel(formatteerDividendBedrag(r.netto_eur, r.valuta)),
        },
    ];

    return maakSorteerbareTabel(kolommen, lijst, { legeTekst: "Geen uitkeringen beschikbaar." });
}

function renderDividendUitkeringenlijst(lijst) {
    const heeftUitkeringen = Boolean(lijst && lijst.length > 0);
    document.getElementById("dividendUitkeringenSectie").hidden = !heeftUitkeringen;
    if (!heeftUitkeringen) return;
    document.getElementById("dividendUitkeringenTabel").replaceChildren(maakDividendUitkeringenTabel(lijst));
}

function renderDividendStats(data) {
    document.getElementById("dividendTotaal").textContent = `${formatteerEuro(data.totaal_netto)} totaal ontvangen dividend`;
    document.getElementById("dividendPerTicker").replaceChildren(maakDividendTotalenTabel(data.per_ticker || []));
}

function toonDividendChart(cumulatief) {
    if (chart) chart.destroy();
    const naamVoorTicker = ticker => (huidigeData.tickers.find(t => t.ticker === ticker) || {}).naam || ticker;
    const eersteTransactie = huidigeData.chart_data && huidigeData.chart_data.labels[0];
    const vandaagIso = new Date().toISOString().slice(0, 10);
    const trapreeksen = bouwDividendTrapreeksen(cumulatief, eersteTransactie, vandaagIso);
    const datasets = bouwDividendDatasets(trapreeksen, naamVoorTicker, kleurVoorTicker);

    chart = new Chart(document.getElementById("rendementChart"), {
        type: "line",
        data: { datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            locale: "nl-NL",
            scales: {
                x: {
                    type: "time",
                    time: {
                        tooltipFormat: "dd-MM-yyyy",
                        displayFormats: {
                            day: "dd-MM-yyyy",
                            week: "dd-MM-yyyy",
                            month: "MM-yyyy",
                            quarter: "MM-yyyy",
                            year: "yyyy"
                        }
                    }
                },
                y: { stacked: true, beginAtZero: true, title: { display: true, text: "Cumulatief dividend (€)" } }
            },
            plugins: {
                tooltip: {
                    callbacks: {
                        label: (ctx) => `${ctx.dataset.label}: ${formatteerEuro(ctx.parsed.y)}`
                    }
                },
                zoom: zoomOpties(),
                datalabels: { display: false }
            }
        }
    });
}

// Alleen Dividend heeft het rekeningoverzicht nodig; de rest werkt zonder.
function toonGeenRekeningoverzicht() {
    if (chart) { chart.destroy(); chart = null; }
    document.getElementById("chartWrapper").style.display = "none";
    toonAlleen(DIVIDEND_TOESTANDEN, "dividendGeenOverzicht");
}

function renderDividend(data) {
    toonAlleen(DIVIDEND_TOESTANDEN, "dividendStatsSectie");
    renderDividendStats(data);
    toonDividendChart(data.cumulatief);
    renderDividendUitkeringenlijst(data.lijst);
}

async function toonDividend() {
    toonAlleen(DIVIDEND_TOESTANDEN, "dividendLaden");
    document.getElementById("dividendUitkeringenSectie").hidden = true;

    if (!huidigeData.code) {
        const data = huidigeData.dividend;
        if (data && data.beschikbaar) renderDividend(data);
        else toonGeenRekeningoverzicht();
        return;
    }

    let res, data;
    try {
        res = await fetch(`/api/portfolio/${huidigeData.code}/dividend`);
        data = await res.json();
    } catch (e) {
        toonAlleen(DIVIDEND_TOESTANDEN, "dividendFout");
        return;
    }

    if (!res.ok || !data.beschikbaar) {
        toonGeenRekeningoverzicht();
        return;
    }

    renderDividend(data);
}

document.getElementById("dividendNaarUploadBtn").addEventListener("click", () => location.assign(START_PAD));

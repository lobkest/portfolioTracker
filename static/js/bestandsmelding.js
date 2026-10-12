// Pure logica voor de ene melding als het rekeningoverzicht en/of het transactiebestand niet de hele periode lijken te beslaan (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    const KOP = "Je bestanden beslaan mogelijk niet de hele periode.";
    const KOP_ALLEEN_REKENING = "Je rekeningoverzicht (Account.xlsx) beslaat niet de hele periode.";
    const GEVOLG_REKENING = "Netto gestort, vrije ruimte (cash) en Totaal W/V kunnen daardoor niet kloppen.";
    const GEVOLG_TRANSACTIES = "Posities, GAK en rendement kunnen daardoor niet kloppen.";
    const LINK_MET_CODE = "Upload de volledige bestanden";
    const LINK_ZONDER_CODE = "Upload bij de volgende analyse de volledige bestanden";
    const GEEN_REKENINGOVERZICHT = "Geen rekeningoverzicht geüpload.";
    const GEEN_AANWIJZINGEN = "Geen aanwijzingen dat er transacties ontbreken.";
    const NIET_TE_CONTROLEREN = "Of dit bestand compleet is, is niet vast te stellen omdat het rekeningoverzicht onvolledig is.";
    const WAARSCHIJNLIJK_EERDER = "Er zijn waarschijnlijk ook eerdere transacties.";

    const REDEN_BEGINSALDO = "beginsaldo_niet_nul";
    const REDEN_GESTORT_TE_LAAG = "gestort_kleiner_dan_geinvesteerd";
    const REDEN_SALDO_NEGATIEF = "saldo_negatief";

    function datumNl(iso) {
        const [jaar, maand, dag] = iso.slice(0, 10).split("-");
        return `${dag}-${maand}-${jaar}`;
    }

    function euro(bedrag) {
        const [hele, cent] = Math.abs(bedrag).toFixed(2).split(".");
        return `€${hele.replace(/\B(?=(\d{3})+(?!\d))/g, ".")},${cent}`;
    }

    function periode(van, tot) {
        if (!van) return null;
        return tot ? `${datumNl(van)} t/m ${datumNl(tot)}` : datumNl(van);
    }

    function heeft(waarde) {
        return typeof waarde === "number" && Number.isFinite(waarde);
    }

    // Zonder het bedrag in het antwoord valt een reden terug op de backend-tekst.
    function rekeningRedenTekst(status, code, backendTekst) {
        if (code === REDEN_BEGINSALDO && heeft(status.beginsaldo_eur) && status.eerste_regel_datum) {
            return `Het overzicht begint met een saldo van ${euro(status.beginsaldo_eur)}; er stond dus al geld op de rekening vóór ${datumNl(status.eerste_regel_datum)}.`;
        }
        if (code === REDEN_GESTORT_TE_LAAG && heeft(status.netto_gestort_eur) && heeft(status.geinvesteerd_eur)) {
            return `Netto gestort is ${euro(status.netto_gestort_eur)}, terwijl ${euro(status.geinvesteerd_eur)} is geïnvesteerd.`;
        }
        if (code === REDEN_SALDO_NEGATIEF && heeft(status.saldo_eur)) {
            return `Het kassaldo is −${euro(status.saldo_eur)}; dat kan bij DEGIRO niet.`;
        }
        return backendTekst;
    }

    function rekeningBlok(status) {
        if (!status) {
            return { titel: "Rekeningoverzicht (Account.xlsx)", periode: null, tekst: GEEN_REKENINGOVERZICHT, redenen: [] };
        }
        const codes = status.redenen || [];
        const teksten = status.redenen_tekst || [];
        return {
            titel: "Rekeningoverzicht (Account.xlsx)",
            periode: periode(status.eerste_regel_datum, status.laatste_regel_datum),
            tekst: null,
            redenen: status.volledig === false ? codes.map((code, i) => rekeningRedenTekst(status, code, teksten[i])).filter(Boolean) : [],
        };
    }

    function vermoedtEerdereTransacties(rekening) {
        const codes = (rekening && rekening.redenen) || [];
        return codes.includes(REDEN_BEGINSALDO) || codes.includes(REDEN_GESTORT_TE_LAAG);
    }

    function transactieBlok(status, rekening) {
        const blok = {
            titel: "Transactiebestand (Transactions.xlsx)",
            periode: periode(status.eerste_transactie_datum, status.laatste_transactie_datum),
            tekst: null,
            redenen: [],
        };
        if (status.controle === "aanwijzingen") {
            blok.redenen = (status.redenen || []).map(reden => reden.tekst);
        } else if (status.controle === "niet_te_controleren") {
            blok.tekst = vermoedtEerdereTransacties(rekening) ? `${NIET_TE_CONTROLEREN} ${WAARSCHIJNLIJK_EERDER}` : NIET_TE_CONTROLEREN;
        } else {
            blok.tekst = GEEN_AANWIJZINGEN;
        }
        return blok;
    }

    // null als er niets te melden valt. `metCode` false = "niet opslaan" (geen link, alleen tekst).
    function bestandsmelding(rekening, transactie, metCode) {
        const rekeningOnvolledig = Boolean(rekening) && rekening.volledig === false;
        const transactieAanwijzingen = Boolean(transactie) && transactie.controle === "aanwijzingen";
        if (!rekeningOnvolledig && !transactieAanwijzingen) return null;

        const gevolg = [];
        if (rekeningOnvolledig) gevolg.push(GEVOLG_REKENING);
        if (transactieAanwijzingen) gevolg.push(GEVOLG_TRANSACTIES);
        return {
            kop: transactie ? KOP : KOP_ALLEEN_REKENING,
            gevolg: gevolg.join(" "),
            blokken: [rekeningBlok(rekening), ...(transactie ? [transactieBlok(transactie, rekening)] : [])],
            linkTekst: metCode ? LINK_MET_CODE : LINK_ZONDER_CODE,
            linkNaarInstellingen: Boolean(metCode),
        };
    }

    const LABEL_OPNIEUW = "Opnieuw uploaden";
    const LABEL_AANBEVOLEN = "Aanbevolen";

    function bestandNiveau(niveau, tekst, periodeTekst) {
        return { niveau, label: niveau === "opnieuw" ? LABEL_OPNIEUW : niveau === "aanbevolen" ? LABEL_AANBEVOLEN : null, tekst, periode: periodeTekst };
    }

    const GEEN_NIVEAU = bestandNiveau("geen", null, null);

    function rekeningNiveau(status) {
        if (!status || status.volledig !== false) return GEEN_NIVEAU;
        const huidig = periode(status.eerste_regel_datum, status.laatste_regel_datum);
        const tekst = status.eerste_regel_datum
            ? `Dit overzicht begint op ${datumNl(status.eerste_regel_datum)}; upload het volledige bestand.`
            : "Dit overzicht lijkt niet de hele periode te beslaan; upload het volledige bestand.";
        return bestandNiveau("opnieuw", tekst, huidig);
    }

    function transactieNiveau(status, rekening) {
        if (!status) return GEEN_NIVEAU;
        const huidig = periode(status.eerste_transactie_datum, status.laatste_transactie_datum);
        if (status.controle === "aanwijzingen") {
            return bestandNiveau("opnieuw", "Er lijken transacties te ontbreken; upload het volledige bestand, vanaf je eerste transactie.", huidig);
        }
        if (status.controle === "niet_te_controleren") {
            const eerder = vermoedtEerdereTransacties(rekening) ? ` ${WAARSCHIJNLIJK_EERDER}` : "";
            return bestandNiveau("aanbevolen", `${NIET_TE_CONTROLEREN}${eerder} Upload voor de zekerheid het volledige bestand.`, huidig);
        }
        return GEEN_NIVEAU;
    }

    // Per uploadveld (bestand1 = transacties, bestand2 = rekeningoverzicht); `eerste` is het veld om naartoe te scrollen: sterk vóór zacht, dan veldvolgorde.
    function bestandNiveaus(rekening, transactie) {
        const velden = { bestand1: transactieNiveau(transactie, rekening), bestand2: rekeningNiveau(rekening) };
        const eerste = ["opnieuw", "aanbevolen"].map(niveau => Object.keys(velden).find(v => velden[v].niveau === niveau)).find(Boolean) || null;
        return { velden, eerste };
    }

    const exportsObj = { bestandsmelding, bestandNiveaus };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof globalThis !== "undefined" ? globalThis : this);

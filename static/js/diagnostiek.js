// Rekenkern voor het Diagnostiek-subtabblad (Instellingen > Diagnostiek):
// meldingen uit de backend-antwoorden (sleutel "diagnostiek", zie
// diagnostiek.py) samenvoegen, tellen en groeperen. Los van DOM-manipulatie
// gehouden (net als menu.js) zodat het zowel in de browser (index.html) als
// onder Node (tests/test_diagnostiek.js) draait.

(function (root) {
    "use strict";

    // Ernstigste eerst -- zelfde niveaus als in diagnostiek.py.
    const DIAGNOSTIEK_NIVEAUS = ["FOUT", "LET_OP", "INFO", "GOED"];

    const DIAGNOSTIEK_NIVEAU_LABEL = {
        FOUT: "Fout",
        LET_OP: "Let op",
        INFO: "Info",
        GOED: "Goed",
    };

    function ernstIndex(niveau) {
        const i = DIAGNOSTIEK_NIVEAUS.indexOf(niveau);
        return i === -1 ? DIAGNOSTIEK_NIVEAUS.indexOf("INFO") : i;
    }

    function meldingSleutel(m) {
        return `${m.categorie}\u0000${m.sleutel}`;
    }

    // Voegt `nieuw` samen met `bestaand`: ontdubbelen op categorie + sleutel,
    // de nieuwste wint (op de plek van de oude). Geeft een nieuwe array
    // terug; de invoer blijft ongewijzigd. null/undefined telt als leeg.
    function voegMeldingenSamen(bestaand, nieuw) {
        const resultaat = (bestaand || []).slice();
        const positie = new Map(resultaat.map((m, i) => [meldingSleutel(m), i]));
        (nieuw || []).forEach(m => {
            const sleutel = meldingSleutel(m);
            if (positie.has(sleutel)) {
                resultaat[positie.get(sleutel)] = m;
            } else {
                positie.set(sleutel, resultaat.length);
                resultaat.push(m);
            }
        });
        return resultaat;
    }

    // Aantal meldingen per niveau, altijd met alle vier de niveaus.
    function telPerNiveau(meldingen) {
        const telling = {};
        DIAGNOSTIEK_NIVEAUS.forEach(n => { telling[n] = 0; });
        (meldingen || []).forEach(m => {
            telling[DIAGNOSTIEK_NIVEAUS[ernstIndex(m.niveau)]] += 1;
        });
        return telling;
    }

    // Groepeert per categorie: [{categorie, meldingen}]. Binnen een categorie
    // ernstigste eerst; categorieën op hun ernstigste melding, bij gelijke
    // ernst in volgorde van eerste voorkomen.
    function groepeerPerCategorie(meldingen) {
        const groepen = [];
        const perCategorie = new Map();
        (meldingen || []).forEach(m => {
            if (!perCategorie.has(m.categorie)) {
                const groep = { categorie: m.categorie, meldingen: [] };
                perCategorie.set(m.categorie, groep);
                groepen.push(groep);
            }
            perCategorie.get(m.categorie).meldingen.push(m);
        });
        groepen.forEach(g => {
            g.meldingen.sort((a, b) => ernstIndex(a.niveau) - ernstIndex(b.niveau));
        });
        const ernstigste = g => ernstIndex(g.meldingen[0].niveau);
        groepen.sort((a, b) => ernstigste(a) - ernstigste(b));
        return groepen;
    }

    // "1 fout, 2 let op" -- alleen niveaus met minstens één melding, lege
    // string als er niets is.
    function diagnostiekTellerTekst(telling) {
        return DIAGNOSTIEK_NIVEAUS
            .filter(n => (telling[n] || 0) > 0)
            .map(n => `${telling[n]} ${DIAGNOSTIEK_NIVEAU_LABEL[n].toLowerCase()}`)
            .join(", ");
    }

    const exportsObj = {
        DIAGNOSTIEK_NIVEAUS, DIAGNOSTIEK_NIVEAU_LABEL,
        voegMeldingenSamen, telPerNiveau, groepeerPerCategorie, diagnostiekTellerTekst,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);

// Rekenkern voor de "gekozen bestand + x-knop"-rij onder de bestandsvelden
// op de startpagina. Los van DOM-manipulatie gehouden (net als menu.js)
// zodat het zowel in de browser (index.html) als onder Node
// (tests/test_bestandskeuze.js) draait. De DOM-koppeling staat in app.js
// (koppelBestandWisKnop()).

(function (root) {
    "use strict";

    // bestandsnamen: lijst met namen van de gekozen bestanden (uit
    // input.files). Geeft terug of de rij zichtbaar moet zijn en welke
    // tekst erin staat. Meer dan één bestand kan nu niet (geen `multiple`
    // op de inputs), maar dan tonen we het aantal i.p.v. één naam.
    function bestandSelectieWeergave(bestandsnamen) {
        const namen = Array.isArray(bestandsnamen) ? bestandsnamen : [];
        if (namen.length === 0) {
            return { zichtbaar: false, tekst: "" };
        }
        if (namen.length === 1) {
            return { zichtbaar: true, tekst: namen[0] };
        }
        return { zichtbaar: true, tekst: `${namen.length} bestanden` };
    }

    const exportsObj = { bestandSelectieWeergave };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);

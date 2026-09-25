// Pure logica voor de "gekozen bestand + x-knop"-rij (DOM-kant: koppelBestandWisKnop() in app.js).

(function (root) {
    "use strict";

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

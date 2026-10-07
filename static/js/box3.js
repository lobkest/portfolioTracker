// Pure helpers voor het Box 3-tabblad (zonder DOM, getest onder Node). Het belastingrekenwerk zit in box3.py.

(function (root) {
    "use strict";

    const BOX3_STELSELS = ["huidig", "aanwas", "vermogenswinst"];

    // Nederlandse notatie: "12.500" en "1.234,56"; leeg is 0, onleesbaar is null. Negatief laat de backend afkeuren.
    function leesBox3Bedrag(tekst) {
        let t = String(tekst ?? "").replace(/[€\s]/g, "");
        if (t === "") return 0;
        if (t.includes(",")) {
            t = t.replace(/\./g, "").replace(",", ".");
        } else if (/^-?\d{1,3}(\.\d{3})+$/.test(t)) {
            t = t.replace(/\./g, "");
        }
        if (!/^-?\d+(\.\d+)?$/.test(t)) return null;
        return Number(t);
    }

    // velden: {sleutel: tekst} plus fiscale_partner (bool). ongeldig: de sleutels die geen bedrag zijn.
    function bouwBox3Invoer(velden) {
        const invoer = { fiscale_partner: Boolean(velden.fiscale_partner) };
        const ongeldig = [];
        Object.entries(velden).forEach(([sleutel, tekst]) => {
            if (sleutel === "fiscale_partner") return;
            const bedrag = leesBox3Bedrag(tekst);
            if (bedrag === null) ongeldig.push(sleutel);
            else invoer[sleutel] = bedrag;
        });
        return { invoer, ongeldig };
    }

    // Belasting per jaar per stelsel; null = niet berekend (huidig stelsel vóór 2023), dus geen staaf.
    function box3GrafiekReeksen(berekening) {
        const reeksen = { labels: berekening.jaren.map(j => String(j.jaar)) };
        BOX3_STELSELS.forEach(stelsel => {
            reeksen[stelsel] = berekening.jaren.map(j => (j[stelsel].belasting ?? null));
        });
        return reeksen;
    }

    const exportsObj = { BOX3_STELSELS, leesBox3Bedrag, bouwBox3Invoer, box3GrafiekReeksen };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);

// Pure logica voor het Bijnamen-tabblad: welke naambron actief is en of een ingetypte bijnaam opgeslagen moet worden (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    // Index van de eerste bron waarvan de naam gelijk is aan de huidige bijnaam; -1 bij een eigen naam.
    function actieveNaamBron(bronNamen, huidigeNaam) {
        if (!huidigeNaam) return -1;
        return bronNamen.findIndex(naam => naam === huidigeNaam);
    }

    function bijnaamGewijzigd(invoer, huidigeNaam) {
        const naam = (invoer || "").trim();
        return naam !== "" && naam !== huidigeNaam;
    }

    const exportsObj = { actieveNaamBron, bijnaamGewijzigd };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);

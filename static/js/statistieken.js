// Pure logica voor het tabblad Statistieken (zonder DOM, getest onder Node).

(function (root) {
    "use strict";
    const { formatteerEuro } = typeof module !== "undefined" && module.exports ? require("./getallen.js") : root;

    // Mobiele rij van Rendement per jaar: dagen alleen bij een onvolledig jaar.
    function jaarSubregel(jaar) {
        const ingelegd = `ingelegd ${formatteerEuro(jaar.ingelegd)}`;
        return jaar.pct_van_jaar >= 100 ? ingelegd : `${jaar.dagen_verstreken} d · ${ingelegd}`;
    }

    const exportsObj = { jaarSubregel };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);

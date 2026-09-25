// Maakt van <span class="infoTip" data-label="...">tekst</span> een (i)-knop met tooltip;
// zonder JS blijft de tekst leesbaar. Muis: hover; toetsenbord: focus; telefoon: tik.
(function () {
    "use strict";

    function heeftToetsenbordFocus(knop) {
        try {
            return knop.matches(":focus-visible");
        } catch (_) {
            return false; // oudere browser zonder :focus-visible
        }
    }

    function initInfoTips() {
        const tips = Array.from(document.querySelectorAll(".infoTip"));

        function zetOpen(tip, open) {
            tip.classList.toggle("open", open);
            tip.querySelector(".infoTipKnop").setAttribute("aria-expanded", String(open));
        }

        function sluitAlle(behalve) {
            tips.forEach(t => { if (t !== behalve) zetOpen(t, false); });
        }

        tips.forEach((tip, i) => {
            const tekstId = `infoTipTekst${i + 1}`;

            const tekst = document.createElement("span");
            tekst.className = "infoTipTekst";
            tekst.id = tekstId;
            tekst.setAttribute("role", "tooltip");
            while (tip.firstChild) tekst.appendChild(tip.firstChild);

            const knop = document.createElement("button");
            knop.type = "button";
            knop.className = "infoTipKnop";
            knop.textContent = "i";
            knop.setAttribute("aria-label", `Uitleg: ${tip.dataset.label || "meer informatie"}`);
            knop.setAttribute("aria-describedby", tekstId);
            knop.setAttribute("aria-expanded", "false");

            tip.append(knop, tekst);

            // Een muisklik volgt op een pointerenter die de tooltip al opende;
            // zonder deze vlag zou die klik hem meteen weer sluiten.
            let laatsteInvoerWasMuis = false;

            tip.addEventListener("pointerenter", (e) => {
                if (e.pointerType !== "mouse") return;
                sluitAlle(tip);
                zetOpen(tip, true);
            });
            tip.addEventListener("pointerleave", (e) => {
                if (e.pointerType !== "mouse") return;
                if (!heeftToetsenbordFocus(knop)) zetOpen(tip, false);
            });

            knop.addEventListener("pointerdown", (e) => {
                laatsteInvoerWasMuis = e.pointerType === "mouse";
            });
            knop.addEventListener("keydown", () => { laatsteInvoerWasMuis = false; });

            knop.addEventListener("click", () => {
                if (laatsteInvoerWasMuis) {
                    laatsteInvoerWasMuis = false;
                    return;
                }
                sluitAlle(tip);
                zetOpen(tip, !tip.classList.contains("open"));
            });

            // Alleen toetsenbord-focus opent (:focus-visible); een tik geeft ook
            // focus en zou anders botsen met de klik-toggle hierboven.
            knop.addEventListener("focus", () => {
                if (heeftToetsenbordFocus(knop)) {
                    sluitAlle(tip);
                    zetOpen(tip, true);
                }
            });
            knop.addEventListener("blur", () => zetOpen(tip, false));
        });

        document.addEventListener("pointerdown", (e) => {
            tips.forEach(t => { if (!t.contains(e.target)) zetOpen(t, false); });
        });
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape") sluitAlle(null);
        });
    }

    if (typeof document !== "undefined") {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", initInfoTips);
        } else {
            initInfoTips();
        }
    }
})();

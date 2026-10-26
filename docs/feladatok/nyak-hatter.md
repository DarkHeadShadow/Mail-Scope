# Feladat: nyitóképernyő 3D nyomtatottáramkör-háttérrel

Készítő: Bálint Szabolcs

## Cél
Amikor nincs megnyitott ügy, a Mail-Scope egy nyitóképernyővel induljon. A háttere egy
perspektivikusan dőlő, 3D hatású nyomtatott áramkör, amelyen fényimpulzusok futnak: az adat a
négy forrás-chipből (PST, OST, MBOX, EML) a középső, Mail-Scope logós chipbe áramlik.

Referencia: `docs/design/nyitokepernyo-nyak-referencia.dc.html`. Ez a tervvászon forrása, nem
futtatható oldal (a `{{…}}` helyőrzőket és a `renderVals()`-t a tervező eszköz tölti ki). Az
elrendezést, a színeket, a vezetékgenerátort és a CSS-animációt innen kell átvenni; a
`renderVals()` útvonal-generátora (seedelt véletlen, 45°-os PCB-vezetékek) szinte változatlanul
átültethető sima JavaScriptbe.

## Mit kell elkészíteni
1. **Nyitónézet a `ui/index.html`-ben**, amely akkor látszik, ha a `/api/status` szerint nincs
   megnyitott ügy:
   - fejléc: logó (A+ jel) + „Mail-Scope” + „Levelezés-elemző”;
   - címsor: „Minden levél egy helyen, időrendben.”, alatta a rövid leírás a referenciából;
   - gombok: „Új ügy létrehozása” és „Ügy megnyitása…” (a meglévő mappatallózó és
     `/api/case` folyamatot használják, új API nélkül);
   - „Legutóbbi ügyek” lista, ha van hozzá adat (lásd 5. pont), különben ne jelenjen meg;
   - lábléc: verzió, „Készítő: Bálint Szabolcs”, „Offline mód – az adatok nem hagyják el a gépet”.
   Ügy megnyitása után a nyitónézet tűnjön el, és a mostani munkaterület jelenjen meg.
2. **Háttér** egy új `ui/pcb-background.js` modulban, amely az SVG-t `document.createElementNS`
   segítségével építi fel:
   - réteg alulról felfelé: lap (pöttyminta) → halvány belső buszok → vezeték-árnyék →
     rézvezetékek → átmenőfuratok → lábak → chipek (árnyék, test, felirat) → középső chip
     „lélegző” fénye → impulzusok (széles halvány + vékony fényes vonal);
   - 3D: CSS `perspective` a tárolón, `rotateX(52deg)` a lapon; bal oldalt és felül sötét
     köd-átmenet, hogy a szöveg olvasható maradjon;
   - színek: zöld `#4ADE80` (PST, OST) és borostyán `#F2B544` (MBOX, EML), ugyanazok, mint a
     beszélgetés-nézetben;
   - fix seed, hogy a minta minden indításkor ugyanaz legyen;
   - a szerver csak felsorolt UI-fájlokat szolgál ki (`server/app.py`, `do_GET`): az új
     `/pcb-background.js` útvonalat oda fel kell venni.
3. **Animáció** tisztán CSS-ből (`stroke-dasharray` + `stroke-dashoffset` kulcskockák a
   `styles.css`-ben, a referencia `f6/f10/f14/f20` periódusosztályai szerint):
   - `prefers-reduced-motion: reduce` esetén az impulzusok álljanak meg és tűnjenek el;
   - ha a lap rejtett (`visibilitychange`) vagy megnyílt egy ügy, az animáció álljon le
     (`animation-play-state: paused`), a nyitónézet elhagyásakor az SVG-t el is lehet távolítani;
   - legfeljebb ~120 animált útvonal; a középső chip fényén lehet SVG-blur, az impulzusokon ne.
4. **A biztonsági szabályok maradnak**:
   - a CSP (`style-src 'self'`) nem lazítható: inline `style="…"` és `<style>` nem használható.
     Minden stílus a `styles.css`-be kerül; az elemenkénti animációs időtartamot és késleltetést
     JS-ből, CSSOM-mal kell beállítani (`element.style.animationDuration = …`);
   - nincs `innerHTML`, nincs külső betűtípus vagy CDN, minden fájl helyben van.
5. **Legutóbbi ügyek** (opcionális, ha egyszerűen megoldható): a legutóbb megnyitott ügymappák
   listája a felhasználói profilban (pl. `%APPDATA%\Mail-Scope\recent.json`), legfeljebb 5
   elemmel, ügynév + forrásszám + levélszám + dátum. Új végpont: `GET /api/recent-cases`. Ha egy
   mappa már nem létezik, maradjon ki a listából. Forrásfájlba vagy ügymappába ehhez nem kell írni.
6. **Munkaképernyők:** a levéllista, olvasópanel és beszélgetés-nézet mögé **ne** kerüljön
   animált háttér. Itt legfeljebb a lap nagyon halvány, statikus változata jelenhet meg
   (átlátszatlanság ≤ 0,06), és csak ha nem rontja a szöveg kontrasztját.

## Elfogadási feltételek
- Ügy nélkül indítva a nyitóképernyő jelenik meg, a háttér 1280–2560 px szélességben kitölti a
  képet, és a lap széle sehol nem lóg ki; 390 px szélességen a tartalom egymás alá rendeződik.
- A szöveg kontrasztja a háttér előtt is legalább 4,5:1.
- „Kevesebb animáció” Windows-beállítás mellett nincs mozgás.
- A CSP-fejléc nem változott, a böngészőkonzolban nincs CSP-hiba.
- Ügy megnyitása után nem fut animáció (a CPU-terhelés visszaesik).
- Új tesztek: a szerver kiszolgálja a `pcb-background.js`-t a helyes MIME-típussal; a
  `/api/recent-cases` (ha elkészül) csak létező mappákat ad vissza.
- A meglévő tesztek zöldek; `CHANGELOG.md` bejegyzés; a README képernyőképe frissül kitalált
  adatokkal.

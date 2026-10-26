# Feladat: a funkciók külön lapokra bontása

Készítő: Bálint Szabolcs

## Mostani állapot
A `ui/index.html` egyetlen hosszú oldal, amelyen öt panel van egymás alatt: `case-panel`,
`people-panel`, `conversation-panel`, `archive-panel` és `detailed-search-panel`. Az oldalsáv
linkjei (`#case-panel` stb.) csak görgetnek, a „Személyek” panelnek nincs is linkje, az `app.js`
pedig `scrollIntoView`-val ugrál a panelek között (pl. „Teljes beszélgetés” → beszélgetés panel).

## Cél
Minden funkció egy külön lapon legyen; egyszerre mindig csak egy lap látszik.

| # | Lap | Panel | Útvonal |
|---|-----|-------|---------|
| 01 | Ügy és források | `case-panel` | `#/ugy` |
| 02 | Archívum böngésző | `archive-panel` | `#/archivum` |
| 03 | Beszélgetésnézet | `conversation-panel` | `#/beszelgetes` |
| 04 | Részletes keresés | `detailed-search-panel` | `#/kereses` |
| 05 | Személyek | `people-panel` | `#/szemelyek` |

## Elvárások
1. **Navigáció:**
   - Az oldalsáv linkjei lapváltók legyenek: hash-alapú útvonalak (`#/ugy` stb.), a `hashchange`
     eseményre váltson a nézet. A böngésző Vissza/Előre gombja és az oldal frissítése is a helyes
     lapot mutassa.
   - Az aktív link kapja meg az `active` osztályt és az `aria-current="page"` attribútumot.
   - Lapváltáskor a fókusz a lap címsorára kerüljön, és a fejléc `page-title` szövege a lap neve
     legyen.
   - Ismeretlen útvonal esetén a program az „Ügy és források” lapra váltson.
2. **Megjelenítés:**
   - A nem aktív paneleket a `hidden` attribútum rejtse el (nem elég átlátszóvá tenni).
   - Minden stílus a `styles.css`-be kerüljön, inline stílus nem lehet: a CSP-n (`style-src
     'self'`) nem szabad lazítani.
3. **Állapot:** lapváltáskor ne vesszenek el a beírt szűrők, a kijelölt levél, a betöltött
   beszélgetés és a keresési feltételek. Elég a DOM-ot megtartani, újratöltés nem kell.
4. **Ugrások a lapok között:** a mostani `scrollIntoView` hívásokat lapváltásra kell cserélni:
   - „Teljes beszélgetés” az olvasópanelből → Beszélgetésnézet lap a két személlyel kitöltve;
   - „Megnyitás beszélgetés-nézetben” a keresési találatokból → Beszélgetésnézet lap;
   - találat vagy idővonal-elem megnyitása → Archívum lap, kijelölt levéllel.
5. **Ügy nélkül:** ha nincs megnyitott ügy, a 02–05. lapok linkjei legyenek letiltva
   (`aria-disabled="true"`, magyarázó `title`). Közvetlen URL-lel megnyitva a program az
   „Ügy és források” lapra irányítson. Ha a nyitóképernyő (`docs/feladatok/nyak-hatter.md`) már
   elkészült, ügy nélkül az jelenjen meg.
6. **Indexelés közben** az oldalsávon látsszon egy kis folyamatjelző a „01” mellett, akármelyik
   lap aktív, hogy ne kelljen visszamenni ellenőrizni.
7. **Billentyűzet:**
   - `Ctrl+1` … `Ctrl+5` váltson lapot, kivéve ha a fókusz beviteli mezőben van;
   - a Tab-sorrend a lapon belül maradjon logikus;
   - a rejtett lapok elemei ne kapjanak fókuszt.
8. **Mobil (390 px):** az oldalsáv vízszintes, görgethető lapsávvá alakuljon a tartalom felett.

## Elfogadási feltételek
- **Lapok:** mind az öt lap elérhető az oldalsávból és a közvetlen URL-jével is; frissítés után
  ugyanaz a lap marad aktív.
- **Állapot és ugrások:** lapváltás után a visszatérő lapon megvan a korábbi állapot; a 4. pont
  ugrásai a megfelelő lapra visznek a megfelelő adattal.
- **Hozzáférhetőség:** a rejtett lapok tartalmát a képernyőolvasó nem olvassa fel; a böngésző
  konzoljában nincs CSP-hiba.
- **Tesztek:** új tesztek a szerver felé nem kellenek (a változás csak a felületet érinti), de a
  meglévők maradjanak zöldek.
- **Dokumentáció:** `CHANGELOG.md` bejegyzés; a README képernyőképe és leírása frissül.

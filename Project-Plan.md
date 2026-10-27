# Mail-Scope – levélnéző elemzőknek

Készítő: **Bálint Szabolcs**

A Mail-Scope önálló levélnéző és -elemző program. **Közvetlenül olvassa be a postafiókfájlokat**
(Outlook PST/OST, MBOX) és az ilyeneket tartalmazó mappákat, nem kell előtte semmit kibontani.
Böngészni, keresni és elemezni lehet vele a leveleket. A fő újdonság a **beszélgetés-nézet**:
két vagy több személy összes levelezése egy helyen, időrendben, akkor is, ha a levelek több
postafiókból származnak.

Testvérprogram: **Mail Extractor** (github.com/DarkHeadShadow/Mail-Extractor). Az olvasómotorjai
(`pypff_engine.py`, `mbox_engine.py`) kiindulópontként átvehetők, de a Mail-Scope nem függ tőle.

Képernyőtervek: https://claude.ai/artifact/Uoqja3me1AtgpwW81isiqW
(1 · Archívum böngésző, 2 · Beszélgetés-nézet, 3 · Részletes keresés)

## Alapelvek
- **A forrásfájlokat soha nem módosítja.** A PST/OST/MBOX fájlokat csak olvasásra nyitja meg.
  Beolvasás előtt és után SHA-256-ot számol rájuk, és naplózza, hogy változatlanok maradtak.
  Minden saját adat (index, címkék, mentett keresések, napló) az ügy `.mailscope` mappájába,
  SQLite fájlba kerül.
- **Nem bont ki mindent lemezre.** Az index a levél helyét tárolja a forrásban
  (PST/OST: elem-azonosító és mappaútvonal; MBOX: bájt-eltolás és hossz). A teljes levél, a
  törzs és a mellékletek megnyitáskor, igény szerint töltődnek be közvetlenül a forrásból.
  EML exportra csak külön kérésre kerül sor (export funkciók).
- **Offline.** Nincs hálózati kapcsolat, nincs külső CDN, minden helyben fut.
- **Windows 10/11 x64, Python 3.13**, PyInstallerrel buildelhető.
- A felület nyelve magyar.
- Minden Python forrás- és tesztmodul tartalmazza a
  `__author__ = "Bálint Szabolcs"` szerzői metaadatot; a dokumentáció és a
  felhasználónak látható részek is tüntessék fel a készítőt.

## Bemenet
Egy **ügy** több forrásból állhat (pl. Antal PST-je + Anna MBOX-a + Béla OST-je).
Forrás lehet:
- `.pst`, `.ost` fájl: olvasás **libpff**-fel (`libpff-python-windows`, a NOTICE fájllal együtt).
  Az eredeti internetes fejlécek (Received, DKIM, X-…) megtartása, HTML törzs, RTF tartalék,
  címzettek a recipient táblából. A nem levél jellegű elemek (kontakt, naptár, feladat, jegyzet)
  külön típusként indexelődnek, és szűrhetők.
- `.mbox`, `.mbx`, `.mbs` vagy kiterjesztés nélküli MBOX fájl.
- **Mappa**, amelyet a program rekurzívan bejár, és minden felismert forrást felvesz:
  - Thunderbird profil (`Inbox` + `Inbox.sbd\…` mappastruktúra; törölt, de nem tömörített
    levelek `X-Mozilla-Status` 0x0008 alapján jelölve),
  - Apple Mail export (`Inbox.mbox\mbox`, beágyazott `.mbox` mappák),
  - Gmail Takeout MBOX (`X-Gmail-Labels` címkék megtartva, egy levél több címkével),
  - a benne talált PST/OST fájlok,
  - opcionálisan `.eml` fájlok és a Mail Extractor kimeneti mappái (`*_PST_EXTRACT` stb.).
- A forrás felismerése tartalom alapján történik (PST/OST fejléc-aláírás, MBOX `From ` sor),
  nem csak a kiterjesztés alapján.

## Javasolt felépítés
- `sources/` – forrásolvasók közös felülettel (`iter_folders()`, `iter_messages()`,
  `open_message(locator)`, `open_attachment(locator, n)`):
  - `pst_reader.py` (libpff), `mbox_reader.py`, `eml_reader.py`, `discover.py` (mappabejárás és felismerés).
  - Sérült elemeknél nem áll le: naplóz, és az elemet „hibás” jelzéssel indexeli.
- `indexer/` – a forrásolvasókból SQLite-ba ír: levelek, címek, résztvevők (from/to/cc/bcc),
  mellékletek metaadatai, mappa, forrás, lokátor, Message-ID / In-Reply-To / References,
  levelenkénti tartalom-hash. Teljes szöveges keresés: **SQLite FTS5**
  (tárgy + törzs + mellékletnevek; ügyenként kapcsolhatóan a mellékletek szövege is, lásd a
  4. képernyőt).
  - Háttérben fut, folyamatjelzővel.
  - Megszakítható és folytatható.
  - Új forrás hozzáadásakor csak azt indexeli.
- **Duplikátum-összevonás:** azonos Message-ID (tartalék: normalizált fejlécek + törzs hash)
  egy levél, több forrás-előfordulással.
- **Személyek:** több e-mail-cím egy személyhez rendelhető. Minden szűrés személyre megy,
  nem címre. PST/OST-ben az Exchange (`/O=…/CN=…`) címeket SMTP-címre kell feloldani, ahol
  lehetséges.
- A személyek **automatikusan** jönnek létre (a felhasználó 2026-10-26-i döntése): indexelés
  után és ügy megnyitásakor minden még hozzá nem rendelt cím személyhez kerül; az azonos
  (normalizált, leggyakoribb) megjelenő nevű címek egy „automatikus” személyhez. Az automatikus
  személyek szerkeszthetők (átnevezés, összevonás, cím hozzáadása/áthelyezése, leválasztás,
  eltávolítás, törlés); az első kézi módosítás után „kézi” személlyé válnak, és az automatika
  többé nem nyúl hozzájuk. Eltávolított vagy törölt személyhez tartozó címet az automatika nem
  rendel újra.
- A személyhez rendelés nem módosítja a forrásleveleket, és az e-mail-címekhez pontosan egy
  személy rendelhető.
- **Szálak:** In-Reply-To / References alapján, tartalékként normalizált tárgy (Re:/Fw:/VS: le);
  PST/OST-ben a ConversationIndex is felhasználható.
- `server/` – helyi webszerver (pl. FastAPI vagy a standard library), csak 127.0.0.1-en.
- `ui/` – böngészős felület a tervek alapján (sötét, zöld kiemelés, IBM Plex betűk helyben).

## Funkciók képernyőnként
0. **Ügy és források:** új ügy létrehozása, forrás hozzáadása fájlként vagy mappaként
   (felismert források listája típussal, mérettel, SHA-256-tal), indexelés indítása,
   folyamat és hibanapló.
1. **Archívum böngésző:** forrásonkénti mappafa (az eredeti PST/MBOX mappastruktúra),
   levéllista (rendezés), olvasópanel, gyorsszűrők (melléklet, címke, törölt nem tömörített,
   duplikátumok elrejtése, elemtípus), forenzikus blokk (forrásfájl és belső útvonal / eltolás,
   Message-ID, levél-hash, másik forrásbeli egyezés), „Teljes beszélgetés” gomb a feladó és
   címzett között.
2. **Beszélgetés-nézet:** 2+ személy kiválasztása. Módok:
   - *csak egymás között*;
   - *közös levelek (másokkal együtt is)*;
   - *bármelyikük összes levele*.

   További elemek:
   - CC/BCC kapcsoló, időszak;
   - idővonal napelválasztókkal és „X nap csend” jelzéssel;
   - személyenként szín és oldal;
   - havi aktivitás-sáv (kattintásra ugrás);
   - keresés a beszélgetésben kiemeléssel;
   - téma(szál)-szűrő;
   - export PDF és EML ZIP + SHA-256 lista.
3. **Részletes keresés:**
   - Feltételépítő (ÉS/VAGY, csoportok) és szöveges lekérdezés
     (`from:"…" AND to:"…" AND text:(a OR b) AND date:2023-01-01..2023-12-31 AND has:attachment`).
   - Szűkítés feladó / hónap / mappa / forrás / melléklettípus szerint.
   - [x] Mellékletjelzés (gemkapocs + darabszám, `title`/`aria-label` a legfeljebb öt névvel)
     a találatokban, az Archívum levéllistájában és a Beszélgetésnézet buborékjain, valamint
     „Csak melléklettel” kapcsológomb (`aria-pressed`), amely ÉS kapcsolattal az egész
     lekérdezésre vonatkozik, VAGY csoportok mellett is; a mentett keresés és minden export
     (CSV, EML ZIP, PDF) megőrzi. A CSV „Mellékletek száma” és „Mellékletek neve” oszlopot kap.
     A gyanús mellékletek színjelzése a melléklet-nézegetővel együtt készül el
     (`docs/feladatok/kereses-mellekletjelzes.md`).
   - Mentett keresések, címkézés.
   - Export CSV, EML ZIP + SHA-256, PDF jelentés.
   - „Megnyitás beszélgetés-nézetben”.
4. **Melléklet-nézegető** (részletek: `docs/feladatok/melleklet-nezegeto.md`, tervek:
   `docs/design/melleklet-nezegeto-*.dc.html`):
   - beépített, biztonságos előnézet: PDF, Word (DOCX; DOC csak LibreOffice-szal), Excel
     (XLSX/XLS/CSV, rejtett lapokkal és képletekkel), szöveges fájlok, képek (EXIF-fel),
     csatolt levelek (EML, MSG), tömörített fájlok (ZIP, 7z; RAR telepített 7-Zip-pel)
     kicsomagolás nélkül, beágyazott fákkal;
   - ha a gépen van LibreOffice, „Pontos nézet” az Office-fájlokhoz;
   - semmi nem fut a mellékletből, nincs külső programban megnyitás; csak mentés SHA-256-tal
     és eredetleírással;
   - elemzői adatlap: valódi típus és eltérés a megadottól, SHA-256, hely a forrásban,
     ugyanez a fájl más levelekben, dokumentum-metaadatok, jelzések (rejtett lapok, makró,
     PDF-JavaScript, GPS);
   - a mellékletek szövege ügyenként kapcsolhatóan kereshető.

## Mérföldkövek
1. [x] Forrásolvasók: MBOX (mappák, Thunderbird, Apple Mail, Gmail Takeout) és PST/OST (libpff),
   forrásfelismerés, tesztek kis mintafájlokkal a `tests/` alatt.
2. [x] Indexelő + SQLite séma + FTS5, háttérindexelés, folytatás, forrás-hash ellenőrzés.
3. [x] Személyek és címek összevonása, duplikátumok, szálak.
4. [x] Helyi, `127.0.0.1` címre kötött szerver + Ügy és források képernyő +
   Archívum böngésző. A case- és forrásfelismerés, háttérindexelés, mappa-/levéllista,
   szűrők és forenzikus megnyitás működik; a kiválasztott levél az eredeti forrásból
   töltődik be, SHA-256 ellenőrzés után. Indítás: `py -3.13 -m server`.
5. [x] Beszélgetés-nézet: legalább két kézzel összerendelt személy kiválasztása;
   egymás közti, közös és bármelyik kiválasztott személyt érintő levelek szűrése;
   CC/BCC- és dátumkapcsolók, szál- és teljes szöveges keresés, személyszínek,
   havi aktivitás és napokra bontott idővonal csendjelzéssel. A nyomtatási nézetből
   PDF menthető, az EML ZIP SHA-256 jegyzéket tartalmaz; export előtt az eredeti
   források hash-e ellenőrzött. MBOX/EML esetén a forrás levélbájtjai kerülnek az
   exportba (az MBOX envelope sor nélkül), PST/OST esetén az elérhető adatokból
   rekonstruált EML, mellékletekkel.
6. [x] Részletes keresés, címkék, mentett keresések: legfeljebb tíz, ÉS/VAGY
   operátorral összekapcsolt feltételcsoport és összesen húsz mező; teljes szöveg,
   tárgy, feladó, címzett, dátum, melléklet, forrás, mappa, melléklettípus és címke
   szerinti szűrés; lapozható, duplikátum-összevont eredménylista; több forráspéldányra
   érvényes címkézés; a találat résztvevőiből indítható beszélgetésnézet; ügyenkénti
   mentés, betöltés és törlés. A címkék és keresések
   kizárólag az ügy SQLite-adatbázisában tárolódnak.
7. [x] Exportok és csomagolás: részletes keresési találatok CSV-be (Excel-kompatibilis,
   képletbiztonságos UTF-8 BOM-mal) és MBOX/EML forrásbájtokból vagy PST/OST
   rekonstrukcióból létrejövő EML ZIP-be, SHA-256 jegyzékkel;
   keresési jelentés nyomtatása/PDF-be mentése a böngészőből. A teljes EML-export
   forrás-hash ellenőrzéssel működik, méret- és találati korlátokkal. Elkészült a
   Python 3.13 x64 / Windows onedir PyInstaller build, a fejlesztői és build
   követelményfájl, a `run_dev.bat`, a `build_windows.bat`, a `0.1.0` verziómetaadat,
   a changelog és az angol/magyar README. A PDF-jelentés legfeljebb 2000 találatot
   tartalmaz; a kitalált adatokkal készült keresési képernyőkép megtalálható a
   `docs/screenshots/` mappában. A projektlicenc kiválasztása a kiadás előtt pótlandó.
8. Felkészítés a GitHubra (lásd lent), első kiadás.
9. Melléklet-nézegető a 4. képernyő szerint (`docs/feladatok/melleklet-nezegeto.md`):
   formátumonkénti előnézet elkülönített, időkorlátos feldolgozással, opcionális LibreOffice,
   elemzői adatlap, mentés, kapcsolható melléklet-szöveg az FTS-ben.

## Felkészítés a GitHubra
Ha a program elkészült (és menet közben is, minden mérföldkőnél), a repó legyen feltölthető
a GitHubra. Javasolt név: `DarkHeadShadow/Mail-Scope`, először privátként.
- **Git:** `git init`, `main` ág, értelmes, mérföldkövenkénti commitok. Push csak a felhasználó
  kérésére.
- **`.gitignore`:** `.venv/`, `__pycache__/`, `build/`, `dist/`, `*.spec` kimenetei, `.pytest_cache/`,
  `.mailscope/`, ügymappák, naplók, valamint minden `*.pst`, `*.ost`, `*.mbox`, `*.eml`, kivéve
  a `tests/fixtures/` alatti mintafájlokat. A Mail Extractorban ez hiányzott:
  ott a `build/` és `__pycache__/` felkerült a repóba, ezt itt kerülni kell.
- **Nincs valós adat a repóban:** a tesztekhez csak saját készítésű, kitalált nevű és című
  mintalevelek (pl. Teszt Antal, Teszt Anna, `@pelda.hu`), valódi levél vagy postafiók soha.
  Commit előtt ellenőrizni kell, hogy nincs-e benne jelszó, token, személyes adat vagy abszolút
  helyi útvonal.
- **Licenc:** a projekt licencét a felhasználó választja ki a kiadás előtt, Claude ne döntse el
  helyette. A libpff LGPL-licencű: a `LICENSES/NOTICE_libpff.txt` és a harmadik féltől származó
  csomagok listája (`THIRD_PARTY_NOTICES.md`) kötelezően benne legyen.
- **Dokumentáció:** `README.md` (angol) és `README_HU.md` (magyar): mit tud, képernyőképek
  kitalált adatokkal, telepítés, használat, build, korlátok. `CHANGELOG.md`,
  verziószám a programban (`__version__`), szemantikus verziózás (első kiadás: `v0.1.0`).
- **Függőségek rögzítve:** `requirements.txt` (futtatás), `requirements-dev.txt` (tesztek),
  `requirements-build.txt` (PyInstaller), lehetőleg pontos verziókkal.
- **Fejlesztői környezet:** `.vscode/` (launch, tasks: venv létrehozása, tesztek, build),
  `run_dev.bat`, `build_windows.bat` a Mail Extractor mintájára.
- **CI:** Elkészült a `.github/workflows/tests.yml` Windows futtatóval és Python 3.13-mal:
  tesztek minden `main` push és pull request esetén; címkézett verziónál (`v*`) PyInstaller
  build és a ZIP-elt `dist\Mail-Scope` csatolása a GitHub Release-hez.
- **Ellenőrzőlista a feltöltés előtt:** minden teszt zöld, a build lefut, a README naprakész,
  a `git status` tiszta, nincs nagy bináris vagy valós adat a commitokban.

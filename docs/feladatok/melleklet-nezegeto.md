# Feladat: melléklet-nézegető

Készítő: Bálint Szabolcs

## Cél
Az elemző a mellékleteket a programon belül, biztonságosan nyithassa meg. Semmilyen tartalom ne
fusson, és semmi ne kerüljön ki a gépről. Cél továbbá, hogy a mellékletek szövege kereshető
legyen.

Tervek a `docs/design/` mappában:
- `melleklet-nezegeto-pdf.dc.html` – PDF-nézet;
- `melleklet-nezegeto-excel-zip.dc.html` – Excel rejtett munkalappal, ZIP és csatolt levél a
  mellékletfában, gyanús EXE.

A tervek a tervvászon forrásai, nem futtatható oldalak: elrendezést, színeket és szövegeket
veszünk át belőlük.

## A felhasználó döntései (2026-10-26)
1. **Office-fájlok:** beépített előnézet, és ha a gépen van LibreOffice, mellette pontos
   („Pontos nézet”) megjelenítés is.
2. **Külső program:** nincs. Csak beépített nézet és mentés.
3. **Melléklet-szöveg keresése:** igen, ügyenként kapcsolható.
4. **Formátumok:**
   - PDF, Word, Excel, TXT (és rokonaik);
   - képek;
   - csatolt levelek (EML, MSG);
   - tömörített fájlok (ZIP, 7z, RAR).
   - PowerPoint és RTF nem kell, de LibreOffice-szal a „Pontos nézet” ezeket is megjeleníti, ha
     a gépen van LibreOffice.

## Alapelvek
- **Csak olvas, a forrásból:**
  - a melléklet a forrásolvasó `open_attachment()`-jéből jön, a memóriában;
  - a forrásfájl és az indexelt adatok nem változnak.
- **Semmi nem fut:**
  - nincs makró, PDF-JavaScript, külső hivatkozás vagy távoli kép;
  - nincs „megnyitás külső programmal”;
  - futtatható fájl (EXE, DLL, MSI, BAT, PS1, JS, VBS, LNK, SCR…) nem kap előnézetet, csak
    adatlapot és mentést.
- **Elkülönített feldolgozás:**
  - a mellékletek elemzése (PDF, Office, kép, archívum) külön munkafolyamatban
    (`multiprocessing`) fusson, időkorláttal (alapérték 30 s) és memóriakorláttal;
  - ha egy sérült vagy rosszindulatú fájl miatt összeomlik vagy elakad, a szerver és a felület
    zavartalanul működjön tovább, és magyar hibaüzenet jelenjen meg.
- **Gyorsítótár:**
  - az előállított előnézetek (oldalképek, HTML, szöveg) az ügy
    `.mailscope\cache\attachments\<sha256>\` mappájába kerülnek;
  - a LibreOffice ideiglenes fájljai a `.mailscope\tmp\` mappába, és használat után törlődnek;
  - máshova nem írunk.
- **Offline, CSP:**
  - nincs CDN;
  - a fő CSP egyetlen bővítést kap: `frame-src 'self'` a homokozott előnézethez (lásd lent).

## Formátumok és megjelenítés

| Típus | Beépített nézet | Könyvtár (licenc) | „Pontos nézet” |
| --- | --- | --- | --- |
| PDF | Oldalképek a szerveren renderelve, nagyítás, oldalléptetés, szövegpanel, keresés kiemeléssel | `pypdfium2` (Apache-2.0 / BSD-3) | – |
| DOCX | Formázott szöveg HTML-ként: címsorok, listák, táblázatok, képek, lábjegyzetek | `mammoth` (BSD-2) | LibreOffice → PDF |
| DOC (régi) | Csak LibreOffice-szal; enélkül adatlap + „Ehhez a formátumhoz LibreOffice kell” | – | LibreOffice → PDF |
| XLSX, XLSM | Táblázat munkalap-fülekkel, képletsor, tárolt érték és képlet nézet, rejtett lapok, sorok és oszlopok jelölve, cellamegjegyzések | `openpyxl` (MIT) | LibreOffice → PDF |
| XLS (régi) | Táblázat munkalapokkal (értékek) | `xlrd` (BSD) | LibreOffice → PDF |
| CSV | Táblázat, kódolás- és elválasztó-felismeréssel | standard `csv` | – |
| TXT, LOG, XML, JSON, HTML, EML-forrás | Szöveg sorszámokkal, kódolás-felismeréssel (pl. Windows-1250); HTML-ből tisztított nézet + „Forrás” fül | `charset-normalizer` (MIT), `nh3` (MIT) | – |
| Képek: JPG, PNG, GIF, BMP, WEBP, TIFF (többoldalas) | Nagyítás, görgetés, oldalak, EXIF-adatok; a GPS-koordináta szövegként, térkép nélkül | `Pillow` (MIT-CMU) | – |
| EML, MSG | Levélként, az olvasópanel formájában (fejlécek, törzs, saját mellékletek) | standard `email`; MSG-hez `olefile` (BSD-2) + saját MAPI-olvasó | – |
| ZIP | Tartalomjegyzék fában; a benne lévő fájl ugyanígy megnyitható | standard `zipfile` | – |
| 7z | Mint a ZIP | `py7zr` (LGPL-2.1+) | – |
| RAR | Mint a ZIP, ha a gépen van 7-Zip (`7z.exe`); enélkül adatlap + figyelmeztetés | telepített 7-Zip | – |

**Licencek:**
- Az `extract-msg` GPL-3 licencű, ezért ne használd: ütközne a projekt MIT-licencével.
- Minden új csomag kerüljön be a `THIRD_PARTY_NOTICES.md`-be. Az LGPL-es `py7zr` a libpff-hez
  hasonlóan kezelendő.
- A függőségek pontos verzióval kerüljenek a `requirements.txt`-be.

**LibreOffice (opcionális):**
- Felismerés a szokásos helyeken (`C:\Program Files\LibreOffice\program\soffice.exe`, x86 változat),
  vagy a beállításokban megadott útvonalon.
- Konvertálás:
  `soffice --headless --norestore --convert-to pdf --outdir <.mailscope\tmp\…> <fájl>`
  - saját, eldobható felhasználói profillal (`-env:UserInstallation=file:///…`);
  - 60 s időkorláttal.
- A makróbiztonság ilyenkor magas, makró nem fut. Ezt egy teszt ellenőrizze egy makrós minta
  `.docm`-mel.
- Az eredmény PDF-ként, a PDF-nézővel jelenik meg, a gyorsítótárban tárolva.
- Ha nincs LibreOffice, a „Pontos nézet” gomb letiltva, magyarázattal.

**Korlátok:**
- Előnézet legfeljebb 200 MB-os mellékletig, nagyobbnál csak adatlap és mentés.
- Szövegnézet legfeljebb 5 MB-ig, a többi „Teljes szöveg mentése”-vel érhető el.
- Táblázatnézet munkalaponként az első 2000 sor, lapozással tovább.
- Archívumok:
  - legfeljebb 5 szint mélyen;
  - legfeljebb 10 000 bejegyzés;
  - a kibontott méret legfeljebb 2 GB, a tömörítési arány legfeljebb 100:1, különben
    „lehetséges ZIP-bomba” figyelmeztetés, és a művelet leáll.
- Jelszavas ZIP/7z: a tartalomjegyzék látszik (ha a formátum engedi). Az elemző megadhat egy
  ismert jelszót; az nem tárolódik, csak a munkamenetig él.

## Elemzői adatlap (minden mellékletnél, jobb oldali panel)
- **Fájl:**
  - név, méret;
  - megadott MIME-típus és a tartalom alapján felismert valódi típus. Eltérésnél jól látható
    figyelmeztetés, pl. `fizetes_reszletek.pdf.exe` vagy egy PDF-nek nevezett ZIP;
  - SHA-256;
  - hely a forrásban (forrás › mappa › levél › melléklet › archívum-útvonal);
  - „Ugyanez a fájl máshol”: más levelek ugyanilyen SHA-256-ú mellékletei.
- **Dokumentum-metaadatok:** szerző, utolsó mentő, létrehozás és módosítás ideje, készítő
  program (Office core.xml / app.xml, PDF Info, EXIF).
- **Elemzői jelzések:**
  - Excel: rejtett és nagyon rejtett munkalapok, rejtett sorok és oszlopok, képletek és
    megjegyzések száma, külső hivatkozások;
  - Office: makró (vbaProject.bin, .docm/.xlsm);
  - PDF: beágyazott JavaScript, beágyazott fájlok, űrlapmezők, digitális aláírás jelenléte;
  - kép: GPS-adat.
  - Csak jelzés; az aláírást nem ellenőrizzük.

## Felület
- **Megnyitás innen:**
  - az olvasópanel mellékletlistájából;
  - a beszélgetés-nézet buborékjaiból;
  - a részletes keresés találataiból (ha a találat mellékletben van, azon az oldalon nyílik meg).
- **Elrendezés (a tervek szerint):**
  - fejléc: vissza a levélhez, levél-összefoglaló, „3 / 7” léptetés, keresés a dokumentumban,
    nagyítás, „Mentés…”;
  - bal oldalt a levél mellékletfája (archívumok és csatolt levelek kibontható ágakkal);
  - középen az előnézet;
  - jobb oldalt az adatlap.
- **Homokozott előnézet:**
  - a HTML-alapú előnézet (DOCX, tisztított HTML) `<iframe sandbox>`-ban jelenjen meg;
  - a saját válaszfejléce:
    `Content-Security-Policy: sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; frame-ancestors 'self'`;
  - így a dokumentum stílusai működnek, de szkript és külső tartalom nem.
- **Billentyűk:** `Esc` vissza, `←`/`→` előző és következő melléklet, `Ctrl+F` keresés a
  dokumentumban, `+`/`−` nagyítás.
- **Mentés:**
  - natív mentési párbeszéd, a fájlnév megtisztítva;
  - mellé egy `<fájl>.sha256` és egy rövid eredetleírás (forrás, levél, hely);
  - bejegyzés az ügynaplóba.
- **„Összes mentése”:** ZIP a levél összes mellékletével + `MANIFEST.txt` (név, SHA-256, hely,
  valódi típus).

## Melléklet-szöveg a keresésben (ügyenként kapcsolható)
- **Beállítás:**
  - az ügy létrehozásakor, a hash-ellenőrzési módhoz hasonlóan, időbecsléssel kérdezzen rá;
  - később a beállításokban átkapcsolható;
  - utólagos bekapcsoláskor csak a mellékletek szövegét dolgozza fel, a leveleket nem
    indexeli újra (háttérben, folytatható).
- **Kinyerés:**
  - PDF, DOCX, XLSX, XLS, CSV, TXT és rokonai, valamint archívumokban és csatolt levelekben
    lévő ugyanilyen fájlok;
  - mellékletenként legfeljebb 2 MB szöveg.
- **Képes PDF** (nincs szövegréteg): „szöveg nem kinyerhető (OCR nélkül)” jelzés. OCR ebben a
  feladatban nincs.
- **Tárolás:**
  - külön FTS5-tábla (`attachments_fts`: fájlnév, szöveg, oldal- vagy munkalap-jelölés),
    hivatkozva a mellékletre;
  - a melléklet SHA-256 alapján csak egyszer, az ismétlődőket nem dolgozza fel újra.
- **Keresés:** a teljes szöveges keresés, a beszélgetés-nézet és a részletes keresés is
  találjon a mellékletekben. A találatnál látsszon: „Találat a mellékletben:
  Kotberszamitas.xlsx · Számítás lap” vagy „… 3. oldal”.
- **Részletes keresés:** új mező, „Melléklet szövege”.

## API (javaslat)
- `GET /api/messages/{id}/attachments`
  - fa a beágyazott elemekkel: `path` (pl. `5/0/2`), név, méret, megadott és valódi típus,
    nézettípus, figyelmeztetések.
- `GET /api/attachments/{message_id}/{path}/info` – adatlap és jelzések.
- `GET /api/attachments/{message_id}/{path}/preview?page=&scale=` – oldalkép, HTML vagy
  táblázatadat.
- `GET /api/attachments/{message_id}/{path}/text` – kinyert szöveg.
- `POST /api/attachments/{message_id}/{path}/save` – mentés natív párbeszéddel.
- Nyers fájl a böngészőnek soha nem megy ki inline: csak a mentés írja ki.

## Elfogadási feltételek
- **Minden típusra** kitalált mintafájl a `tests/fixtures/attachments/` alatt:
  - `.docm` makróval;
  - jelszavas ZIP;
  - ZIP-bomba-szerű minta (kis méretben);
  - EXE-nek álcázott PDF;
  - MSG csatolt PDF-fel;
  - JS-t tartalmazó PDF.
- **Mindegyikre teszt.**
- **Egy összeomló vagy végtelen ciklusba kerülő elemző** (szimulált) nem viszi magával a
  szervert, és időkorláton belül hibát ad.
- **A LibreOffice-tesztek** kihagyódnak, ha a CI-gépen nincs LibreOffice. A felismerés és a
  konvertáló hívás egységteszttel, mockolva is legyen lefedve.
- **Kereshetőség:** bekapcsolt melléklet-szöveg mellett egy XLSX-ben és egy ZIP-be csomagolt
  PDF-ben lévő szó kereshető, és a találat a mellékletre mutat.
- **Böngésző:** a konzolban nincs CSP-hiba; a homokozott előnézetben egy beágyazott
  `<script>` nem fut le (teszt).
- **Dokumentáció:**
  - `CHANGELOG.md`, README-k, `THIRD_PARTY_NOTICES.md`;
  - `Project-Plan.md` mérföldkő kipipálva;
  - képernyőképek kitalált adatokkal.

# Mail-Scope – használati útmutató

Készítő: **Bálint Szabolcs** · Verzió: 0.1.1

A Mail-Scope helyben futó levélnéző és -elemző program. Közvetlenül olvassa az Outlook (PST, OST),
Thunderbird, Apple Mail, Gmail Takeout (MBOX) és EML postafiókokat, nem kell előtte semmit
kibontani. A leveleket böngészni, keresni, címkézni és exportálni lehet, a **beszélgetésnézet**
pedig két vagy több személy összes levelezését mutatja időrendben, akkor is, ha a levelek több
postafiókból származnak.

## Tartalom

1. [Alapelvek: mit csinál és mit nem csinál a program](#1-alapelvek)
2. [Indítás és leállítás](#2-indítás-és-leállítás)
3. [Nyitóképernyő: ügy létrehozása és megnyitása](#3-nyitóképernyő)
4. [A munkaterület felépítése](#4-a-munkaterület-felépítése)
5. [01 · Ügy és források](#5-01--ügy-és-források)
6. [02 · Archívum böngésző](#6-02--archívum-böngésző)
7. [03 · Beszélgetésnézet](#7-03--beszélgetésnézet)
8. [04 · Részletes keresés](#8-04--részletes-keresés)
9. [05 · Személyek](#9-05--személyek)
9a. [Melléklet-nézegető](#9a-melléklet-nézegető)
10. [Keresési szintaxis](#10-keresési-szintaxis)
11. [Exportok és a forenzikus jegyzékek](#11-exportok-és-a-forenzikus-jegyzékek)
12. [Forrásintegritás: hash-ellenőrzés](#12-forrásintegritás-hash-ellenőrzés)
13. [Hol tárolja a program az adatokat](#13-hol-tárolja-a-program-az-adatokat)
14. [Billentyűparancsok](#14-billentyűparancsok)
15. [Korlátok](#15-korlátok)
16. [Hibaelhárítás](#16-hibaelhárítás)

---

## 1. Alapelvek

- **A forrásfájlokat soha nem módosítja.** A PST/OST/MBOX/EML fájlokat csak olvasásra nyitja meg,
  és SHA-256 hash-sel ellenőrzi, hogy változatlanok maradtak.
- **Nem bont ki mindent lemezre.** Az index csak a levelek helyét, metaadatait és kereshető szövegét
  tárolja. A levél teljes tartalma megnyitáskor, közvetlenül az eredeti forrásból töltődik be.
- **Offline működik.** Nincs hálózati kapcsolat, nincs felhő. A felület a böngészőben fut, de csak a
  saját gépen (`127.0.0.1`) érhető el.
- **Ügyekben dolgozik.** Minden vizsgálat egy *ügy*, amely egy mappa a lemezen. Egy ügy több
  forrásból állhat (például Antal PST-je + Anna MBOX-a + Béla OST-je). Az ügy minden saját adata
  (index, címkék, mentett keresések, napló) az ügymappa `.mailscope` almappájába kerül.

## 2. Indítás és leállítás

### Telepítés

Futtassa a `Mail-Scope-Setup-<verzió>-x64.exe` telepítőt. A telepítő rendszergazdai jogot kér, és a
programot a `C:\Program Files\Mail-Scope` mappába telepíti, Start menü-bejegyzéssel.

A telepítő két kiegészítőt is felkínál, ha még nincsenek fent a gépen:

- **7-Zip:** ezzel nyílnak meg a RAR-archívumok (és az AES-titkosítású ZIP-ek). Kb. 2 MB.
- **LibreOffice (Still ág):** ezzel működik a **Pontos nézet** (lásd a mellékletnézegetőnél). Kb.
  370 MB, a telepítése néhány percig tart.

Ha bejelöli őket, a telepítő a hivatalos helyről letölti a legfrissebb kiadásukat, ellenőrzi őket
(SHA-256, a LibreOffice-nál a digitális aláírást is), majd csendben telepíti. A LibreOffice-t úgy
telepíti, hogy a Word- és Excel-fájlok továbbra is a Microsoft Office-szal nyíljanak meg, és nem
tesz ikont az asztalra. Ehhez internetkapcsolat kell. Ha a letöltés nem sikerül, a telepítő ezt
jelzi, de a Mail-Scope ettől még feltelepül; a kiegészítők később a hivatalos oldalukról is
telepíthetők, és a Mail-Scope megtalálja őket. A Mail-Scope eltávolítása a 7-Zipet és a
LibreOffice-t nem távolítja el; ezek a Windows „Alkalmazások” listájából külön távolíthatók el.

### Indítás

- **Telepített változat:** indítsa el a Mail-Scope-ot a Start menüből (vagy a `Mail-Scope.exe`-t a
  telepítési mappából). Ha a mappát kézzel másolja, tartsa egyben, mert a felület és a
  licencfájlok is benne vannak.
- **Forrásból:** a projekt mappájában PowerShellben:

  ```powershell
  .\.venv\Scripts\python.exe -m server
  ```

A program elindít egy helyi szervert, és megnyitja az alapértelmezett böngészőben a
`http://127.0.0.1:8765` címet. Ha a böngésző nem nyílik meg, nyissa meg kézzel ezt a címet.

Egyszerre csak egy példány futhat. Ha a program már fut (vagy a port foglalt), a második indítás ezt
jelzi, és kilép.

### Leállítás

Kattintson a jobb felső sarokban a **Leállítás** gombra, és erősítse meg. Ha indexelés fut, a
program jelzi ezt; megerősítés után az indexelés az utolsó mentési pontnál biztonságosan megáll, és
a következő indításkor folytatható. Forrásból futtatva a terminálban `Ctrl+C` is leállítja.

A böngészőfül bezárása **nem** állítja le a programot.

## 3. Nyitóképernyő

Ha nincs megnyitott ügy, a nyitóképernyő jelenik meg.

- **Új ügy létrehozása:** megnyílik a mappaválasztó. Válasszon vagy hozzon létre egy **üres** mappát
  az ügynek (például `E:\Ügyek\2026-014`). A forrásfájlokat ne ebbe a mappába tegye.
- **Ügy megnyitása…:** egy korábbi ügy mappájának kiválasztása.
- **Elérési út megadása:** a gombok alatti mezőbe közvetlenül is beírhatja az ügymappa teljes
  útvonalát, majd **Megnyitás**. Ha a mappa még nem létezik, a program létrehozza.
- **Legutóbbi ügyek:** az utoljára megnyitott (legfeljebb öt) ügy forrás- és levélszámmal. Egy sorra
  kattintva az ügy megnyílik. A már nem létező mappák nem jelennek meg.

A háttér áramköri animációja megáll, ha a böngészőablak rejtett, és eltűnik, amint ügyet nyit meg.
Ha a Windowsban ki vannak kapcsolva az animációk („Animációk megjelenítése” / csökkentett mozgás),
a háttér mozdulatlan marad – ez szándékos.

## 4. A munkaterület felépítése

Megnyitott ügy esetén bal oldalt az oldalsáv öt lapot kínál:

| # | Lap | Mire való |
| --- | --- | --- |
| 01 | **Ügy és források** | források felvétele, indexelés, integritás, ellenőrzési mód |
| 02 | **Archívum böngésző** | levéllista, szűrők, olvasópanel, forenzikus adatok |
| 03 | **Beszélgetésnézet** | személyek közötti levelezés idővonalon |
| 04 | **Részletes keresés** | összetett feltételek, címkézés, mentett keresések, exportok |
| 05 | **Személyek** | e-mail-címek személyekhez rendelése |

- Egyszerre egy lap látszik. Lapváltáskor a beírt szűrők, a kijelölt levél, a betöltött beszélgetés
  és a keresési feltételek megmaradnak.
- Minden lapnak saját címe van (`#/ugy`, `#/archivum`, `#/beszelgetes`, `#/kereses`,
  `#/szemelyek`), így a böngésző Vissza/Előre gombja és a frissítés is a helyes lapot mutatja.
- Indexelés közben az oldalsávon a „01” mellett egy kis forgó jelző látszik, bármelyik lap aktív.
- A fejléc mutatja az ügy nevét és mappáját.
- Keskeny képernyőn az oldalsáv a tartalom fölötti, vízszintesen görgethető lapsávvá alakul.

## 5. 01 · Ügy és források

### Másik ügy megnyitása

A lap tetején az **Ügymappa teljes elérési útja** mezővel és a **Mappa tallózása** / **Ügy
megnyitása** gombokkal bármikor másik ügyre válthat (futó indexelés közben nem).

### Források felismerése

1. A **Postafiókfájl vagy bejárandó mappa** mezőbe írjon be egy fájlt vagy mappát, vagy használja a
   **Fájl tallózása** / **Mappa tallózása** gombot.
2. Kattintson a **Források felismerése** gombra.
3. A **Felismerhető források** lista mutatja a talált forrásokat típussal, mérettel és SHA-256-tal.
   Nagy fájlnál a hash kiszámítása eltarthat egy ideig.

A program a fájlok **tartalma** alapján ismeri fel a formátumot, nem a kiterjesztés alapján:

| Forrás | Megjegyzés |
| --- | --- |
| `.pst`, `.ost` | Outlook. A naptár, névjegy, feladat és jegyzet elemek is indexelődnek, típus szerint szűrhetők. |
| `.mbox`, `.mbx`, `.mbs`, kiterjesztés nélküli MBOX | Thunderbird (`Inbox` + `Inbox.sbd\…` mappaszerkezet), Apple Mail (`Inbox.mbox\mbox`), Gmail Takeout. |
| `.eml` | Egy mappa összes EML fájlja **egyetlen forrás** lesz (például egy Mail Extractor kimeneti mappa). |

- **Thunderbird:** a törölt, de még nem tömörített leveleket a program „törölt” jelzéssel indexeli;
  az archívumban külön szűrhetők.
- **Gmail Takeout:** a Gmail-címkék (`X-Gmail-Labels`) a levél címkéi lesznek.
- **Mappa megadásakor** a program a teljes mappát rekurzívan bejárja, és minden felismert forrást
  felvesz. A mappaszerkezetet megtartja.

### Indexelés

1. A felismert források listájában pipálja ki az indexelendőket.
2. Kattintson az **Indexelés indítása** gombra.

- Az indexelés a háttérben fut, a felület közben használható. A folyamatsáv a forrás sorszámát, a
  vizsgált levelek számát és az aktuális tárgyat mutatja.
- A **Megszakítás** gomb biztonságosan leállítja; az eddig mentett levelek megmaradnak. Ugyanazt a
  forrást újra indexelve a program ott folytatja, ahol abbahagyta.
- Egy már indexelt, változatlan forrást a program kihagy. Új forrást bármikor hozzáadhat; ilyenkor
  csak az új forrás indexelődik.
- Ha egy forrás hibás, a program naplózza, „failed” állapotba teszi, és **a többi forrással
  folytatja**. Sérült egyedi leveleket „hibás” bejegyzésként indexel.
- Indexeléskor a program összevonja az azonos leveleket (duplikátumokat), például a küldő Elküldött
  elemeiben és a címzett Beérkezett üzenetei között lévő példányt. Az összevonás alapja a
  Message-ID, ennek hiányában a fejlécek és a törzs hash-e.

Tájékoztató sebesség: egy 830 MB-os PST (kb. 2400 elem) fél perc alatt indexelődik. Az OST
lényegesen lassabb: egy 7 GB-os OST (kb. 25 000 elem) puszta beolvasása is kb. fél óra, ezért
nagy OST-nél számítson ennél hosszabb indexelésre.

### Ügy forrásai

Minden indexelt forrás sora mutatja:

- útvonal, típus, állapot (`COMPLETED`, `FAILED`, `SOURCE_CHANGED`, `CANCELLED`), SHA-256;
- **Utolsó teljes ellenőrzés:** dátum, idő és eredmény (egyezik / ELTÉR);
- **Teljes újraellenőrzés** gomb – lásd a [12. fejezetet](#12-forrásintegritás-hash-ellenőrzés).

### Forrásellenőrzés levélmegnyitás előtt (ügyenkénti döntés)

Az első megnyitáskor egy kiemelt kártya kéri, hogy válasszon **gyors** vagy **szigorú**
ellenőrzési mód között. A két lehetőség és következményeik a kártyán olvashatók; a részleteket a
[12. fejezet](#12-forrásintegritás-hash-ellenőrzés) írja le. A döntés az ügyben megmarad, és bármikor
megváltoztatható. Döntésig a gyors mód érvényes.

### Melléklet-szöveg keresése (ügyenkénti döntés)

Alatta egy második kártya kérdezi meg, hogy a keresés a **mellékletek szövegében** is keressen-e.
A kártya becsli, mennyi ideig tart a feldolgozás (a mellékletes levelek számából; új ügyben az
indexelés után látszik). A döntés az ügyben megmarad, és bármikor megváltoztatható. Döntésig ki
van kapcsolva.

- **Bekapcsolva** a program a háttérben újra beolvassa a mellékletes leveleket a változatlan
  forrásból (a leveleket nem indexeli újra), és kinyeri a mellékletek szövegét:
  - PDF (oldalanként), Word (DOCX), Excel (XLSX, XLS; munkalaponként, a rejtett lapokat is), CSV,
    szöveges és HTML fájlok, csatolt levelek (EML, MSG);
  - archívumokban (ZIP, 7z, gzip; RAR telepített 7-Zippel) és csatolt levelekben lévő ugyanilyen
    fájlok; jelszavas archívum belseje nem;
  - mellékletenként legfeljebb 2 MB szöveg.
- Minden fájlt **SHA-256 szerint egyszer** dolgoz fel; ha ugyanaz a fájl több levélben is
  szerepel, mindegyiknél megtalálja.
- A feldolgozás ugyanabban az elkülönített, idő- és memóriakorlátos folyamatban fut, mint a
  melléklet-nézegető, és előtte ellenőrzi a forrás SHA-256-ját. Közben a program használható.
- Megszakítás (kikapcsolás, leállítás, új indexelés) után **ott folytatódik**, ahol abbamaradt.
  A később indexelt levelek mellékleteit az indexelés végén dolgozza fel.
- A **képes (szkennelt) PDF** szövege OCR nélkül nem nyerhető ki; ezt a kártya „szöveg nem
  kinyerhető (OCR nélkül)” jelzéssel számolja.
- A kártya alján az állapot látszik: hány mellékletes levél van feldolgozva, hány fájl szövege
  kereshető, hány képes PDF és hány hiba volt (a hibák részletei az ügy naplójában).
- **Kikapcsolva** nincs háttérfeldolgozás, és a keresés nem nézi a mellékletek szövegét. A már
  kinyert szöveg megmarad, így egy újabb bekapcsolás csak a hiányzó mellékleteket dolgozza fel.

Bekapcsolt melléklet-szöveg mellett az Archívum keresőmezője, a beszélgetéskereső és a részletes
keresés „Teljes szöveg” feltétele a mellékletekben is talál. A találatnál egy sor mutatja, hol:
„**Találat a mellékletben:** Kotberszamitas.xlsx · Számítás lap” vagy „… szamla.pdf · 3. oldal”.
A részletes keresésben a **Találat megnyitása**, a beszélgetésnézetben maga a sor nyitja meg a
mellékletet a nézegetőben, azon az oldalon vagy munkalapon, a keresett szóval a dokumentum
keresőjében.

## 6. 02 · Archívum böngésző

### Szűrők

| Szűrő | Hatás |
| --- | --- |
| **Teljes szövegű keresés** | tárgy, törzs és mellékletnevek; a szintaxist lásd a [10. fejezetben](#10-keresési-szintaxis) |
| **Forrás** | egy forrás levelei |
| **Mappa** | az eredeti postafiókmappa (például Inbox, Sent Items) |
| **Elemtípus** | Levél, Névjegy, Naptár, Feladat, Jegyzet |
| **Mellékletes** | csak mellékletes levelek |
| **Törölt (nem tömörített)** | Thunderbird törölt, de még nem tömörített levelei |
| **Duplikátumok mutatása** | az összevont levelek minden forráspéldánya külön sorban |

A szűrők változtatásakor a lista azonnal frissül; a szöveges keresést a **Keresés** gombbal vagy
Enterrel indítja.

### Levéllista

- A levelek dátum szerint, a legújabb elöl jelennek meg, egyszerre 100; a **Továbbiak betöltése**
  gomb hozza a következőket.
- Minden sor: tárgy, feladó, dátum (`ÉÉÉÉ-HH-NN ÓÓ:PP`, a gép helyi idejében), mappa és forrás.
  A dátum fölé állva az eredeti `Date` fejléc is látszik.
- Mellékletes levélnél a tárgy előtt gemkapocs és a mellékletek száma látszik; fölé állva
  a program legfeljebb öt fájlnevet sorol fel („… és még N”).

### Olvasópanel

Egy levélre kattintva a program az **eredeti forrásból** tölti be a levelet, a beállított
ellenőrzés után.

- **Fejlécek:** FROM, TO, CC, BCC, DATE (helyi időben), MESSAGE-ID.
- **Törzs:** a levél szövege. HTML-levélnél a látható szöveg.
- **Mellékletek:** fájlnév és típus.
- **Címkék:** a levél összes forráspéldányán lévő címke.
- **Forenzikus adatok** minden forráspéldányra: forrásfájl, forrásmappa, lokátor (MBOX-nál
  bájteltolás és hossz, PST/OST-nél mappa és elemsorszám), Message-ID, **eredeti `Date` fejléc**
  változatlanul, a levél SHA-256-ja, a forrás SHA-256-ja, és hogy melyik példányból nyílt meg.
- **Teljes beszélgetés:** megnyitja a Beszélgetésnézetet a feladóval és a címzettekkel – ha a
  címeik két különböző személyhez tartoznak (lásd [9. fejezet](#9-05--személyek)).

## 7. 03 · Beszélgetésnézet

A beszélgetésnézet **személyeket** kapcsol össze, nem egyes e-mail-címeket. A személyek indexelés
után automatikusan létrejönnek; ha kell, a **Személyek** lapon javíthatja őket. A résztvevők
listája fölötti **Személy keresése** mezővel név vagy cím szerint szűrhet.

### Beállítások

1. **Résztvevők:** jelöljön ki legalább két személyt.
2. **Beszélgetés módja:**
   - **Csak egymás között** – csak azok a levelek, amelyekben kizárólag a kijelölt személyek
     szerepelnek, és az egyik a másiknak írt. Az önmagának küldött levél nem számít bele.
   - **Közös levelek (másokkal együtt is)** – minden kijelölt személy szerepel, mások is lehetnek.
   - **Bármelyikük összes levele** – bármelyik kijelölt személyt érintő levél.
3. **Kezdete / Vége:** a vizsgált időszak (mindkét nap beleszámít). Ha valamelyik üres, az időszak
   az első, illetve az utolsó levél napjáig tart; a választott időszak az eredmény fejlécében látszik.
4. **Téma / szál:** egy levélszálra szűkít (a lista a betöltés után telik meg).
5. **Keresés a beszélgetésben:** szöveges szűrés; a találatok kiemelve jelennek meg.
6. **CC/BCC résztvevők bevonása:** kikapcsolva csak a feladó és a közvetlen (To) címzettek számítanak.
7. **Beszélgetés betöltése.**

### Idővonal

- **Havi aktivitás-sáv:** havonta a levelek száma; egy hónapra kattintva odaugrik.
- **Napelválasztók** a helyi idő szerint, és „**X nap csend**” jelzés, ha két levél között 10 napnál
  hosszabb szünet volt. Megadott Kezdete / Vége esetén az időszak eleji és végi szünetet is jelzi.
- **Kártyák:** személyenként saját szín és oldal (bal/jobb); tárgy, szálazonosító, feladó, címzettek,
  időpont, a törzs eleje.
- **Teljes üzenet megjelenítése** kibontja a törzset; **Eredeti levél megnyitása** az Archívum lapon
  nyitja meg, az eredeti forrásból.

### Szálak

A program a levélszálakat a válaszhivatkozások (`In-Reply-To`, `References`) alapján állítja össze.
Ha ezek hiányoznak, egy Re:/Fw:/VS: előtagú levelet csak akkor kapcsol tárgy alapján egy szálhoz, ha
van közös résztvevő; időkorlát nélkül, a dátumban legközelebbi ilyen szálhoz. Ha egy automatikus rendszer minden értesítést ugyanarra
a hiányzó levélre hivatkoztat, a program ezeket tárgy szerint külön szálakra bontja.

### Export

- **EML ZIP + SHA-256** – a beszélgetés leveleit (legfeljebb 500) EML fájlokba csomagolja,
  jegyzékkel (lásd [11. fejezet](#11-exportok-és-a-forenzikus-jegyzékek)).
- **PDF / nyomtatás** – a böngésző nyomtatási ablakát nyitja meg; ott a „Mentés PDF-ként” célt
  választva PDF készül.

## 8. 04 · Részletes keresés

### Feltételépítő

- Egy **feltételcsoporton** belül több feltétel adható meg, ÉS vagy VAGY kapcsolattal
  (**+ Feltétel**).
- Több csoport is felvehető (**+ Feltételcsoport**); a csoportok közötti kapcsolat a
  **Csoportok összekapcsolása** mezőben ÉS vagy VAGY.
- Legfeljebb 10 csoport és összesen 20 feltétel.

| Mező | Érték |
| --- | --- |
| Teljes szöveg | szavak a tárgyban, törzsben, mellékletnevekben, bekapcsolt melléklet-szöveg mellett a mellékletek szövegében is ([szintaxis](#10-keresési-szintaxis)) |
| Tárgy | szövegrész a tárgyban |
| Feladó / Címzett | szövegrész a címben vagy a megjelenő névben |
| Kezdő dátum / Záró dátum | dátum; mindkét határnap beleszámít |
| Melléklet | `yes` (van) vagy `no` (nincs) |
| Forrás útvonala | szövegrész a forrásfájl útvonalában |
| Mappa | szövegrész a postafiókmappa nevében |
| Melléklet típusa | MIME-típus részlete, például `pdf` |
| Melléklet szövege | szavak a mellékletek szövegében ([szintaxis](#10-keresési-szintaxis)); csak bekapcsolt [melléklet-szöveg](#melléklet-szöveg-keresése-ügyenkénti-döntés) mellett |
| Címke | szövegrész a címkében |

A **Keresés** gomb indítja. Mellette a **Csak melléklettel** kapcsológomb (egérrel vagy
billentyűzettel kapcsolható) az egész lekérdezést a legalább egy melléklettel rendelkező
levelekre szűkíti – ÉS kapcsolattal, akkor is, ha a csoportok között VAGY áll. Bekapcsolt
állapotban kiemelt színű; átkapcsoláskor a már lefutott keresés az első oldaltól újra lefut.
A régi „Melléklet” (`yes`/`no`) feltételmező továbbra is használható.

A találatok 50-esével lapozhatók; mellékletes levélnél gemkapocs és a mellékletek száma látszik; az azonos levelek forráspéldányai egy
találatként jelennek meg. Egy találatra kattintva a levél az Archívum lapon nyílik meg; a
**Beszélgetés** gomb a levél feladójával és címzettjével nyitja meg a Beszélgetésnézetet.

### Címkézés

1. Jelölje ki a találatokat a sor eleji jelölőnégyzettel.
2. Írja be a címkét (például `Fontos`) a **Kijelölt levelek címkéje** mezőbe.
3. **Címke hozzáadása** vagy **Címke eltávolítása**.

A címke a levél összes forráspéldányára érvényes, és csak az ügy adatbázisába kerül; a forrásfájlok
nem változnak. A címkékre a „Címke” feltétellel lehet keresni.

### Mentett keresések

- **Keresés mentése:** adjon nevet a keresésnek a **Mentett keresés neve** mezőben.
- **Mentett keresések:** a listából választva a feltételek és a „Csak melléklettel” kapcsoló
  állása visszatöltődik, és a keresés lefut.
- **Mentés törlése:** a kiválasztott mentett keresés törlése.

### Export

Minden export (CSV, EML ZIP, PDF) pontosan azt a halmazt adja, amely a képernyőn látszik,
a „Csak melléklettel” szűréssel együtt.

- **CSV export** – legfeljebb 10 000 találat; Excelben közvetlenül megnyitható (UTF-8), a képletnek
  látszó értékek semlegesítve. A „Mellékletek száma” és „Mellékletek neve” (pontosvesszővel
  elválasztva) oszlop is benne van.
- **EML ZIP + SHA-256** – legfeljebb 500 levél és 256 MiB.
- **PDF / nyomtatás** – nyomtatható jelentés legfeljebb 2000 találatról, a feltételekkel és a
  készítés időpontjával.

## 9. 05 · Személyek

A személyekhez rendelés teszi lehetővé, hogy egy embert több e-mail-címével együtt kezeljen
(például munkahelyi és magáncím). A beszélgetésnézet és a személyre szűrés ezeken alapul.

### Automatikus személyek

Indexelés után (és ügy megnyitásakor) a program **minden még hozzá nem rendelt címet automatikusan
személyhez rendel**:

- a címekhez a levelekben leggyakrabban látott megjelenő nevet veszi (az idézőjeleket és a
  többszörös szóközöket figyelmen kívül hagyva);
- az **azonos nevű címek egy személyhez** kerülnek, például `anna@ceg.hu` és `anna.magan@pelda.hu`,
  ha mindkettő „Teszt Anna” néven szerepel – ide tartozik az Exchange-cím és SMTP-párja is;
- ha egy címhez nincs megjelenő név, a személy neve maga a cím lesz;
- az így létrejött személyek a listában **automatikus** jelölést kapnak.

Az automatika tévedhet: két különböző ember azonos névvel (például „Kovács Péter”) egy személybe
kerülhet, egy ember eltérő írásmódú nevei pedig külön személyekbe. Ezeket a szerkesztővel javítsa.

A **Automatikus csoportosítás most** gomb azonnal lefuttatja a csoportosítást (például új források
indexelése után ez magától is megtörténik).

### Személylista

- A **Szűrés név vagy e-mail-cím szerint** mezővel kereshet (például `anna` vagy `pelda.hu`).
- A fejléc mutatja a személyek számát, és hogy ebből hány automatikus.
- Egy sorra kattintva a személy megnyílik a szerkesztőben.

### Személy szerkesztése

| Művelet | Hatás |
|---|---|
| **Átnevezés** | a személy nevének megváltoztatása (Enter is menti) |
| **Leválasztás új személybe** | a cím egy új, külön személy lesz (a cím megjelenő nevével) |
| **Eltávolítás** | a cím személy nélkül marad, és az automatika sem rendeli újra |
| **Cím hozzáadása** | a gépelés közben felkínált címek közül; ha a cím más személyé, áthelyezi |
| **Összevonás** | a kiválasztott másik személy minden címét ebbe a személybe olvasztja |
| **Személy törlése** | megerősítés után törli a személyt; címei személy nélkül maradnak, és az automatika nem rendeli őket újra |

Az első kézi módosítás után a személy **kézi** lesz: az automatika többé nem ad hozzá címet és nem
változtat rajta. Ha egy személy minden címe máshová kerül, a személy megszűnik.

### Új személy kézzel

1. A **Személy neve** mezőbe írja be a nevet.
2. A **Megfigyelt e-mail-címek** listában jelölje ki a címeket. Az automatikus személyekhez tartozó
   címek is kijelölhetők – ezeket a program átveszi tőlük; a kézi személyek címei nem jelölhetők
   (azokat a szerkesztőben helyezze át).
3. **Személy és aliasok mentése.**

Egy e-mail-cím egyszerre csak egy személyhez tartozhat. A személyhez rendelés a forrásleveleket
nem módosítja.

## 9a. Melléklet-nézegető

A mellékleteket a programon belül, biztonságosan nézheti meg. A mellékletből **semmi nem fut**
(nincs makró, szkript, külső hivatkozás vagy távoli kép), nem nyílik meg külső programban, és a
nyers fájl nem kerül a böngészőbe.

### Megnyitás

- az **olvasópanel** MELLÉKLETEK szakaszában a melléklet nevére kattintva;
- a **Beszélgetésnézet** buborékjain a gemkapocsra kattintva;
- a **Részletes keresés** találatainál a **Mellékletek** gombbal.

A nézegetőnek saját címe van (`#/melleklet/<levél>/<melléklet>`), így a Vissza gomb és a frissítés
is működik. Archívumban vagy csatolt levélben lévő fájlnál a cím a teljes utat tartalmazza, pl.
`#/melleklet/8/1/2/0` = a levél 2. melléklete (egy ZIP) › abban a 3. fájl (egy belső ZIP) › abban
az 1. fájl.

### Felépítés

- **Fejléc:** vissza a levélhez, a levél tárgya, feladója, címzettje és dátuma, a melléklet
  sorszáma („3 / 7”), keresés a dokumentumban, nagyítás, **Mentés…**.
- **Bal oldalt:** a levél mellékletfája; a gyanús mellékletek sárga jelzést kapnak. Az
  archívumok és a csatolt levelek a ▸ jellel kibonthatók, a bennük lévő fájlok beljebb húzva
  látszanak, és ugyanúgy megnyithatók. Alul **Összes mentése (ZIP + jegyzék)**.
- **Középen:** az előnézet.
- **Jobb oldalt:** az elemzői adatlap.

### Előnézetek

| Típus | Megjelenítés |
| --- | --- |
| TXT, LOG, XML, JSON, EML-forrás | szöveg sorszámokkal, kódolásfelismeréssel (pl. Windows-1250), keresés kiemeléssel |
| HTML | tisztított, látható szöveg; a **Forrás** gombbal a HTML-forrás |
| CSV | táblázat elválasztó-felismeréssel, 2000 soros oldalakkal |
| Képek (JPG, PNG, GIF, BMP, WEBP, TIFF) | a kép PNG-be újrakódolva, nagyítással; többoldalas TIFF-nél oldalléptetés; EXIF-adatok, a GPS-koordináta szövegként |
| PDF | oldalkép nagyítással és oldalléptetéssel; az oldal szövegrétege kiemeléssel; a keresés az összes oldalt átnézi („Találat: 2., 5. oldal”); szövegréteg nélküli (szkennelt) PDF-nél jelzés: OCR nélkül nem kinyerhető |
| Word (DOCX, DOCM) | **Formázott** fül: címsorok, listák, táblázatok, képek tisztított, homokozott keretben; **Szöveg** fül a kereséshez |
| Excel (XLSX, XLSM) | munkalapfülek (a rejtett és nagyon rejtett lapok is, jelölve), képletsor a kijelölt cellához, **Képletek** kapcsoló, a rejtett sorok és oszlopok halványan, csíkozva, a megjegyzéses cellák sarokjellel |
| Régi Excel (XLS) | munkalapok értékekkel (a képletek a Pontos nézetben látszanak) |
| Régi Word és PowerPoint (DOC, PPT), PPTX, RTF, OpenDocument | Pontos nézet LibreOffice-szal (lásd lent); enélkül adatlap és mentés |
| HTML | **Formázott** fül tisztítva, homokozott keretben; **Szöveg** és **Forrás** fül |
| ZIP, 7z, RAR, gzip | tartalomjegyzék (fájl, méret, tömörített méret, valódi típus, módosítás, állapot); a fájlok a táblázatból vagy a fából nyithatók meg, kicsomagolás nélkül, a memóriában |
| Csatolt levél (EML, MSG) | fejlécek (feladó, címzett, dátum, tárgy, Message-ID), törzs (**Formázott** fül tisztítva, homokozott keretben, ha van HTML-törzs; **Szöveg** fül), a levél saját mellékletei |
| Futtatható fájl (EXE, DLL, MSI, BAT, PS1, JS, VBS, LNK…) | előnézet nincs, csak adatlap és mentés |

A formázott (Word, HTML) nézet egy elkülönített, homokozott keretben jelenik meg: szkript, űrlap,
navigáció és külső tartalom nem működik benne, a linkek célja eltávolítva. Kereséskor a program
az előnézetet a **Szöveg** fülre váltja.

#### Pontos nézet (LibreOffice)

Ha a gépen telepítve van a LibreOffice, az Office-mellékleteknél (Word, Excel, PowerPoint,
OpenDocument, RTF, régi DOC/XLS/PPT) az előnézet fölött két gomb jelenik meg:
**Beépített nézet** és **Pontos nézet (LibreOffice)**.

- **Mit mutat:** a LibreOffice a dokumentumot PDF-be alakítja, és a program ezt a PDF-nézővel
  jeleníti meg (oldalléptetés, nagyítás, szövegréteg, keresés az összes oldalon). Így az
  oldaltördelés, a betűtípusok és a régi XLS képletei is az eredetihez hasonlóan látszanak.
- **Beépített nézet nélküli formátumok** (régi DOC és PPT, RTF, PPTX, OpenDocument) a Pontos
  nézetben nyílnak meg, ha van LibreOffice. LibreOffice nélkül ezeknél az adatlap és a mentés
  érhető el.
- **Biztonság:** a LibreOffice ablak nélkül fut, a Mail-Scope saját LibreOffice-beállításaival
  (`%LOCALAPPDATA%\Mail-Scope\lo`), amelyekben a makrók le vannak tiltva. A dokumentum makrói
  így nem futnak le, és a gépen lévő LibreOffice saját beállításait a program nem érinti. A
  program minden átalakítás előtt ellenőrzi a makrótiltást, és ha hiányzik, a beállításokat
  újra létrehozza. A dokumentum másolata az ügy `.mailscope\tmp\` mappájába kerül, és az
  átalakítás után törlődik. Az eredmény-PDF a gyorsítótárba kerül, így a második megnyitás
  azonnali.
- **Időtartam:** az első átalakítás kb. 20 másodperc (ekkor készülnek el a LibreOffice
  beállításai), a többi néhány másodperc. Egyszerre egy átalakítás fut.
- **Időkorlát:** egy átalakítás legfeljebb 60 másodpercig tarthat; utána a program leállítja a
  LibreOffice-t, és hibaüzenetet ad. A Beépített nézet ekkor is elérhető.
- **Hol keresi a program:** `C:\Program Files\LibreOffice\program\soffice.exe`, az x86-os
  változat helye, majd a `PATH`. Ha máshova telepítette (vagy hordozható változatot
  használ), a letiltott Pontos nézet gomb melletti **LibreOffice megkeresése…** gombbal
  kiválaszthatja a `soffice.exe` programot. A program ezt a felhasználói profilban
  (`%APPDATA%\Mail-Scope\settings.json`) jegyzi meg, nem az ügyben.

A Mail-Scope a LibreOffice-t nem tartalmazza. A telepítő felkínálja a letöltését (lásd
[Telepítés](#telepítés)), vagy a [libreoffice.org](https://www.libreoffice.org/) oldalról
bármikor telepíthető.

#### Archívumok és csatolt levelek

- **Kibontás a memóriában:** a program az archívumot a forrásból olvassa, és a benne lévő
  fájlokat csak a memóriában bontja ki; lemezre nem ír semmit (a RAR kivételével, lásd lent).
- **Korlátok (ZIP-bomba elleni védelem):** legfeljebb 5 szint mélyen (archívum az
  archívumban), legfeljebb 10 000 bejegyzés, legfeljebb 2 GB kibontott méret és 100:1-es
  tömörítési arány (10 MB felett). Ha egy archívum ezt túllépné, a program **„Lehetséges
  ZIP-bomba”** figyelmeztetéssel leállítja a kibontást; a tartalomjegyzék első 500 sora
  ekkor is látszik. Egy fájl legfeljebb 200 MB-ig, egy archívum összesen 512 MB-ig töltődik be
  a memóriába; a többi fájl csak a listában szerepel.
- **Jelszavas archívum:** a ZIP tartalomjegyzéke jelszó nélkül is látszik (a 7z-é csak akkor,
  ha a fájllista nem titkosított). Az archívum nézetében megadhatja az ismert jelszót
  (**Kibontás jelszóval**). A jelszót a program **nem menti és nem naplózza**: csak a
  program bezárásáig, illetve másik ügy megnyitásáig él a memóriában. Hibás jelszónál
  „A jelszó hibás” üzenet jelenik meg.
- **RAR:** csak akkor bontható ki, ha a gépen telepítve van a 7-Zip (`7z.exe`). Ilyenkor a
  program a RAR-fájlt az ügy `.mailscope\tmp\` mappájába írja, a 7-Zippel ablak nélkül
  kibontja, majd azonnal törli. 7-Zip nélkül csak az adatlap és a mentés érhető el. Az
  AES-titkosítású ZIP-hez is 7-Zip kell.
- **Outlook MSG:** a program saját olvasóval, a fájl MAPI-tulajdonságaiból állítja össze a
  levelet (fejlécek, szöveges és HTML-törzs, mellékletek, beágyazott Outlook-elemek). Ha az
  MSG-ben benne vannak az eredeti internetes fejlécek, azok az irányadók.
- **Gyanús fájl az archívumban:** ha egy archívumban futtatható vagy álcázott fájl van, az a fában
  sárga jelzést kap, az archívum adatlapja megnevezi, és a levél gemkapcsa is sárga lesz.

Legfeljebb 200 MB-os mellékletnél van előnézet; a szövegnézet az első 5 MB-ot mutatja.

### Elemzői adatlap

- **Fájl:** név, méret, megadott típus, a **tartalom alapján felismert valódi típus** (egyezik /
  ELTÉR), SHA-256, hely a forrásban (forrásfájl › mappa › levél › melléklet › archívumon belüli
  út), archívumban lévő fájlnál az útvonal és a módosítás ideje.
- **Archívum:** formátum, bejegyzések száma, kibontott méret, tömörítési arány, titkosított
  fájlok száma; csatolt levélnél a formátum (EML/MSG) és a mellékletek száma.
- **Dokumentum-metaadatok:** szerző, utolsó mentő, cím, létrehozás és módosítás ideje, készítő
  program, oldalak / munkalapok, képletek és megjegyzések száma.
- **Biztonság (PDF):** beágyazott JavaScript, beágyazott fájl, űrlapmezők, digitális aláírás
  (csak jelenlét, az aláírást a program nem ellenőrzi), külső hivatkozás.
- **Képadatok:** méret, EXIF-adatok, GPS-koordináta.
- **Elemzői jelzések:** például futtatható fájl, kettős kiterjesztés (`fizetes_reszletek.pdf.exe`),
  a névtől vagy a megadott típustól eltérő tartalom (PDF-nek nevezett ZIP, PDF-nek álcázott
  program), makró (`vbaProject.bin`), rejtett és nagyon rejtett munkalap, rejtett sorok és
  oszlopok, külső hivatkozás, beágyazott objektum, GPS-helyadat.
- **Ugyanez a fájl máshol:** más levelek ugyanilyen SHA-256-ú mellékletei. A program a már
  megnyitott mellékletek hash-ét ismeri; a lista a mellékletek nézegetésével bővül.

Ha egy melléklet gyanús, a keresési találatok, az Archívum és a Beszélgetésnézet gemkapcsa is
sárga lesz, a fölé álló szöveg pedig megnevezi a gyanús fájlt. A futtatható nevű mellékleteket
a program azonnal jelzi; a tartalom szerinti álcázást a melléklet első megnyitása után.

### Biztonság

- A mellékletek elemzése **külön folyamatban** fut, 30 másodperces időkorláttal és
  memóriakorláttal. Ha egy sérült vagy rosszindulatú fájl miatt az elemzés összeomlik vagy
  elakad, a program magyar hibaüzenetet ad, és zavartalanul működik tovább.
- Az elkészült előnézetek az ügy `.mailscope\cache\attachments\<SHA-256>\` mappájába kerülnek;
  máshova a program nem ír.

### Mentés

- **Mentés…:** natív mentési ablak, megtisztított fájlnévvel. A fájl mellé a program létrehoz egy
  `<fájl>.sha256` és egy `<fájl>.eredet.txt` fájlt (forrás, forrás SHA-256, levél, hely, típusok),
  és bejegyzést ír az ügynaplóba.
- **Összes mentése:** ZIP a levél minden mellékletével és egy `MANIFEST.txt` jegyzékkel
  (név, SHA-256, méret, valódi típus, hely).

### Billentyűk

| Billentyű | Hatás |
| --- | --- |
| `Esc` | vissza oda, ahonnan a nézegetőt megnyitotta |
| `←` / `→` | előző / következő melléklet (archívumon belül az ugyanazon a szinten lévő fájlok között) |
| `Ctrl+F` | keresés a dokumentumban |
| `+` / `−` | nagyítás / kicsinyítés |

## 10. Keresési szintaxis

Az Archívum szövegmezője, a beszélgetéskereső, a „Teljes szöveg” és a „Melléklet szövege” feltétel
ugyanazt a szintaxist használja.

| Beírás | Jelentés |
| --- | --- |
| `szerződés` | szókezdet: megtalálja a „szerződést”, „szerződésben” alakot is |
| `szerződés határidő` | mindkét szó szerepel (ÉS) |
| `"aláírt példány"` | pontos kifejezés |
| `számla OR teljesítés` | bármelyik szó |
| `szerződés NOT tervezet` | az első igen, a második nem |
| `(számla OR díj) AND 2023` | zárójeles csoportosítás |
| `anna@pelda.hu` | cím mint kifejezés |

- A kis- és nagybetű, valamint az ékezet nem számít (`szerzodes` is működik).
- Az operátorokat (`AND`, `OR`, `NOT`) nagybetűvel kell írni.
- Ha a beírt szövegben nincs kereshető szó (például csak `"(`), a program figyelmeztet:
  „A keresőkifejezésben nincs kereshető szó.”

## 11. Exportok és a forenzikus jegyzékek

Minden EML ZIP két jegyzéket tartalmaz:

- **`SHA256SUMS.txt`** – minden EML fájl SHA-256-ja, szabványos formában
  (`sha256sum -c` vagy PowerShell `Get-FileHash` szerint ellenőrizhető).
- **`MANIFEST.txt`** – minden fájlhoz: SHA-256, eredet, forrásfájl, a forrás SHA-256-ja, a levél
  helye a forrásban, az export előtti ellenőrzés módja, és a forrás utolsó teljes ellenőrzésének
  ideje (UTC).

Az **eredet** kétféle lehet:

- **eredeti forrásbájtok** – MBOX és EML esetén a ZIP-be a levél eredeti bájtjai kerülnek, bájtra
  pontosan, így a DKIM-aláírás is ellenőrizhető marad. MBOX-nál a „hely a forrásban” jelzi, hogy
  az indexelt tartomány a `From ` borítéksort is tartalmazza, az exportált EML viszont nem; például:
  `bájt 0, hossz 317 (benne a 33 bájtos „From ” borítéksor; az EML 284 bájt)`.
- **PST/OST-ből rekonstruált (nem eredeti bájtok)** – az Outlook nem tárol eredeti MIME-levelet,
  ezért a program az eredeti fejlécekből, MAPI-adatokból, törzsből és mellékletekből állítja össze
  az EML-t. Ennek hash-e a rekonstruált fájlé, nem egy eredeti bájtfolyamé.

## 12. Forrásintegritás: hash-ellenőrzés

A program két szinten ellenőrzi, hogy a forrás változatlan-e.

**Teljes SHA-256 számítás** (a teljes fájl beolvasása) történik:

- a forrás felismerésekor;
- az indexelés befejezése után;
- ha a forrás fájlaláírása (méret, módosítási idő, fájlazonosító) eltér a legutóbbi teljes
  ellenőrzéskor rögzítettől;
- a **Teljes újraellenőrzés** gomb megnyomásakor;
- **szigorú módban** minden levél megnyitása előtt (exportnál forrásonként egyszer).

**Csak aláírás-ellenőrzés** történik gyors módban minden más megnyitáskor és exportkor: ha a méret,
a módosítási idő és a fájlazonosító változatlan, a program a legutóbbi teljes hash-t fogadja el.

| | Gyors mód | Szigorú mód |
| --- | --- | --- |
| Megnyitás | azonnali | minden megnyitás előtt teljes hash |
| Bizonyító erő | a módosítási idő szándékos visszaállításával végzett manipulációt csak a következő teljes ellenőrzés mutatja ki | minden megnyitott és exportált levélhez friss, teljes ellenőrzés |
| Költség | elhanyagolható | nagy PST/OST-nél megnyitásonként akár percek (a kártya becslést mutat) |

**Teljes újraellenőrzés gomb:** a háttérben, folyamatjelzővel fut, a felület közben használható. Az
eredmény és az időpont az ügy adatbázisába és az ügynaplóba kerül, és a forrás sorában „Utolsó
teljes ellenőrzés” néven látszik. **Eltérés esetén** a forrás `source_changed` állapotba kerül, és
leveleit a program többé nem nyitja meg és nem exportálja. Bizonyítási célú export előtt gyors
módban is érdemes ezt a gombot használni.

## 13. Hol tárolja a program az adatokat

| Hely | Tartalom |
| --- | --- |
| `<ügymappa>\.mailscope\index.sqlite3` | az ügy indexe: levelek, személyek, címkék, mentett keresések, ellenőrzési adatok, bekapcsolt melléklet-szöveg mellett a mellékletek kinyert szövege |
| `<ügymappa>\.mailscope\mailscope.log` | az ügy naplója: források, hash-ek, ellenőrzések, hibák |
| `%LOCALAPPDATA%\Mail-Scope\logs\mail-scope.log` | az alkalmazás naplója (rotált, legfeljebb 5 × 5 MB) |
| `%APPDATA%\Mail-Scope\recent.json` | a legutóbbi ügyek listája (név, útvonal, darabszámok) |

A forrásmappákba és a forrásfájlokba a program soha nem ír. Egy ügy teljes törléséhez elég törölni
az ügymappa `.mailscope` almappáját.

## 14. Billentyűparancsok

| Billentyű | Hatás |
| --- | --- |
| `Ctrl+1` … `Ctrl+5` | lapváltás (01–05); beviteli mezőben nem működik |
| `Enter` | keresés indítása az Archívum keresőmezőjében; útvonal elküldése a nyitóképernyőn és az ügy mezőjében |
| `Tab` / `Shift+Tab` | lépkedés a lap elemei között |
| böngésző Vissza / Előre | az előző / következő lap |

## 15. Korlátok

- PST/OST olvasáshoz a mellékelt libpff könyvtár szükséges; sérült vagy nem támogatott
  Outlook-elemek „hibás” bejegyzésként jelennek meg.
- A mellékletek szövegében csak bekapcsolt melléklet-szöveg mellett lehet keresni; OCR nincs, a
  jelszavas archívumok belsejét nem dolgozza fel, és mellékletenként legfeljebb 2 MB szöveget tárol.
- Exportkorlátok: CSV 10 000 találat, EML ZIP 500 levél és 256 MiB, PDF-jelentés 2000 találat.
- A beszélgetésnézet legfeljebb 10 000 levelet és 50 személyt kezel egyszerre; nagyobb halmaznál
  szűkítsen dátumra vagy témára.
- PDF-et a böngésző nyomtatási ablaka készít.
- Az automatikus személycsoportosítás csak a megjelenő nevet használja; azonos nevű különböző
  embereket és eltérő írásmódú neveket kézzel kell javítani.

## 16. Hibaelhárítás

**„A Mail-Scope nem tudott elindulni a 8765-ös porton.”**
Már fut egy példány (például egy régebbi verzió), vagy más program használja a portot. Nyissa meg a
`http://127.0.0.1:8765` címet, és állítsa le a **Leállítás** gombbal, vagy zárja be a
`Mail-Scope.exe`-t a Feladatkezelőben. Másik porton is indítható:
`.\.venv\Scripts\python.exe -m server --port 8766`.

**Egy gombra kattintva csak pörög a jelző (Új ügy, tallózás).**
A mappa- vagy fájlválasztó ablak nyitva van, de a böngésző mögé került. Keresse a tálcán, vagy
használja a nyitóképernyőn és az Ügy lapon a kézi útvonal-megadást.

**Új forrás indexelése után az Archívumban nem jelennek meg a levelei.**
Ellenőrizze, hogy a **Forrás**, **Mappa** vagy **Elemtípus** szűrőben nincs-e kiválasztva valami
(állítsa „Minden …” értékre), és hogy a szövegmező üres-e. Ha a lista még a korábbi állapotot
mutatja, frissítse az oldalt (`F5`). Az **Ügy és források** lapon a forrás állapotának
`COMPLETED`-nek kell lennie.

**„Az indexelés éppen ír az adatbázisba, próbálja újra néhány másodperc múlva.”**
Indexelés közben egy mentés (személy, címke, mentett keresés) nem kapott 5 másodpercen belül
hozzáférést. Ismételje meg a műveletet.

**„A forrásfájl az indexelés óta megváltozott; a forenzikus megnyitás leállt.”**
A forrás tartalma eltér az indexeléskor rögzítettől. Ellenőrizze, ki és mi módosította a fájlt.
A változatlan eredeti visszaállítása után a **Teljes újraellenőrzés** újra „egyezik” eredményt ad.

**A levél törzse üres.**
Előfordul, hogy a levél valóban csak mellékletet tartalmaz (üres sablon). A forenzikus blokk és a
mellékletlista ilyenkor is megjelenik.

**A nyitóképernyő háttere nem mozog.**
A Windows csökkentett mozgás beállítása be van kapcsolva; ez szándékos viselkedés.

**Belső hiba.**
A hibaüzenet megadja a naplófájl helyét. A naplóban (`mail-scope.log`) a részletek a hiba
időpontjánál találhatók; hibajelentéshez ezt a részt érdemes elküldeni – valódi levéltartalom
nélkül.

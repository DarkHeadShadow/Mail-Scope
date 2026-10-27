# Feladat: mellékletjelzés és „Csak melléklettel” szűrő a részletes keresésben

Készítő: Bálint Szabolcs

## Mostani állapot
- A részletes keresés találati sora (`ui/app.js`, `renderDetailedResults`) csak a tárgyat, a
  feladót, a dátumot, a forrást és a címkéket mutatja. Nem látszik, hogy a levélnek van-e
  melléklete.
- Melléklet szerint most csak úgy lehet szűrni, ha a feltételépítőben „Melléklet” mezőt
  választunk, és beírjuk, hogy `yes` vagy `no`. Ez nehézkes.

## 1. Mellékletjelzés a találati sorban
- Ha a levélnek van melléklete, a tárgy mellett jelenjen meg egy **gemkapocs ikon** (inline
  SVG, a felület többi ikonjához illően) és a mellékletek **száma**, pl. „2”. Melléklet nélküli
  levélnél ne jelenjen meg semmi.
- Az ikonon legyen:
  - `title`, amely felsorolja a mellékletek nevét (legfeljebb 5, utána „… és még N”);
  - képernyőolvasónak `aria-label`, pl. „2 melléklet: szerzodes.pdf, kotber.xlsx”.
- Ha a melléklet-nézegető (`docs/feladatok/melleklet-nezegeto.md`) már elkészült, és a levélnek
  van gyanús melléklete (futtatható fájl, vagy a valódi típus eltér a megadottól), az ikon
  figyelmeztető színt kapjon, a `title` pedig írja ki, melyik a gyanús. Ha a nézegető még nincs
  kész, ez a pont kimarad, és a nézegető feladatával együtt készül el.
- Ugyanez az ikon és szám jelenjen meg az **Archívum** levéllistájában és a
  **Beszélgetésnézet** buborékjain is, hogy a jelzés mindenhol egyforma legyen.
- **Backend:** a keresés, az archívum és a beszélgetés lekérdezése adja vissza mellé:
  - `attachment_count` (szám);
  - `attachment_names` (az első 5 név).

  Mindkettő az `attachments` táblából, levélenkénti allekérdezéssel vagy előre összesítve.
  Lapozáskor ne lassuljon: 20 000 levélnél egy találati oldal maradjon 0,1 s alatt.

## 2. „Csak melléklettel” kapcsológomb
- A részletes keresés gombsorába, a „Keresés” gomb mellé kerüljön egy kapcsológomb
  **„Csak melléklettel”** felirattal és gemkapocs ikonnal:
  - `<button aria-pressed="false|true">`, bekapcsolva kiemelt stílussal;
  - billentyűzettel is kapcsolható.
- **Működés:**
  - bekapcsolva a keresés csak a legalább egy melléklettel rendelkező leveleket adja vissza;
  - a feltétel **az egész lekérdezésre** vonatkozik, ÉS kapcsolattal. Akkor is, ha a
    feltételcsoportok között VAGY a kapcsolat;
  - ne új feltételsorként jelenjen meg, hanem külön szűrőként.
- Átkapcsoláskor a keresés azonnal fusson újra, ha már volt eredmény. Az első oldalra ugorjon,
  és a találatszám frissüljön.
- **Mentett keresések:** a kapcsoló állapota a mentett keresés része legyen, betöltéskor álljon
  vissza.
- **Exportok** (CSV, EML ZIP, PDF-jelentés) ugyanazt a szűrt halmazt adják, mint a képernyő.
- **CSV:** kapjon két új oszlopot, „Mellékletek száma” és „Mellékletek neve” (pontosvesszővel
  elválasztva, a képletbiztonsági szabály szerint).
- **API:**
  - a `/api/search` és a `/api/search/export` kérése egy új, opcionális `has_attachment: true`
    mezőt kap a `groups` mellett;
  - a szerver logikai értékként ellenőrizze;
  - a meglévő „Melléklet” feltételmező maradjon meg a régi mentett keresések miatt.

## Elfogadási feltételek
- **Ikon:**
  - melléklettel rendelkező levélnél látszik az ikon és a szám, melléklet nélkülinél nem;
  - a `title` a neveket sorolja.
- **Kapcsoló:**
  - bekapcsolva csak mellékletes levél van a találatok között, VAGY kapcsolatú
    feltételcsoportok mellett is;
  - a mentett keresés visszaállítja.
- **Tesztek:**
  - szerveroldali teszt az `attachment_count` mezőre;
  - teszt a `has_attachment` szűrőre ÉS és VAGY csoportokkal;
  - teszt a CSV új oszlopaira.
- **Nincs regresszió:** CSP-hiba nincs, a meglévő tesztek zöldek.
- **Dokumentáció:**
  - `CHANGELOG.md`;
  - a README-k keresési része;
  - a `Project-Plan.md` 3. képernyőjének leírása;
  - képernyőkép kitalált adatokkal.

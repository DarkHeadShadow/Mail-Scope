# Feladat: javítások a második kódátnézés alapján

Készítő: Bálint Szabolcs

A második átnézés (2026-10-26) futtatással is ellenőrizte a programot. A korábbi kritikus hibák
javítva vannak; az alábbiak maradtak. A sorrend egyben a javasolt munkasorrend.

## 1. Adatbázis-zárolás indexelés közben (komoly)

**Tünet:** indexelés közben egy felületi írás (személy létrehozása, címkézés) akár 11 másodpercig
várhat. Egy futtatásnál maga az indexelés állt le `sqlite3.OperationalError: database is locked`
hibával (`indexer/database.py`, `store_message` → `INSERT`), és a forrás `failed` állapotba került
(összesítés: `completed_with_errors`).

**Reprodukálás:** 20 000 levelet tartalmazó kitalált MBOX indexelése háttérben (`IndexingService.start`),
közben 0,3 másodpercenként `IndexDatabase.create_person(...)` hívás egy másik szálból.

**Ok:**
- az `indexer/service.py` egy kapcsolaton, 500 levelenként commitol (`COMMIT_BATCH_SIZE`);
- a köteg tranzakciója halasztott (deferred) módban indul, és csak az első íráskor kér írási zárat;
- ha közben egy másik kapcsolat írt, WAL módban a tranzakció nem tud íróvá válni, és a
  `busy_timeout` ezen nem segít.

**Javítás:**
- Minden indexelési köteg `BEGIN IMMEDIATE`-tel induljon.
- A commit ne csak 500 levél után történjen, hanem legkésőbb kb. 1 másodpercenként is (idő
  vagy darabszám, amelyik előbb teljesül), így az írási zár sosem marad sokáig foglalva.
- Az indexelő átmeneti `database is locked` / `SQLITE_BUSY` hibánál várjon és próbálja újra a
  köteget (néhányszor, növekvő várakozással). A forrás csak tartós hiba esetén legyen `failed`.
- A felületi írások (személyek, címkék, mentett keresések) szintén `BEGIN IMMEDIATE`-tel
  induljanak. Ha 5 másodpercen belül sem kapnak zárat, magyar hibaüzenetet adjanak:
  „Az indexelés éppen ír az adatbázisba, próbálja újra néhány másodperc múlva.”

**Teszt:**
- Új teszt indítsa az indexelést háttérszálon kitalált, legalább 5000 leveles MBOX-szal, és közben
  hozzon létre legalább 10 személyt, valamint címkézzen.
- Elvárás: az indexelés `completed` állapotban fejeződik be, minden levél bekerül, és egyetlen
  felületi írás sem tart 2 másodpercnél tovább.

## 2. A hash-ellenőrzés hatókörének dokumentálása (közepes)

**Helyzet:**
- Megnyitáskor (`server/app.py`, `_verified_source_hash`) a program nem számol új SHA-256-ot, ha a
  forrás mérete, módosítási ideje és fájlazonosítója nem változott; ilyenkor a legutóbbi teljes
  hash-t fogadja el.
- Ez gyorsítás, de a README és a felület azt sugallja, hogy minden megnyitás előtt teljes
  ellenőrzés történik. A módosítási idő utólag átállítható.

**Teendő:**
- A `README.md` és a `README_HU.md` írja le pontosan:
  - mikor van teljes SHA-256 számítás: felismeréskor, indexelés után, a fájl aláírásának
    változásakor és a 2. pontbeli gombra;
  - mikor csak aláírás-ellenőrzés: minden más megnyitáskor.
- Az „Ügy és források” lapon minden forrás mellett legyen egy **„Teljes újraellenőrzés”** gomb:
  - a hash a háttérben számolódjon, folyamatjelzővel, és ne blokkolja a felületet;
  - az eredményt és az időpontot az ügy adatbázisa és az ügynapló is rögzítse;
  - eltérésnél a forrás `source_changed` állapotba kerüljön.
- A forrás sorában látsszon: „Utolsó teljes ellenőrzés: <dátum, idő>”.
- Az EML-export `MANIFEST.txt`-je minden forrásnál tüntesse fel a legutóbbi teljes ellenőrzés
  időpontját, és hogy az export előtt teljes hash vagy csak aláírás-ellenőrzés történt-e.
- Nyitott kérdés a felhasználó felé: kell-e egy beállítás, amely minden megnyitás előtt
  teljes ellenőrzést kényszerít (lassú nagy PST-knél)? Amíg nincs döntés, ezt ne valósítsd meg.

**Teszt:**
- A gomb végpontja helyes hash-t ad.
- Módosított fájlnál `source_changed` állapotot jelez.
- A manifest tartalmazza az ellenőrzés módját és idejét.

## 3. Apróságok

1. **Dátumformátum:**
   - A levéllistában, az olvasópanelen és a beszélgetés-nézetben a dátum `ÉÉÉÉ-HH-NN ÓÓ:PP`
     formában, a gép helyi idejében jelenjen meg (pl. `2023-05-04 09:00`), ne a nyers
     „Thu, 04 May 2023 09:00:00 +0200” szövegként.
   - Az eredeti `Date` fejléc az olvasópanel forenzikus blokkjában változatlanul maradjon meg.
2. **Fókuszkeret lapváltáskor:**
   - Lapváltáskor a fókusz maradjon a lap címsorán (ez a hozzáférhetőséghez kell), de egérrel
     vagy kattintással történő váltásnál ne jelenjen meg a vastag, beviteli mezőre emlékeztető
     keret.
   - Billentyűs (`Ctrl+1…5`, Tab) váltásnál maradjon látható, finomabb fókuszjelzés.
   - Megoldás `:focus-visible`-lel és a címsoron `tabindex="-1"`-gyel; inline stílus a CSP miatt
     nem lehet.
3. **Értelmetlen keresés:**
   - Ha a `fts_query()` üres kifejezést ad, mert a beírt szövegben nincs kereshető szó (pl. `"(`),
     ne az összes levél jöjjön vissza.
   - Helyette magyar figyelmeztetés: „A keresőkifejezésben nincs kereshető szó.”
4. **MBOX-hely a jegyzékben:**
   - A `MANIFEST.txt` „hely a forrásban” oszlopa MBOX-levélnél jelezze, hogy a tartomány a
     borítéksort is tartalmazza, az exportált fájl viszont nem.
   - Például: `bájt 0, hossz 317 (benne a 33 bájtos „From ” borítéksor; az EML 284 bájt)`.
5. **Nyitóképernyő:** a lábléc („Offline mód…”, verzió) kapjon a háttér fölött egy sötét
   átmenetet vagy hátteret, hogy a fényimpulzusok ne fussanak át a szövegen.
6. **Tesztek más rendszeren:**
   - A `tests/test_eml_reader.py::test_directory_of_eml_files_is_one_source` Windows-útvonalat
     (`..\\notes.json`) használ, ezért Linuxon bukik.
   - Az útvonalat `pathlib`-bel kell építeni, vagy a tesztet Windowsra korlátozni
     (`@unittest.skipUnless(sys.platform == "win32", …)`), indoklással.

## Elfogadási feltételek
- Minden meglévő és új teszt zöld a Windows CI-ban.
- Az 1. pont tesztje legalább háromszor egymás után sikeresen lefut.
- `CHANGELOG.md` bejegyzés.
- A README-k frissülnek a 2. pont szerint.
- A felület változásairól a `docs/screenshots/` képei kitalált adatokkal frissülnek, ahol
  látható a változás.

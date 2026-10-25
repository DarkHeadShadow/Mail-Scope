# Mail-Scope

**Készítő: Bálint Szabolcs**

A Mail-Scope offline, helyben futó levélnéző és -elemző program. Közvetlenül olvassa a postafiókforrásokat, a levél-metaadatokat és kereshető tartalmat az ügy SQLite-adatbázisába indexeli, a teljes eredeti levelet pedig kérésre az eredeti forrásból nyitja meg.

> **Fejlesztési állapot:** `0.1.0` verzió. A projekt aktív fejlesztés alatt áll; még nincs aláírt vagy kiadott Windows-alkalmazás. A Mail-Scope MIT-licencű; a külső összetevők a saját licencük alatt maradnak.

## Funkciók

- MBOX, MBX, MBS, EML, PST és OST források felismerése és indexelése, támogatott fájlok rekurzív megtalálásával.
- PST/OST olvasása az opcionális `libpff-python-windows` kötésen keresztül.
- Keresés levéltörzsben, tárgyban, résztvevők között, dátum, melléklet, mappa, forrás, melléklettípus és ügybeli címke szerint.
- Részletes feltételépítő ÉS/VAGY csoportokkal, menthető és újra betölthető keresésekkel.
- Duplikált levelek összevonása a találatokban, miközben megmaradnak a forráspéldányok és forenzikus lokátorok.
- Címke hozzárendelése a kijelölt levél összes indexelt forráspéldányához.
- Személyalapú beszélgetésnézet és eredeti EML-export SHA-256 jegyzékkel.
- Részletes keresési találatok exportja CSV-be vagy EML ZIP-be; korlátozott keresési jelentés nyomtatása/PDF-be mentése a böngészőből.
- Az index, címkék, mentett keresések és naplók az ügy `.mailscope` mappájában maradnak.

## Adatvédelem és forrásintegritás

- A program nem tölt fel adatot, nem használ külső szolgáltatást, CDN-t vagy internetes erőforrást.
- A helyi szerver kizárólag a `127.0.0.1` címen figyel.
- A forrás-postafiókok csak olvashatók. A levél megnyitása és exportja előtt SHA-256-ellenőrzés történik; megváltozott forrásból a program megtagadja az olvasást.
- Az index lokátorokat, metaadatokat és kereshető tartalmat tárol, nem bontja ki a teljes postafiókot. Eredeti EML csak kifejezett exportkérésre készül.
- A tesztekhez és képernyőképekhez kizárólag kitalált adat használható. Valódi levelet, postafiókot, hitelesítő adatot vagy személyes adatot ne tegyen a repóba.

## Követelmények

- Windows 10/11 x64
- Python 3.13 x64
- SQLite FTS5-támogatással (a támogatott Python-disztribúciók része)
- PST/OST-támogatáshoz `libpff-python-windows`

## Futtatás forrásból

PowerShellben, a repó gyökérmappájából:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\run_dev.bat
```

A felület automatikusan megnyílik az alapértelmezett böngészőben (alapértelmezésben `http://127.0.0.1:8765`). Forrásból futtatva a szerver leállítható a terminálban a `Ctrl+C` billentyűkkel.

Tesztek futtatása:

```powershell
py -3.13 -m pip install -r requirements-dev.txt
py -3.13 -m unittest discover -v
```

## Windows-alkalmazás buildelése

A `build_windows.bat` létrehozza vagy újrahasználja a `.venv` környezetet, telepíti a rögzített build-függőségeket, majd PyInstallerrel onedir csomagot készít:

```bat
build_windows.bat
```

A futtatható fájl: `dist\Mail-Scope\Mail-Scope.exe`. Futtatáskor a teljes `Mail-Scope` mappát tartsa meg, mert a helyi felület és a harmadik fél licenceiről szóló tájékoztatók is abban vannak. A buildhez szükséges a rögzített PST-kötés, amelyet a PyInstaller csomagol.

Az `.exe` indításakor a program automatikusan megnyitja a helyi felületet az alapértelmezett böngészőben; terminálablak nem jelenik meg. A felület jobb felső sarkában található **Leállítás** gomb megerősítést kér. Futó indexelés esetén jelzi ezt, majd biztonságosan megszakítja az aktuális mentési pontnál. A már elmentett levelek és a folytatáshoz szükséges indexállapot megmarad az ügy `.mailscope` adatbázisában.

## Használat

1. Indítsa el a Mail-Scope-ot, majd nyisson meg vagy hozzon létre egy ügyet.
2. Adjon meg forrásfájlt vagy mappát, és ellenőrizze a felismert forrásokat.
3. Indítsa el az indexelést, majd várja meg a befejezését.
4. Böngéssze az archívumot, hozzon létre személyeket és aliasokat a beszélgetésekhez, vagy használja a részletes keresést.
5. Csak a szükséges eredményeket exportálja. A CSV legfeljebb 10 000, az EML ZIP legfeljebb 500 levelet és 256 MiB eredeti levéladatot, a nyomtatható PDF-jelentés legfeljebb 2000 találatot tartalmazhat.

A részletes keresés tárgy, teljes szöveg, feladó, címzett, kezdő/záró dátum, melléklet megléte, forrásútvonal, mappa, melléklet MIME-típusa és címke alapján szűr. A feltételek csoportosíthatók, a csoportok között és csoporton belül is választható az ÉS/VAGY kapcsolat.

## Ismert korlátozások

- A PST/OST olvasás az opcionális natív `pypff` kötéstől és a libpff képességeitől függ. Sérült vagy nem támogatott Outlook-elemek beolvasása sikertelen lehet.
- A keresési index nem tárol teljes levéltörzset vagy melléklet-tartalmat exportra; az eredeti levelet változatlan forrásból kell újra megnyitni.
- A PDF a böngésző nyomtatási párbeszédablakán keresztül készül, nem beépített PDF-könyvtárral.
- A helyi válaszok méretének korlátozására keresési és exportlimitek vannak.
- A nyilvános közzététel előkészítése még hátravan.

## Harmadik féltől származó összetevők

A Mail-Scope licencét a [LICENSE](./LICENSE) fájl tartalmazza (MIT). A PST/OST-támogatás ettől elkülönülő, LGPL-3.0-or-later licencű `libpff`/`pypff` könyvtárat használ. Részletek: [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md) és [LICENSES/NOTICE_libpff.txt](./LICENSES/NOTICE_libpff.txt).

## Képernyőképek

Az alábbi részleteskeresés-képernyőkép kizárólag kitalált postafiókadatot tartalmaz:

![Mail-Scope részletes keresés kitalált találatokkal](./docs/screenshots/detailed-search.png)

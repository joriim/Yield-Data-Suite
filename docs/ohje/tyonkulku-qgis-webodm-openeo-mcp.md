# Ohje: työnkulku QGIS, WebODM, openEO ja tiladata-MCP

Tämä ohje kertoo, mitä työkalua käytetään missäkin prosessointitason vaiheessa
(L0–L5 + lohkot), ja kuvaa MCP-palvelimen, jonka kautta maatilan
datavarastoa luetaan ja päivitetään sekä julkisia aineistoja haetaan.

> **Tila (marraskuu 2026).** Toteutettu: ydinkirjasto L1A–L4, monimuotoinen
> sato- ja konedatan tuonti (CSV/TXT, Excel, Shapefile/GeoPackage/GeoJSON,
> ISOXML kokeellisena, Ag Leader), L5 Sentinel-2 openEO:n kautta,
> lohkokohtainen sää ja agroklimaatti (ERA5 Open-Meteon kautta: PAR, RUE,
> lämpösumma), komentorivi, Slack-ilmoitukset ja tiladata-MCP (luku 3).
> Toteutettu myös: Sentinel-2-haku, peltolohkot ja lohkoraja, viiveen arvio,
> kaistaraidat, lohkojako, laatukanava (GrainSense) ja QGIS-algoritmit.
> sekä monilähteiset kuvapiirteet (drone, PlanetScope) ja WebODM-integraatio.
> Ei vielä toteutettu: maaperähaut (GTK). Ne on
> merkitty alla tunnuksella **[suunnitelma]**.

## 1. Roolijako yhdellä silmäyksellä

| Työkalu | Rooli | Ei käytetä |
|---|---|---|
| **yieldsuite** (Python-ydin) | Kaikki laskenta L0–L5; ainoa paikka, jossa malleja ajetaan | — |
| **QGIS** | Katselu, tarkastus, ihmisen päätökset (viive, liput, hyväksyntä), lohkojen viimeistely. Processing-työkalupakissa *Miiro*: tiedoston tarkistus, ketjun ajo, viiveen arvio, fuusio, lohkojako ja sää (samat funktiot kuin komentorivillä) | Laskentaa ei tehdä käsin QGISissä, ettei tulos riipu klikkauksista |
| **WebODM** | Drone-kuvista heijastusortokuva (Mavic 3M), DSM | Satodatan käsittely |
| **openEO** (Copernicus Data Space) | Sentinel-2-aikasarjat palvelimella: pilvimaski, indeksit, keskiarvo ruuduittain | Satodata ei koskaan lähde openEO:hon |
| **ERA5 / Open-Meteo** | Lohkon päivittäinen sää: lämpötila, pilvet huomioiva säteily, sade, ET0 | Palveluun lähtee vain pyöristetty sijainti (~5 km) |
| **Tiladata-MCP** | Yksi rajapinta datavarastoon ja julkisiin hakuihin agenteille ja työkaluille | Ei laske itse; kutsuu ydintä |
| **Slack** (#yield-data-suite) | Ajojen tila, QA-yhteenveto, hyväksyntäpyynnöt | Karttatiedostot eivät mene Slackiin |

Periaate: **laskenta yhdessä paikassa, ihmisen päätökset tallennetaan
parametreiksi.** Kun käyttäjä korjaa QGISissä viiveen, korjaus tallentuu
koneprofiilin uudeksi versioksi ja ketju ajetaan uudelleen – karttaa ei
muokata käsin.

## 2. Vaihe vaiheelta

### L0 – Raakadata talteen

| Mitä | Miten |
|---|---|
| Puimurin lokit (CSV / ISOXML), vaakalaput, drone-kuvat | Tallennetaan muuttumattomina. MCP: `upload_raw` (tallentaa `raw/`-kansioon tarkistussummineen; olemassa olevaa ei voi korvata) |
| QGIS | Ei tarvita |
| WebODM | Drone-kuvien L0 on WebODM-projektin alkuperäiset kuvat; ne säilytetään myös varastossa |
| openEO | Ei käytetä |
| Julkinen data | **Lohkoraja lohkonumerolla:** `yieldsuite parcel 123-4567-890 --year 2026`, MCP `get_boundary_by_parcel_id` tai QGIS *Data → Field boundary by parcel number*. Haku Ruokaviraston INSPIRE-WFS:stä; vuoden taso ja tunnuskenttä (esim. `PERUSLOHKOTUNNUS`) tunnistetaan palvelusta ja kerrotaan tuloksessa. Vaihtoehtoisesti `get_parcels` alueen lohkoille tai oma tiedosto → `import_boundary` |

Tarkistuslista: kuvien ja lokien tarkistussumma, koneen tunnus, sadonkorjuupäivä.

**Sato- ja konedatan tuonti – mikä tahansa vientitiedosto**

1. Tarkista tiedosto: `yieldsuite inspect vienti.csv` (tai MCP: `inspect_harvest_file`).
   Tuetut muodot: CSV/TXT/TSV (myös suomalainen `;` ja desimaalipilkku),
   Excel, Shapefile/GeoPackage/GeoJSON (myös zip), ISOXML TaskData
   (kansio, TASKDATA.XML tai zip; kokeellinen), Ag Leader advanced -teksti.
2. Raportti kertoo jokaisesta sarakkeesta, mihin se tunnistettiin
   (suomeksi, englanniksi, saksaksi ja valmistajien kenttänimillä), mikä
   yksikkö ja **mistä yksikkö pääteltiin** (asetus, tiedostomuoto,
   sarakeotsikko, vertailu GNSS-paikkoihin tai arvoalue). Arvatuista
   yksiköistä tulee varoitus. Epäselvissä tapauksissa (esim. bu/ac ilman
   kasvia) ohjelma pysähtyy eikä arvaa.
3. Satopistevienneistä (sato pinta-alaa kohden, tyypillinen
   viljelysuunnitteluohjelmien shapefile-vienti) massa rakennetaan
   uudelleen: sato × matka × leikkuuleveys. Näin nekin täsmäytetään
   vaakalappuihin. Ne tulevat tasolla L2, koska valmistaja on jo korjannut
   viiveen.
4. Hyväksy tulos: liitä tulostettu `harvest_logs`-lohko `field.yaml`-tiedostoon
   tai käytä MCP-työkalua `register_harvest_log`, joka myös tallentaa
   tiedoston L0:ksi.

### L1A–L1B – Purku ja massavirta

| Mitä | Miten |
|---|---|
| Laskenta | `yieldsuite run lohko.yaml --to L1B` (CSV-viennit tulevat valmiiksi L1B-tasolla) |
| QGIS | Raakapisteiden nopea silmäily: *Tason lisäys → Erotinmerkein eroteltu teksti*, x/y-sarakkeet, tyyli massavirran mukaan. Etsi: puuttuvat jaksot, GNSS-hypyt, väärät yksiköt |
| MCP | `get_machine_profile`, `update_machine_profile` (header-leveys, antennin siirtymä, kalibrointikäyrä) |
| Kalibrointikuormat | Vaakalaput CSV:nä (`mass_kg`, `machine_id`, `moisture_pct`); MCP: `add_scale_ticket` |

### L2 – Viive, sijainti, tehollinen ala

| Mitä | Miten |
|---|---|
| Laskenta | `--to L2`: massavirta siirretään viiveellä τ_d, kosteus τ_m:llä, antenni → leikkuupöytä, päällekkäisyys poistetaan |
| QGIS – viiveen tarkastus | Avaa `L3_..._records.parquet` (GeoParquet, QGIS 3.28+ GDAL Arrow -tuella). Tyylitä `yield` jatkuvalla väriasteikolla ja zoomaa kaistanpäihin. **Oikea viive:** vierekkäiset, vastakkaisiin suuntiin ajetut kaistat näyttävät saman kuvion. **Väärä viive:** kuvio "sahaa" kaistojen välillä |
| Viiveen arvio datasta | `yieldsuite delay lohko.yaml` tai MCP `estimate_delay`: viive, jolla vastakkaisiin suuntiin ajetut vierekkäiset kaistat vastaavat parhaiten toisiaan, sekä koko käyrä. Jos profiilin arvo poikkeaa arviosta yli 3 s, QA-raporttiin tulee varoitus; jos profiilissa ei ole arvoa, arviota käytetään (tasaisella pellolla arvio ei ole luotettava, ja silloin käytetään oletusta 12 s) |
| Päätös | Uusi viive kirjataan koneprofiiliin (`tau_d_s`), ei karttaan. MCP: `update_machine_profile` luo uuden version (syy pakollinen), sitten `run_chain`. Ajon tulos kertoo, millä profiiliversiolla kartta tehtiin |
| Julkinen data | — |

### L3 – Kalibrointi vaakaan ja liputus

| Mitä | Miten |
|---|---|
| Laskenta | `--to L3`: liput (leikkuupöytä ylhäällä, kaistan päät, nopeus, kosteus, leveys, GNSS, pellon reuna, koko pellon poikkeamat ja paikalliset poikkeamat: paikallinen Moranin I sekä vertailu naapurien mediaaniin) ja kerroin k konekohtaisesti |
| QGIS – lippujen tarkastus | Tyylitä `flags` luokiteltuna (0 = puhdas). Tarkista, että liputettu osuus on järkevä (tyypillisesti 20–40 %, synteettisellä pellolla ~10 %) ja että poikkeamat eivät kasaudu yhteen kohtaan (silloin kyse voi olla todellisesta ilmiöstä) |
| QA | `L4_..._qa.json`: k-kertoimet, lippujen määrät, varoitukset. Slack-viesti näyttää samat luvut |
| Päätös | Hyväksy k tai pyydä lisää vaakalappuja. MCP: `approve_qa` (approved / rejected / needs_data); Slack-painike myöhemmin |
| Julkinen data | Kun lohkoraja on tuotu, tietueet, joiden keskipiste on reunapuskurissa (oletus yksi leikkuuleveys, `edge_buffer_m`) tai rajan ulkopuolella, liputetaan `FIELD_EDGE`. QA kertoo määrän ja rajan pinta-alan |

### L4 – Ruudukko

| Mitä | Miten |
|---|---|
| Laskenta | `--to L4`: massa jaetaan kiinteään ruudukkoon (EPSG:3067 tai lohkon Sentinel-2-ruudun UTM-vyöhyke, esim. EPSG:32634 Länsi-Suomessa, 20 m), massan säilyminen tarkistetaan |
| QGIS | Avaa `L4_..._yield.tif`. Kaistat: `yield`, `coverage`, `n_records`, `spread`, `flagged_share`, `passes`. Näytä `yield` vain, kun `coverage` ≥ 0,5 (Raster Calculator tai tyylin läpinäkyvyys toisen kaistan mukaan) |
| MCP | `list_products(farm, field, level="L4")`, `get_product(product_id)` (ruudukko ja satotilastot) |

### L5 – Fuusio: Sentinel-2 ja drone

**Sentinel-2 (openEO) – toteutettu**

| Mitä | Miten |
|---|---|
| Haku | Ensin katsotaan, mitä pilvettömiä päiviä on: Copernicus Data Space STAC -haku lohkon alueelle ja kasvukaudelle. MCP: `search_sentinel2` tai `yieldsuite s2-search` (päivät ja koko ruudun pilvisyys, vähiten pilvinen per päivä); QGISissä STAC-selain (QGIS 3.40+ tai STAC API Browser -lisäosa) |
| Laskenta | `yieldsuite fuse lohko.yaml --l4 L4.tif` tai `run --to L5`. openEO-palvelimella: pilvimaski SCL:llä, NDVI/NDRE/CIre/NDMI pikseleittäin, keskiarvo ruuduittain. Paikallisesti: malli ja aukkojen täyttö |
| QGIS | Vertaa `prediction`- ja `yield`-kaistoja; `source` = 2 näyttää täytetyt solut; `sigma` niiden epävarmuuden |
| Tunnukset | Ilmainen Copernicus Data Space -tili; ensimmäisellä ajolla kirjautuminen selaimessa |

**Korjaukset puintitietueista (ketjun ajossa) – toteutettu**

| Korjaus | Mitä tapahtuu |
|---|---|
| Jäännössiirtymä (tarkistus) | Kartan ja kuvan korrelaatio kaistan suunnassa siirrettynä. Selvä siirtymä raportoidaan viive-ehdotuksena (mallikortti ja ajon yhteenveto, myös Slack). Karttaa ei muokata: korjaa viive koneprofiiliin ja aja uudelleen |
| Kaistaraidat | Kaistakohtainen kerroin sekamallilla (vertailuna kuvan ennuste), kutistus kohti nollaa, lohkon kokonaismassa säilyy. Alle 3 %:n raitoja ei korjata, koska 20 m kuva ei erota niitä solun sisäisestä vaihtelusta |
| QGIS | Vertaa L4- ja L5-karttaa: raitojen pitäisi kadota ilman, että lohkon kuvio muuttuu |

**Drone (WebODM) – toteutettu** (ks. `docs/multisource.md`)

1. **Lento:** Mavic 3M, RTK päällä, auringonvalosensori, 70–80 % päällekkäisyys. Ajoita samaan ikkunaan kuin hyvät Sentinel-2-päivät. Kalibrointipaneeli ennen ja jälkeen lennon, jos käytössä.
2. **WebODM-tehtävä** monikanavakuvista. Keskeiset asetukset: `radiometric-calibration: camera+sun`, `orthophoto-resolution` esim. 5 cm, `dsm: true`. Tarkista käyttämäsi ODM-version Mavic 3M -tuki ja asetusten nimet ODM:n dokumentaatiosta.
3. **Tuonti varastoon:** heijastusortokuva (G, R, RE, NIR) ja DSM. MCP: `upload_raw kind=drone_images` → `webodm_submit` → `webodm_status` → `import_orthomosaic`; komentorivi `yieldsuite webodm submit|status|download`.
4. **Yhdenmukaistus Sentinel-2:een:** ortokuva aggregoidaan samaan 20 m ruudukkoon pinta-alapainotetulla keskiarvolla, ja kanavakohtainen lineaarinen sovitus tehdään samanaikaiseen Sentinel-2-kuvaan. Kaistavastaavuus: G 560 → B03, R 650 → B04, NIR 860 → B8A. RE 730 on B05:n (705) ja B06:n (740) välissä, joten red edge -indeksit eivät ole suoraan vertailukelpoisia ilman sovitusta.
5. **Käyttö:** drone-indeksit lisäpiirteiksi samaan malliin; 20 m ruudukossa lisäarvo on lähinnä pilvisen kauden paikkaamisessa ja ajankohdan tarkkuudessa. Tarkempi (esim. 5 m) drone-ruudukko vaatii erillisen L4-ajon pienemmällä solukoolla.

### Sää ja agroklimaatti (ERA5) – toteutettu

| Mitä | Miten |
|---|---|
| Haku | `yieldsuite weather lohko.yaml` tai MCP `get_weather`. ERA5 / ERA5-Land Open-Meteon arkistorajapinnasta, välimuistiin `public/era5_daily.csv` alkuperätietoineen |
| PAR (pilvet huomioiva) | 0,48 × kokonaissäteily. ERA5:n säteily sisältää pilvet; kirkkausindeksi kt = säteily / ilmakehän ulkopuolinen säteily näyttää, kuinka pilvinen kukin päivä oli (kt < 0,3 pilvinen, > 0,6 selkeä) |
| Lämpösumma | Tehoisa lämpösumma kynnyksellä 5 °C kylvöstä alkaen; kasvuvaiheiden päivät omista kynnyksistä (`stages_gdd`, kasvi- ja lajikekohtaiset) |
| Muut | Sade, ET0, vesitase (sade − ET0), hellepäivät (> 30 °C), hallapäivät, fototerminen osamäärä (PTQ) valitulla jaksolla; valinnaisesti maan kosteus ja VPD tunneittaisista arvoista |
| Säteilyn käytön tehokkuus (RUE) | Kun L5 ja kylvöpäivä ovat tiedossa: fAPAR Sentinel-2:n NDVI:stä, IPAR = Σ PAR × fAPAR, RUE = kuiva biomassa / IPAR (g/MJ). Tuloksena lohkon RUE ja **solukohtainen RUE-kartta** (`L5_..._rue.tif`) |
| QGIS | Avaa `L5_..._rue.tif`. Matala RUE kohdassa, jossa kasvusto sitoi valoa mutta ei tuottanut satoa → kuivuus, tauti, lako tai typpi. Vertaa sato- ja RUE-karttaa rinnakkain |
| Rajoitukset | Sää on yksi arvo lohkolle päivässä: se selittää vuoden, ei lohkon sisäistä vaihtelua. Korjuuindeksi (oletus 0,45) on oletus ja skaalaa RUE:n suoraan: vertaa RUE:ta saman kasvin ja vuoden sisällä. Pilvien alla ERA5:n säteily on karkea; satelliittipohjainen CM SAF SARAH-3 on suunniteltu toiseksi PAR-lähteeksi **[suunnitelma]** |

Asetukset `field.yaml`-tiedostossa: `crop:` (nimi, kylvö- ja korjuupäivä,
kynnyslämpötila, korjuuindeksi, kasvuvaiheiden lämpösummat), valinnainen
`location:` ja `weather:` (malli, tunneittaiset lisämuuttujat). Ks.
`docs/weather-agroclimate.md`.

### Lohkot (monivuotinen) – toteutettu

| Mitä | Miten |
|---|---|
| Laskenta | `yieldsuite zones L5_2023.tif L5_2024.tif ... [--covariate korkeus.tif]` tai MCP `make_zones(farm, field)` (käyttää kunkin vuoden L5-karttaa, muuten L4:ää). Vuodet suhteelliseksi sadoksi → spatiaalinen PCA (MULTISPATI) → fuzzy k-means; lohkojen määrä valitaan FPI- ja NCE-indekseillä (KM-sPC, Córdoba ym.) |
| Vakaus | Solukohtainen luokka: korkea ja vakaa, matala ja vakaa, epävakaa (vuosien välinen CV ≥ 15 %) |
| Tulokset | GeoTIFF (lohko, jäsenyysaste, vakaus, suhteellinen sato ja sen CV), lohkopolygonit GeoPackagena, yhteenveto JSON:na |
| Julkinen data | Kovariaatit: maastomuuttujat korkeusmallista (`yieldsuite terrain` / MCP `make_terrain`: korkeus, kaltevuus, topografinen asema, kosteusindeksi TWI; korkeusmallin pitää ulottua ~200 m lohkon ulkopuolelle). Maanmittauslaitoksen 2 m korkeusmalli haetaan automaattisesti, jos korkeusmallia ei anneta (`yieldsuite terrain --like L5.tif` / MCP `make_terrain` ilman `dem_path`; vaatii maksuttoman MML:n API-avaimen ympäristömuuttujaan `NLS_API_KEY`). GTK:n maaperäkartan haku (`get_soil_map`) **[suunnitelma]**. Vuosien vertailuun ERA5-yhteenvedot (`get_weather`) |
| QGIS | Avaa lohkopolygonit (`zones_*.gpkg`); viimeistele rajat ja yhdistä pienet alueet; levityskarttojen pohja |
| Rajoitus | Synteettisellä aineistolla 3 oikeaa lohkoa löytyy 2–5 vuoden datasta; kahdella vuodella epävakaa alue voi muodostaa oman lohkonsa. Lohko kertoo, missä sato on ollut toistuvasti korkea tai matala; erilainen hoito on agronominen päätös |

## 3. Tiladata-MCP-palvelin

### Miksi

Sama data tarvitaan monessa paikassa: QGISissä, komentorivillä, Slackissa ja
agenteilla (esim. Claude, joka kokoaa QA-yhteenvedon tai hakee pilvettömät
päivät). MCP-palvelin antaa yhden, oikeuksiltaan rajatun rajapinnan, jotta
kenenkään ei tarvitse tuntea tallennusrakennetta tai rajapinta-avaimia.

### Arkkitehtuuri

```
 Claude / agentit ──┐
 QGIS (myöhemmin) ──┼──►  tiladata-MCP  ──►  yieldsuite-ydin (laskenta)
 Slack-botti ───────┘          │
                               ├──► Tilan datavarasto
                               │      paikallinen: kansio + GeoPackage + staattinen STAC
                               │      palvelin:    objektivarasto + PostGIS + pgSTAC
                               ├──► WebODM REST API (drone-tehtävät)
                               ├──► openEO / CDSE STAC (Sentinel)
                               └──► Julkiset suomalaiset rajapinnat (MML, Ruokavirasto, GTK, IL)
```

Paikallinen versio on toteutettu: `yieldsuite.store` (kansiovarasto) ja
`yieldsuite.mcp_server` (stdio, Python MCP SDK 2.x). Palvelinversio käyttää
samoja työkaluja PostGIS/pgSTAC-taustalla **[suunnitelma]**.

Varaston rakenne:

```
<varasto>/
  audit.jsonl                        jokainen kirjoitus: kuka, milloin, mitä, miksi
  <tila>/machines/<kone>.yaml        koneprofiilin versiot (vain lisäys)
  <tila>/<lohko>/<vuosi>/
    field.yaml                       lohkovuoden asetukset
    raw/                             L0, muuttumaton; raw/manifest.json = sha256
    raw/scale_tickets.csv            vaakalaput (vain lisäys)
    approvals.jsonl                  QA-päätökset
    output/                          L3–L5-tuotteet
```

### Käyttöönotto

```bash
pip install "yieldsuite[geo,mcp]"
yieldsuite store-demo ~/tiladata            # kokeiluvarasto synteettisellä lohkolla
```

Claude Code:

```bash
claude mcp add yieldsuite --env YIELDSUITE_ACTOR=<nimesi> -- yieldsuite mcp --store ~/tiladata
```

Claude Desktop (`claude_desktop_config.json`):

```json
{"mcpServers": {"yieldsuite": {
  "command": "yieldsuite",
  "args": ["mcp", "--store", "/polku/tiladata"],
  "env": {"YIELDSUITE_ACTOR": "nimesi",
          "YIELDSUITE_SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/..."}
}}}
```

`YIELDSUITE_ACTOR` kirjataan auditlokiin jokaiseen muutokseen. Slack-muuttuja
tarvitaan vain, jos `run_chain` postaa yhteenvedon (`notify_slack: true`).

Esimerkkikysymyksiä Claudelle: *"Aja demo-farm/synthetic-01/2026 L4:ään ja
kerro QA"*, *"Viive näyttää QGISissä 2 s liian lyhyeltä – päivitä profiili ja
aja uudelleen"*, *"Hyväksy QA, kerroin on vaakalappujen mukainen."*

### Työkalut

**Datavarasto – luku** (toteutettu)

| Työkalu | Palauttaa |
|---|---|
| `list_farms()` | Tilat |
| `list_fields(farm)` | Lohkot ja vuodet, joilta dataa on |
| `list_products(farm, field, year?, level?)` | Tuotteet: taso, tyyppi, koko, aika |
| `plan_chain(farm, field, year, to_level)` | Tuoteluokka ja ajettavat vaiheet ilman ajoa |
| `get_product(product_id)` | Polku/URL, kaistat, QA-yhteenveto tai mallikortti |
| `get_qa(farm, field, year)` | QA-raportti: k, liput, massan säilyminen, varoitukset, L5-mallikortti, hyväksynnät |
| `get_machine_profile(farm, machine_id, version?)` | Viiveet, offsetit, kalibrointi; versiohistoria |
| `inspect_harvest_file(path, crs?, options?)` | Tunnistettu muoto, sarakkeet, yksiköt ja niiden lähde, varoitukset, valmis konfiguraatiorivi |

**Datavarasto – päivitys** (toteutettu; vaatii vahvistuksen, kirjataan auditlokiin)

| Työkalu | Tekee |
|---|---|
| `upload_raw(farm, field, year, kind, source_path)` | Tallentaa L0:n muuttumattomana, laskee tarkistussumman |
| `add_scale_ticket(farm, field, year, mass_kg, machine_id?, moisture_pct?)` | Lisää vaakalapun |
| `update_machine_profile(farm, machine_id, changes, reason)` | Luo **uuden version**, ei ylikirjoita |
| `run_chain(farm, field, year, to_level, notify_slack)` | Ajaa ytimen, palauttaa yhteenvedon ja tuotetunnukset, valinnaisesti Slackiin |
| `approve_qa(farm, field, year, decision, note)` | Kirjaa ihmisen hyväksynnän |
| `register_harvest_log(farm, field, year, source_path, options?)` | Tarkistaa tiedoston, tallentaa sen L0:ksi ja lisää sen lohkovuoden asetuksiin tunnistetuin sarakkein ja yksiköin |

**Julkiset haut** (tulokset välimuistiin alkuperätietoineen)

| Työkalu | Lähde | Käyttö |
|---|---|---|
| `get_weather(farm, field, year, refresh?)` **(toteutettu)** | ERA5 / ERA5-Land, Open-Meteo-arkisto | PAR, kirkkausindeksi, lämpösumma, kasvuvaiheet, vesitase, RUE |
| `search_sentinel2(farm, field, year, start?, end?, max_cloud?)` **(toteutettu)** | STAC API, oletus Copernicus Data Space | Pilvettömät päivät ennen openEO-ajoa |
| `fetch_s2_features(field_id, year)` | openEO (toteutettu ytimessä) | L5-piirteet |
| `get_parcels(farm, field, year)` **(toteutettu)** | Peltolohkorekisterin WFS (osoite `public_data`-asetuksissa) | Lohkoehdokkaat ja pinta-alat, tallennus `public/parcels.geojson` |
| `import_boundary(farm, field, year, source_path, parcel_ids?)` **(toteutettu)** | Tiedosto tai `get_parcels`-tulos | Lohkoraja L0:ksi, `FIELD_EDGE`-liputus |
| `make_terrain(farm, field)` **(toteutettu)** | Maanmittauslaitos, korkeusmalli 2 m (WCS, avoin, API-avain) | Maastomuuttujat lohkojakoon |
| `get_soil_map(field_id)` **[suunnitelma]** | GTK, maaperäkartta | Lohkot |
| `get_station_weather(field_id)` **[suunnitelma]** | Ilmatieteen laitos, avoin data | Lähiaseman havainnot ERA5:n tarkistukseen |
| `get_satellite_radiation(field_id)` **[suunnitelma]** | CM SAF SARAH-3 | Tarkempi pilvet huomioiva PAR |
| `webodm_submit`, `webodm_status`, `import_orthomosaic` **(toteutettu)** | Oma WebODM-instanssi | Drone-ortokuvat L0:ksi ja L5:een |

Rajapintojen osoitteet, käyttöehdot ja avainten tarve tarkistetaan
toteutusvaiheessa; ne muuttuvat useammin kuin tämä ohje.

### Säännöt

1. **Tilan data kuuluu viljelijälle.** Jokainen kutsu on rajattu tilaan, jolle käyttäjällä on oikeus. Satodataa ei lähetetä julkisiin palveluihin; openEO:lle menee vain ruudukko ja säähakuun vain noin 5 km:iin pyöristetty sijainti.
2. **L0 on muuttumaton.** Päivitystyökalut lisäävät tai versioivat, eivät poista.
3. **Ihmisen päätös on parametri.** Viive, k ja hyväksynnät tallentuvat versioituina, ja jokainen tuote kertoo, millä versiolla se on tehty.
4. **Kirjoittavat työkalut vaativat vahvistuksen** ja kirjataan auditlokiin (kuka, milloin, miksi).
5. **Salaisuudet ympäristömuuttujissa:** CDSE-kirjautuminen, MML-avain, WebODM-token, Slack-token. Ei koskaan repoon.
6. **Julkisen datan alkuperä tallennetaan:** lähde, hakuaika, versio. Tuote on toistettavissa.

## 4. Toteutusjärjestys

1. ~~MCP:n lukutyökalut paikallista kansiovarastoa vasten~~ – tehty.
2. ~~`run_chain` + Slack-yhteenveto + `update_machine_profile`~~ – tehty.
3. ~~Monimuotoinen sato- ja konedatan tuonti, `inspect_harvest_file`, `register_harvest_log`~~ – tehty.
4. ~~Lohkokohtainen sää (ERA5), PAR, lämpösumma, RUE, `get_weather`~~ – tehty.
5. ~~Viiveen arviointi, `search_sentinel2`, `get_parcels`, `import_boundary`, `FIELD_EDGE`~~ – tehty.
6. ~~WebODM-työkalut ja drone-yhdenmukaistus L5:een~~ – tehty.
7. ~~Kaistaraidat, jäännössiirtymä, lohkojako~~ – tehty.
8. MML-, GTK- ja IL-asemahaut sekä SARAH-3-säteily.
9. ~~QGIS-lisäosan algoritmit kutsumaan samaa ydintä~~ – tehty (6 algoritmia, testattu QGIS 3.34:ssä).

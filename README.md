# VPS Fit Benchmark

Jednoduchý benchmark pro orientační porovnání VPS s ohledem na smíšené
provozní zátěže: herní servery, databáze a webové aplikace či Docker.
Skript změří CPU, paměť a disk a vytvoří číselné skóre pro několik typů
využití.

> **Upozornění:** Skóre je heuristické a slouží pouze k orientaci. Není
> standardizovaným benchmarkem ani zárukou konkrétního počtu hráčů, TPS
> nebo dlouhodobé stability.

## Požadavky

- Linux
- Python 3.8 nebo novější
- `sysbench`
- `fio`
- Existující a zapisovatelný adresář na testovaném souborovém systému

Na Debianu a Ubuntu lze závislosti nainstalovat příkazem:

```bash
sudo apt update
sudo apt install -y python3 sysbench fio
```

## Použití

### Stažení a spuštění z GitHubu

Na Debianu nebo Ubuntu lze launcher stáhnout a spustit jedním příkazem.
Doinstaluje chybějící `python3`, `sysbench` a `fio` přes `apt-get` (může
vyžadovat heslo pro `sudo`) a předá launcheru argumenty benchmarku:

```bash
set -o pipefail && curl -fsSL https://raw.githubusercontent.com/berretw/vps_fit_benchmark/main/run-vps-fit-benchmark.sh | bash -s -- --path /var/lib --duration 15 --size-mb 1024
```

Tento příkaz spouští kód stažený z GitHubu. Před spuštěním si můžete
[launcher prohlédnout](https://github.com/berretw/vps_fit_benchmark/blob/main/run-vps-fit-benchmark.sh)
a ověřit, že URL odpovídá repozitáři, kterému důvěřujete. Launcher
automaticky instaluje závislosti pouze na Debianu/Ubuntu; na ostatních
distribucích vypíše chybějící balíčky.

### Lokální spuštění

Skript spusťte jako uživatel, který má právo zapisovat do testovaného
adresáře. Například:

```bash
python3 vps-fit-benchmark.py
```

Testování konkrétního svazku s delším během a větším dočasným souborem:

```bash
python3 vps-fit-benchmark.py --path /var/lib --duration 15 --size-mb 1024 --output reports/vps
```

Adresář uvedený v `--output` musí už existovat.

### Argumenty

| Argument | Výchozí hodnota | Popis |
|---|---:|---|
| `--path` | `.` | Adresář na svazku, který se bude testovat. Musí existovat a být zapisovatelný. |
| `--duration` | `8` | Délka každého testu v sekundách; povolený rozsah je 5–60. |
| `--size-mb` | `512` | Velikost dočasného souboru pro testy disku v MiB; povolený rozsah je 128–4096. |
| `--output` | `vps-fit-report` | Prefix názvů výstupních souborů. |
| `--share` | vypnuto | Připraví veřejný JSON a text pro GitHub Issue. Nic automaticky neodesílá. |

Úplný přehled argumentů:

```bash
python3 vps-fit-benchmark.py --help
```

## Co se měří

- **CPU:** `sysbench` s jedním vláknem a více vlákny; výsledek v událostech za sekundu.
- **Paměť:** sekvenční zápis pomocí `sysbench`; výsledek v MiB/s.
- **Disk:** `fio` s přímým I/O a frontou hloubky 1; sekvenční čtení a zápis
  po 1 MiB, náhodné čtení a zápis po 4 KiB a náhodný zápis s `fsync`.
- **Systémové informace:** mimo jiné CPU, paměť, virtualizace, svazek a dostupné
  informace o blocích a síťových rozhraních.
- **CPU steal:** odhadovaný pouze během testovacího okna CPU.

Výsledky jsou normalizovány na orientační škále 0–100 a váženy pro profily
`FiveM`, `Open77_CP2077`, `Databases`, `Web_Docker` a `Headroom`. Celkové
skóre se nezobrazí, pokud chybí některé klíčové měření. Referenční hodnoty
a váhy jsou pevně definované ve skriptu, nejde o ověřené průmyslové normy.

## Výstup

Skript vytvoří dva soubory podle zadaného prefixu:

- `<prefix>.json` — úplná strukturovaná zpráva včetně měření, skóre a chyb.
- `<prefix>.txt` — čitelný souhrn, který se zároveň vypíše do terminálu.

Při výchozím nastavení se soubory jmenují `vps-fit-report.json` a
`vps-fit-report.txt`.

Proces skončí s návratovým kódem `0`, pokud lze vypočítat celkové skóre,
jinak s kódem `2`. Chybná konfigurace, nepodporovaný systém nebo chybějící
závislost skončí chybou argumentů.

## Sdílení výsledků a komunitní žebříček

[Veřejný žebříček](RESULTS.md) obsahuje výsledky schválené vlastníkem
repozitáře. Jde o údaje dodané uživateli, nikoli o nezávisle ověřená měření.

### Odeslání výsledku

Spusťte benchmark s `--share`:

```bash
python3 vps-fit-benchmark.py --duration 15 --size-mb 1024 --share
```

Stejný přepínač lze použít i při stažení z GitHubu:

```bash
set -o pipefail && curl -fsSL https://raw.githubusercontent.com/berretw/vps_fit_benchmark/main/run-vps-fit-benchmark.sh | bash -s -- --duration 15 --size-mb 1024 --share
```

1. Vedle běžných reportů vzniknou `vps-fit-report.public.json` a
   `vps-fit-report.issue.md` (názvy respektují `--output`).
2. **Prohlédněte si veřejný JSON před zveřejněním.** Obsahuje datum měření,
   OS, architekturu, virtualizaci, model a počet CPU, CPU kvótu, celkovou
   RAM, typ a kapacitu souborového systému, volné místo, nastavení testů
   a naměřené hodnoty. Neobsahuje lokální cesty, zdroje svazků, rozhraní,
   detaily blokových zařízení ani chybové zprávy.
3. Otevřete odkaz vypsaný skriptem, přihlaste se k GitHubu a do nového
   Issue vložte **celý obsah souboru s koncovkou `.issue.md`**.
4. Zveřejněním potvrzujete souhlas se zveřejněním výsledku. Vaše GitHub
   identita i obsah Issue budou veřejné. Běžné `.json` a `.txt` reporty
   nepřikládejte; mohou obsahovat informace, které sdílet nechcete.

Skript nepoužívá token a neprovádí žádný upload. Neúplné měření bez
celkového skóre nelze do žebříčku odeslat; při přípravě sdílení skončí
s kódem `2`, běžné lokální reporty však zůstanou uložené.

### Nastavení a schvalování pro vlastníka repozitáře

1. Nahrajte skript, workflow a ostatní změny do výchozí větve repozitáře.
   V nastavení repozitáře musí být povolené **Issues** a **GitHub Actions**.
2. Vytvořte štítek `benchmark-approved` v sekci Issues → Labels.
3. Zkontrolujte report a souhlas v Issue. Jako vlastník repozitáře
   `BerretW` přidejte tento štítek.
4. Workflow ověří strukturu, rozsahy a verzi dat, přepočítá skóre ze
   zdrojových měření a uloží `results/issue-<číslo>.json`. Poté aktualizuje
   `RESULTS.md` a vytvoří commit pomocí GitHub Actions tokenu.

Workflow potřebuje právo `contents: write`. Pokud pravidla organizace
nebo ochrana výchozí větve blokují přímé commity z Actions, publikování
selže a bude potřeba upravit pravidla nebo změnit workflow na tvorbu PR.
Chyby jsou viditelné v záložce **Actions**; není potřeba vlastní server
ani společný token vložený do benchmarku.

Každé Issue má nejvýše jeden záznam. Úprava nebo zavření Issue ani
odebrání štítku již uložený záznam automaticky nezmění. Pro aktualizaci
report znovu zkontrolujte, štítek odeberte a znovu přidejte. Úprava
obsahu mezi schválením a načtením workflow způsobí odmítnutí.

Pro odstranění výsledku smažte příslušný JSON a řádek v žebříčku.
Odstranění z aktuální větve neodstraňuje data z historie Gitu ani
z původního Issue; nezveřejňujte citlivé údaje. Schválení ani přepočet
skóre nepotvrzuje pravdivost uživatelem dodaných měření.

### Testy

Testy sdílení a sběru používají pouze standardní knihovnu Pythonu a
neodesílají nic na GitHub:

```bash
python3 -B -m unittest discover -s tests -v
```

## Bezpečnost a omezení

- Test disku zapisuje pouze do dočasného souboru v adresáři určeném pomocí
  `--path`; nepíše přímo na blokové zařízení. Dočasné soubory se po testu uklidí.
- I/O testy mohou zatížit disk, ovlivnit běžící služby nebo narazit na limity
  poskytovatele VPS. Spouštějte je mimo špičku a pouze na svazku, který
  vlastníte nebo máte oprávnění testovat.
- Před spuštěním musí být na svazku volné místo alespoň o velikosti
  `--size-mb` plus bezpečnostní rezerva 512 MiB.
- Síťová propustnost ani latence se neměří.
- Virtualizace může skrýt skutečný model hardwaru, typ disku nebo limity CPU.
- Krátký test nevypovídá o dlouhodobé stabilitě ani o špičkách CPU steal.
- Profil `Open77_CP2077` je pouze orientační proxy, nikoli výsledek podle
  zveřejněné specifikace kapacity.

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

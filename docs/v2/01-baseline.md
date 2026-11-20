# ProtoSlice 2.0 — korak 1: zabeleženo izhodiščno stanje

- **Datum pregleda:** 20. 11. 2026
- **Repozitorij:** `LukaCek/protoslice`
- **Izvorna veja:** `main`
- **Izhodiščni commit:** `d141b4a4a84851ac24567d7dfe6812afcc47005f`
- **Ciljna razvojna veja:** `feature/v2-slicing-foundation`
- **Status:** statični pregled kode; produkcijski servis in realna rezanja še niso preverjeni.

## Obstoječa pogodba API

- `app.py` registrira `/v0.1`, `/v0.1.1`, `/v0.1.2` in `/v0.1.3`. Pot `/` preusmeri na `/v0.1.3/`.
- ProtoTisk v `prototisk/settings.py` uporablja `API_VERSION = 'v0.1.1'` in sinhroni klic iz `core/views.py`.
- `v0.1.1`: vhodni parametri so `stlFile` ali `gcsLink` in opcijska `filament` / `process`; ob uspehu vrne ploščat JSON z `used_cm3`, `total_time` itd.
- `v0.1.3`: vhodni parametri vključujejo `stlFile` ali `gcsLink`, `quality`, `material`, opcijsko `process`, `filament`, `supports` in JSON `settings`; ob uspehu vrne `{success, metadata, warnings, support_detection}`.
- `v0.1.3` bere `Metadata/plate_1.gcode` znotraj `output.3mf`, iz katerega razbere `used_mm`, `used_cm3`, `max_z`, `model_time`, `total_time`, `first_layer_time`.

## Zapisane ugotovitve (iz kode, ne iz produkcijskih meritev)

| ID | Opazovanje | Resnost | Dokaz | Naslednji korak |
|---|---|---|---|---|
| B-01 | ProtoTisk nastavi `material_g = slicer_data.get('used_cm3')`. cm³ je prostornina, ne masa. | Kritično | `prototisk/core/views.py`, funkcija `callSliceAPI` | v2 mora vrniti `used_g`; združitev s ProtoTiskom v ločenem PR |
| B-02 | ProtoTisk v `callSliceAPI` ne pošlje izbranega materiala in kakovosti; v `views.py` predpomnilnik išče predvsem po hash-u datoteke in verziji API. | Kritično | `prototisk/core/views.py` | profil in nastavitve morajo biti del zahtevka in ključa predpomnilnika |
| B-03 | `v0.1.3` izračuna `ORCA_TIMEOUT`, a ga ne preda `subprocess.run`. | Visoko | `versions/v0_1_3/routes.py` | v2 mora dejansko prekiniti proces in očistiti delovno mapo |
| B-04 | `v0.1.3` požene subprocess s `cwd=job_path`, nekateri argumenti (pot do STL in konfiguracij) pa so relativni. | Visoko | `versions/v0_1_3/routes.py` | v2 uporablja absolutne poti in preveri, da so berljive v procesovem `cwd` |
| B-05 | `v0.1.3` ob manjkajoči numerični statistiki lahko vrne `0.0` in s tem prikrije napako. | Visoko | `get_data_from_orcaslicer_output` | v2 manjkajočo obvezno statistiko označi kot napako |
| B-06 | Izbira materiala v `v0.1.3` poteka prek delnega ujemanja imen datotek. | Srednje | `resolve_profile` | eksplicitni ID in verzija profilov; zavrnitev dvoumne izbire |
| B-07 | Obstoječi `t/test_slice_api_v0_1_3.py` uporablja monkeypatch `subprocess.run`; realnega OrcaSlicerja ne izvaja. | Visoko za zanesljivost | `t/test_slice_api_v0_1_3.py` | naslednji korak: pravi integracijski test v Dockerju |
| B-08 | `cloudbuild.yaml` uvaja servis `protoslice` z `--allow-unauthenticated`. | Varnost | `cloudbuild.yaml` | preučiti pred produkcijsko izpostavitvijo v2; brez poseganja v deployment v tem koraku |

## Referenčni testni model

`tests/fixtures/models/cube_20mm.stl`: ASCII STL, 12 trikotnikov, zaprt in orientiran navzven, meje `[0,0,0]`–`[20,20,20]` mm, geometrijska prostornina `8000 mm³` (`8 cm³`). SHA-256 je v `tests/fixtures/manifest.json`.

**Pomembno:** 8 cm³ je prostornina polnega geometrijskega volumna, ne poraba filamenta pri izbranih plasteh in infillu. Časa, teže in dejanske porabe še ne določimo brez realnega rezanja.

## Izvajanje in preverjanje

```bash
python -m pip install -r requirements.txt pytest
python -m pytest -q tests/baseline/
```

1. **Zdaj:** preverjanje geometrije testnega modela in dokumentiranje podprtih legacy endpointov.
2. **Naslednji korak:** reševalnik profilov, robustno zaganjanje OrcaSlicerja, testiranje pravih izhodnih datotek.
3. **Pred zaključkom PR:** realni Docker slicing testi za različne filamente, procese, podpore, timeout, izolacijo in skladnost podatkov z GUI izvozom.

## Meje tega koraka

- Brez sprememb v `app.py`, obstoječih `versions/v0_1_*`, `Dockerfile` in `cloudbuild.yaml`.
- Brez produkcijskega deploya, sprememb naročil ali cen.
- Brez trditev, da so realni slicing testi že uspešni.

## Viri

- https://github.com/LukaCek/protoslice/blob/main/versions/v0_1_3/routes.py
- https://github.com/LukaCek/protoslice/blob/main/t/test_slice_api_v0_1_3.py
- https://github.com/LukaCek/protoslice/blob/main/cloudbuild.yaml
- `LukaCek/prototisk` (zasebni repozitorij): `core/views.py`, `prototisk/settings.py`
- https://docs.google.com/document/d/1R5C-ySxXvaVBk_55RAtVXliIULcEQ_-ato7ypKWeYkE/edit

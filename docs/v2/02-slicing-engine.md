# ProtoSlice 2.0 — korak 2: neodvisen rezalni motor

## Meja sprememb

Nov Python paket `engine/` nima odvisnosti od Flaska, ProtoTiska, GCS, WSS ali Cloud Run. Stari API-ji in `app.py` so nedotaknjeni; v2 javni endpoint še ni uveden.

## Komponente

- `engine.models`: `SliceRequest`, `SliceSettings`, `SliceMetrics`, `SliceResult` in `SlicingError`.
- `engine.profiles`: fiksno dovoljeni P1S 0.4, razpoložljivi procesi in PETG filament. Odstrani dvoumno ujemanje imen.
- `engine.slicer`: ločene delovne mape, absolutne poti, shranjevanje rezultata, timeout z ustavitvijo skupine procesov, validacija statusa Orca in 3MF.
- `engine.results`: strogo branje G-code metapodatkov, izrecno `used_g` (grami), `used_cm3` (prostornina), čas v minutah. Manjkajoče mase ni dovoljeno tiho nadomestiti z volumnom. Če profil ne vsebuje eksplicitne gostote, je potreben izvoz gramov s strani OrcaSlicerja.

## API motorja (Python, ni HTTP)

```python
from pathlib import Path
from engine import SlicingEngine, SliceRequest, SliceSettings

engine = SlicingEngine(timeout_seconds=300)
result = engine.slice(SliceRequest(
    source=Path("tests/fixtures/models/cube_20mm.stl"),
    settings=SliceSettings(supports=False, layer_height_mm=0.2),
))
print(result.metrics.used_g, result.metrics.total_time_min)
# output_3mf velja do engine.delete_job(result), npr. po kopiranju v shrambo.
engine.delete_job(result)
```

Rezultat vsebuje absolutno pot do 3MF. Klicatelj mora pred čiščenjem premakniti trajne artefakte v lastno shrambo. Stanja naročil in cen se v tej fazi ne spreminja.

## Preverjanje

1. `python -m pip install -r requirements.txt pytest`
2. `python -m pytest -q tests/baseline tests/engine` — brez Orca, uporablja deterministični subprocess stub.
3. `ORCA_LIVE=1 python -m pytest -vv tests/integration/test_orca_live.py` — zahteva nameščen OrcaSlicer in dejansko rezanje.
4. GitHub Actions `v2-orca-smoke.yml` skuša zgraditi dejansko projektno Docker sliko in zagnati realni preizkus.

**Ne označi integracijskih testov kot uspešnih**, dokler ni zabeležen zelen CI z dejanskim OrcaSlicerjem. Unit tests niso dokaz fizične pravilnosti rezanja.

## Zavestne omejitve

- En model, ena plošča, en filament, privzeto P1S.
- Rezultati in G-code niso samodejno preneseni v GCS; lifecycle bo rešila orkestracija.
- Izpeljani PETG profil v repozitoriju ne vključuje gostote. V v2 jo zanesljivo razrešimo iz pripetih podatkov OrcaSlicer 2.3.0 (1,25 g/cm³), skupaj s preostalimi podedovanimi nastavitvami. Če te verige ni mogoče preveriti, motor odpove.
- Material in kakovost ne vplivata na trenutni ProtoTiskov cenik, saj integracije še nismo spreminjali.
- Docker je namerno priklopljen le v testnem workflowu, brez sprememb `cloudbuild.yaml`.

## Pred naslednjo fazo

Preveriti realno uporabo procesov in podpor, veljaven .3mf, pravilnost gramov, časov in kompatibilnost različic Orca. Ko preizkusi uspejo, se začne async API z enim workerjem.

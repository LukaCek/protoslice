# Version-pinned OrcaSlicer BBL inheritance profiles

These 11 upstream JSON files come from the official OrcaSlicer tag `v2.3.0`:
https://github.com/OrcaSlicer/OrcaSlicer/tree/v2.3.0/resources/profiles/BBL

They exist solely to resolve `inherits` for the checked-in Bambu P1S, six process profiles, and PETG profile. `engine.profiles` overlays the local leaf profiles last.

**Why necessary:** the CLI accepts leaf profile JSONs but does not automatically load their unbundled ancestors; in real slicing the old PETG profile yielded `filament_density = 0` and an invalid price-driving mass.

This dependency must be revisited when upgrading OrcaSlicer. OrcaSlicer upstream is licensed under AGPL-3.0; retain its original notices and consider license compliance when redistributing derived software.

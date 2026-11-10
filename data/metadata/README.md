# Metadata for EEIO v2.1 release candidate v2

- `input_manifest.json` — bytewise SHA-256, original archive path, file sizes and IO structural diagnostics for the **ten** retained source files. Includes 2020 total-table sign/header differences and two omitted exact duplicate files.
- `buffer_inventory.json` — SHA-256, size and missing-cell audit for **55** historical generated buffer CSVs; **no** raw buffer values are redistributed here.

Source data live at `data/inputs/`. They are used in original scripts via local byte-identical copies created by `python run_experiments.py data-prepare`. Do not edit the canonical inputs or silently mix original, author-corrected or domestic 2020 IO versions. Dataset redistribution approval and source citations are pending.

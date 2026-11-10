# Indonesia EEIO — scenario-driven decarbonization research (repository package v2.1; release candidate)

Research companion to *A Scenario-Driven Environmentally Extended Input–Output Framework for Sectoral Decarbonization Policy Analysis in Indonesia* (manuscript in preparation).

The repository preserves the **original scenario-processing and EEIO calculation code** during the manuscript experiments, tests, and the 2016/2020 IO and energy-source inputs. The accompanying MySQL scripts initialize a **research-only database**, not the operational website.

> **Publication status: release candidate, not a validated numerical reproduction.** Input integrity, synthetic invariants, and two data-backed exploratory tests have been checked. **The original manuscript RQ1–RQ4 experiments have not been rerun against a live local MariaDB research instance**, and the author-provided 2020 legacy/verified IO files are not interchangeable. Obtain software-ownership and data-redistribution approval before publishing this repository publicly.

## 1. What is included

| Directory | Contents | Status |
|---|---|---|
| `src/` | Archival Flask/EEIO engine and scenario computation modules | Original numerical functions preserved, web app **not deployment-ready** |
| `experiments/manuscript/` | Original `scenario_batch_v21.py`, numbered audits, named RQ runners | In-memory baseline and intervention logic preserved |
| `experiments/exploratory_validation/` | Five descriptively named model/data/calibration audit entry points | **Exploratory**, not a replacement of the published baseline |
| `experiments/legacy/` | Five unchanged original numbered scripts and legacy import aliases | Archival provenance; prefer descriptive entry points |
| `tests/exploratory_validation/` | Four descriptively named regression launchers | Historical assertions preserved in `tests/legacy/` |
| `tests/` | Original synthetic tests, repository integrity tests, and data-backed diagnostics | Executable without live MySQL for the checks described below |
| `data/inputs/` | Ten exact-byte source inputs/notes from the authors' `data.zip` | Hash-pinned, unchanged; redistribution rights require review |
| `data/metadata/` | Source hash manifest, duplicates/version differences, and buffer inventory | No original `buf.zip` output caches bundled |
| `database/` | Sanitized SQL schema and seven tables of model reference records | **No `user` table, password hashes, accounts or execution history** |
| `scripts/data_tools.py` | Verification and safe staging into legacy local directory layout | Standard library only |
| `results/reference/` | Author-manuscript benchmark values for later comparison | **Reported values, not recomputed values** |
| `docs/` | Run index, provenance and methodological limitations | See links below |

The original `data.zip` also contained two exact duplicate 2020 IO files; those duplicates are recorded in `data/metadata/input_manifest.json`, not committed again. The three **distinct** 2020 IO files are retained separately:

- `io_ind_2020.csv`: **legacy total-table** version; the original 2020 batch scripts resolve this filename.
- `io_ind_2020_total_BPS_verified.csv`: author-labelled **verified/corrected total-table** version, whose SHA-256 is required by the read-only manufacturing pilot.
- `io_ind_2020_domestic.csv`: **domestic-transactions** table used by exploratory domestic-accounting investigations and regression tests.

Comparison of the two 2020 total-table files shows **106 numerical sign differences and eight differing header labels**. No values were automatically substituted or reconciled.

## 2. Installation and input preparation

Use Python **3.11** where possible (the CI test environment) and create an isolated environment:

```sh
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

The dependency list reflects the imports of the supplied original code; it is **not** a frozen pip lockfile. You may install the lighter `numpy pandas scipy openpyxl` set for offline checks only.

First check the canonical inputs, the 185-product tables, and the sanitized SQL — this step **does not** run the model:

```sh
python run_experiments.py data-check
```

The original scripts look under `static/data/`, while the GitHub versioned source files are stored outside the Flask static directory. Create **local copies** only after checking the input hashes:

```sh
python run_experiments.py data-prepare --dry-run
python run_experiments.py data-prepare
```

The second command stages the exact input files to `static/data/2016/`, `static/data/2020/` and `static/data/`, and creates empty local `static/buf/<year>/tmp` folders as needed. This staging area is Git-ignored. Changed local data are **not** overwritten unless you explicitly use `--force`; canonical `data/inputs` files are never modified.

**Web security note:** The source program is an archival implementation. Files under Flask `static/` may be accessible to site visitors if deployed. Keep research datasets outside any internet-facing Flask instance, or configure access controls after a separate security review.

## 3. Set up an isolated MariaDB/MySQL research database

This is necessary for the original **manuscript RQ1–RQ4 batch calculations**, not for offline tests. A compatible MariaDB server such as **10.6** was used in the supplied dump; the research SQL has **not** been import-tested here because a MariaDB server is not available in the packaging environment.

Create an EMPTY research database using an account authorized to create and populate tables:

```sql
CREATE DATABASE eeio_v21_research;
```

Then import the **sanitized** SQL scripts. Example terminal commands (replace `research_user` with your own database username):

```sh
mysql -u research_user -p eeio_v21_research < database/research_schema.sql
mysql -u research_user -p eeio_v21_research < database/research_seed.sql
```

The schema has **19 non-user tables**. Only seven tables contain seed records: `energy_lst` (4 rows), `sector_agg` (14), `sector_lst` (215, comprising 185 detailed products plus aggregate rows), `fec_byyear` (8), `conversion_factor` (4), `co2_factor` (4), and `scenario_type` (7). The remaining scenario/result structures are empty. No historical scenario definitions, web users, credentials or 11k+ scenario-sector execution records are bundled. 

Create a local private `.env` by copying `.env.example` and setting **your own** `APP_KEY`, `DB_USER`, `DB_PASSWORD`, and `DB_NAME=eeio_v21_research`. The preserved original `src/util_db.py` assumes a MySQL server on `localhost`. **Never commit `.env` or a full database dump.** Prefer an isolated research DB account; read-only access is appropriate for inspection, but parts of the original model builder may require write access to result tables.

## 4. Run tests without a live database

```sh
# Repository integrity, v2 input/version checks, synthetic matrix interventions
python run_experiments.py verify-offline

# Two data-backed exploratory regressions, using real 2016/2020 domestic IO CSVs
# and synthetic/mock database tables, not the MariaDB server
python run_experiments.py verify-data-backed
```

The former runs 13 Python unit checks (five original smoke/invariant checks, four v2 data-release checks, and four v2.1 entry-point checks) and four independent original synthetic accounting scripts. The latter stages local source copies and runs the real-IO domestic-transaction and environmental-extension diagnostic scripts. Passing these checks **does not establish equality with the manuscript's published CO2 totals**.

The HEESI allocation and sector-mapping validation tests additionally require the source-anchored `heesi2021_electricity_enduse_2020.csv`, proposed mapping, reviewer notes and evidence files. These were **not supplied in the attachments**, are not fabricated, and remain unavailable in this release.

## 5. Run the manuscript experiments (requires local database)

### 5.1 Configure environment variables

Before running the manuscript experiments, create a `.env` file in the repository's root directory. You can copy the supplied `.env.example` file and edit it with your own database credentials.
```sh
# EEIO Research Database
DB_USER="your_mysql_username"
DB_PASSWORD="your_mysql_password"
DB_NAME="eeio_v21_research"

# Application security key
APP_KEY="your_random_secret_key"
```
The APP_KEY is used to initialize the application's secure serializer and must be defined even when executing the batch experiments.

Generate a random key using: 
`python -c "import secrets; print(secrets.token_urlsafe(32))"`

### 5.2. Run the manuscript experiments
```sh
python run_experiments.py check-setup
python run_experiments.py list
python run_experiments.py show rq2-surface
python run_experiments.py run rq1-screen --dry-run

# RQ1: 185 independent sectors; 555 perturbation/rank runs
python run_experiments.py run rq1-screen -- --quiet
python run_experiments.py run rq1-ranking -- --quiet

# RQ2: demand-efficiency/grid-emission-factor interactions
python run_experiments.py run rq2-four-case
python run_experiments.py run rq2-surface -- --quiet

# RQ3: sector 149 supply-chain contribution analysis
python run_experiments.py run rq3-upstream

# RQ4: magnitude and 2016/2020 cross-year sensitivity
python run_experiments.py run rq4-sector-sensitivity
python run_experiments.py run rq4-cross-year
```

**Default IO boundary:** The original legacy manuscript runner reads `static/data/<year>/io_ind_<year>.csv`. Those exact author-supplied files are included and locally staged by `data-prepare`. The corrected 2020 total and the domestic IO files are **NOT substituted** for the legacy input.

For the two separate manufacturing applications, the pinned **author-verified 2020 total** IO is automatically staged at the required filename:

```sh
python run_experiments.py run manufacturing-m0-m8 -- --target-sector 114 --factor-set LEGACY_JAMALI_2016
python run_experiments.py run manufacturing-m0-m8 -- --target-sector 111 --factor-set LEGACY_JAMALI_2016 --run-dir results/manuscript/manufacturing-glass
```

Results are saved to new, non-overwriting directories under `results/manuscript/`; choose a different `--run-dir` when repeating a task. The runner saves an output manifest with available code and CSV checksums. It **does not currently snapshot live MariaDB table contents**, so repository data-provenance hashes are necessary but not sufficient for complete experimental traceability.

## 6. Descriptive commands for exploratory validation (repository v2.1)

The historical development used sequential *Requests #1–#7*. Those were project-work blocks, **not** the manuscript RQ1–RQ4. For an external researcher, the public CLI and file names now describe the scientific operation. The original five numbered Request #7 scripts remain **byte-identical** in `experiments/legacy/`; compatibility imports and old CLI aliases are retained solely for traceability.

| New command | Public Python file | Role |
|---|---|---|
| `audit-domestic-io` | `experiments/exploratory_validation/domestic_io_model.py` | Domestic IO identity and isolated model |
| `audit-environmental-extensions` | `experiments/exploratory_validation/environmental_extension_audit.py` | Environmental factors/allocation sensitivity |
| `audit-heesi-allocation` | `experiments/exploratory_validation/heesi_energy_allocation.py` | HEESI-anchored energy allocations (extra data needed) |
| `validate-sector-mapping` | `experiments/exploratory_validation/sector_mapping_validation.py` | Human-review mapping evidence (extra data needed) |
| `audit-baseline-energy` | `experiments/exploratory_validation/baseline_energy_diagnostics.py` | Read-only transport/fuel baseline investigation |

A minimal **database-free** validation command is:

```sh
python run_experiments.py data-prepare
python run_experiments.py run audit-domestic-io -- --year 2020 --io-only
python run_experiments.py verify-data-backed
```

Additional optional experiments **must not be interpreted as the manuscript baseline**. They may require a local research DB or external HEESI/mapping evidence. Inspect each operation before running it:

```sh
python run_experiments.py show audit-environmental-extensions
python run_experiments.py run audit-heesi-allocation --dry-run
python run_experiments.py run validate-sector-mapping --dry-run
python run_experiments.py run audit-baseline-energy --dry-run
```

Descriptive regression names are `test-domestic-io`, `test-environmental-extensions`, `test-heesi-allocation`, and `test-sector-mapping`. The last two cannot currently pass without source-anchored HEESI and mapping/reviewer evidence. Historical command aliases still resolve with a deprecation notice; 

## 7. Research scope and interpretation

The original model uses a fixed Leontief economic structure per model year, a sectorally allocated energy-extension representation, and a historical **0.877 tCO2/MWh JAMALI electricity emission-factor proxy**. Scenario interventions change electricity use and/or electricity EF; they do **not** alter the IO technical coefficients or Leontief inverse within a model year. The 2016 and 2020 structures are compared separately. The outcomes represent **model-defined counterfactual EEIO responses**, not verified national avoided emissions, predictions or optimization results.

The domestic IO, final-demand, physical electricity and accounting-boundary investigations (historically developed under Request #7) remain a **separate exploratory branch**. The presence of corrected source files or alternative calculations does not imply the original manuscript results have been recalculated or validated.

## 8. Public release gate and reuse

**Do not make this repository public automatically.** Before publication: (1) obtain author/institution approval for the code; (2) confirm redistribution permissions for every dataset and give original BPS/energy-source citations; (3) review archived `src/app.py` and `src/util_db.py` for application security before any deployment; (4) run the exact legacy RQ1–RQ4 tasks against the isolated research DB; (5) reconcile the 2020 legacy-versus-corrected IO difference transparently; (6) tag the verified software release, add an approved software license and ideally deposit an archived DOI snapshot.

There is intentionally **no blanket software or third-party data license** in this release candidate; institution-approved licenses and attribution must be supplied before public redistribution. 


**Suggested manuscript availability wording once the public tagged version has been independently verified:** “The experimental Python scripts, documented scenario definitions, input-data provenance records, selected processed datasets and research database initialization scripts are available in the versioned repository [URL; release/DOI]. Data sources, model-year and transaction-boundary choices, and reproduction procedures are documented in its README.”

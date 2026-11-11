# Indonesia EEIO — scenario-driven decarbonization research (release candidate)

Research companion to *A Scenario-Driven Environmentally Extended Input–Output Framework for Sectoral Decarbonization Policy Analysis in Indonesia* (manuscript in preparation).

The repository preserves the **original scenario-processing and EEIO calculation code** during the manuscript experiments, tests, and the 2016/2020 IO and energy-source inputs. The accompanying MySQL scripts initialize a **research-only database**, not the operational website.

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

## 2. Installation and input preparation

Use Python **3.11** or **3.10** where possible and create an isolated environment:

```sh
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

First check the canonical inputs, the 185-product tables, and the SQL — this step **does not** run the model:

```sh
python run_experiments.py data-check
```

The original scripts look under `static/data/`, while the GitHub versioned source files are stored outside the Flask static directory. Create **local copies** only after checking the input hashes:

```sh
python run_experiments.py data-prepare --dry-run
python run_experiments.py data-prepare
```

The second command stages the exact input files to `static/data/2016/`, `static/data/2020/` and `static/data/`, and creates empty local `static/buf/<year>/tmp` folders as needed. This staging area is Git-ignored. Changed local data are not overwritten unless `--force` is explicitly used.

## 3. Set up an isolated MariaDB/MySQL research database

This is necessary for the original **manuscript RQ1–RQ4 batch calculations**, not for offline tests. 

Create an EMPTY research database using an account authorized to create and populate tables:

```sql
CREATE DATABASE eeio_v21_research;
```

Then import the SQL scripts. Example terminal commands (replace `research_user` with the relevant database username):

```sh
mysql -u research_user -p eeio_v21_research < database/research_schema.sql
mysql -u research_user -p eeio_v21_research < database/research_seed.sql
```

The schema has **19 non-user tables**. Only seven tables contain seed records: `energy_lst` (4 rows), `sector_agg` (14), `sector_lst` (215, comprising 185 detailed products plus aggregate rows), `fec_byyear` (8), `conversion_factor` (4), `co2_factor` (4), and `scenario_type` (7). The remaining scenario/result structures are empty. 

Create a local private `.env` by copying `.env.example` and setting the appropriate `APP_KEY`, `DB_USER`, `DB_PASSWORD`, and `DB_NAME=eeio_v21_research`.  

## 4. Run tests without a live database

```sh
# Repository integrity, v2 input/version checks, synthetic matrix interventions
python run_experiments.py verify-offline

# Two data-backed exploratory regressions, using real 2016/2020 domestic IO CSVs
# and synthetic/mock database tables, not the MariaDB server
python run_experiments.py verify-data-backed
```

The former runs 13 Python unit checks (five original smoke/invariant checks, four v2 data-release checks, and four v2.1 entry-point checks) and four independent original synthetic accounting scripts. The latter stages local source copies and runs the real-IO domestic-transaction and environmental-extension diagnostic scripts. 

The HEESI allocation and sector-mapping validation tests additionally require the source-anchored `heesi2021_electricity_enduse_2020.csv`, proposed mapping, reviewer notes and evidence files. 

## 5. Run the manuscript experiments (requires local database)

### 5.1 Configure environment variables

Before running the manuscript experiments, create a `.env` file in the repository's root directory. Copy the supplied `.env.example` file and edit it with the appropriate database credentials.
```sh
# EEIO Research Database
DB_USER="mysql_username"
DB_PASSWORD="mysql_password"
DB_NAME="eeio_v21_research"

# Application security key
APP_KEY="random_secret_key"
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

**Default IO boundary:** The original legacy manuscript runner reads `static/data/<year>/io_ind_<year>.csv`. Those files are included and locally staged by `data-prepare`. 

For the two separate manufacturing applications:

```sh
python run_experiments.py run manufacturing-m0-m8 -- --target-sector 114 --factor-set LEGACY_JAMALI_2016
python run_experiments.py run manufacturing-m0-m8 -- --target-sector 111 --factor-set LEGACY_JAMALI_2016 --run-dir results/manuscript/manufacturing-glass
```

Results are saved to new, non-overwriting directories under `results/manuscript/`; choose a different `--run-dir` when repeating a task. The runner saves an output manifest with available code and CSV checksums. 

## 6. Descriptive commands for exploratory validation (repository v2.1)

The historical development used sequential *Requests #1–#7*. Those were project-work blocks, which address the manuscript RQ1–RQ4. 

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

## 7. Research scope and interpretation

The original model uses a fixed Leontief economic structure per model year, a sectorally allocated energy-extension representation, and a historical 0.877 tCO2/MWh JAMALI electricity emission-factor proxy. Scenario interventions change electricity use and/or electricity EF; they do not alter the IO technical coefficients or Leontief inverse within a model year. The 2016 and 2020 structures are compared separately. The outcomes reflect hypothetical baseline EEIO responses rather than verified national emissions reductions, forecasts, or optimization results


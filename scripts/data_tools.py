#!/usr/bin/env python3
"""Read-only integrity inspection and optional local staging of supplied EEIO inputs.

The canonical input files in data/inputs/ are NEVER modified. The stage command
creates local legacy static/data copies to accommodate the original EEIO scripts.
No database connection is opened and no SQL is executed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'data/metadata/input_manifest.json'
SQL_SCHEMA = ROOT / 'database/research_schema.sql'
SQL_SEED = ROOT / 'database/research_seed.sql'
EXPECTED_SEED_TABLES = {
    'co2_factor', 'conversion_factor', 'energy_lst', 'fec_byyear',
    'sector_lst', 'sector_agg', 'scenario_type'
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 512), b''):
            digest.update(chunk)
    return digest.hexdigest()


def check_inputs(*, quiet: bool = False) -> dict:
    """Fail closed for missing/modified input files and malformed 185-product IO headers."""
    data = json.loads(MANIFEST.read_text(encoding='utf-8'))
    errors = []
    for rel, spec in data['files'].items():
        source = ROOT / rel
        if not source.is_file():
            errors.append(f'Missing canonical input: {rel}')
            continue
        observed = sha256_file(source)
        if observed != spec['sha256']:
            errors.append(f'Checksum mismatch: {rel}: {observed}')
            continue
        if spec['type'] == 'io_185_product':
            with source.open(encoding='utf-8-sig', newline='') as stream:
                reader = csv.reader(stream)
                header = next(reader, [])
                codes = [line[0].strip() for line in reader if line]
            expected_codes = {f'{num:03d}' for num in range(1, 186)}
            if not header or header[0] != 'Sector_Code':
                errors.append(f'Wrong first IO header column: {rel}')
            if len(header) != 207:
                errors.append(f'Expected 207 columns in supplied IO CSV: {rel}, found {len(header)}')
            if not expected_codes.issubset(set(header)):
                errors.append(f'Missing one or more columns 001..185: {rel}')
            if not expected_codes.issubset(set(codes)):
                errors.append(f'Missing one or more rows 001..185: {rel}')
    expected_verified = '80bfaa2ed8f9e4cfc98da617a2182272a722af0e1c7f4fa5f558368f4ec69832'
    reviewed = data['files'].get('data/inputs/2020/io_ind_2020_total_BPS_verified.csv', {})
    if reviewed.get('sha256') != expected_verified:
        errors.append('Verified 2020 total-table checksum differs from loader pin')
    # A second, strong safety gate checks that no user-account table or INSERT is shipped.
    schema = SQL_SCHEMA.read_text(encoding='utf-8')
    seed = SQL_SEED.read_text(encoding='utf-8')
    if len(re.findall(r'^CREATE TABLE `[^`]+`', schema, flags=re.M)) != 19:
        errors.append('Research schema is not the expected 19 non-user tables')
    if re.search(r'\b(?:CREATE TABLE|INSERT INTO|ALTER TABLE)\s+`user`', schema+'\n'+seed, flags=re.I):
        errors.append('Forbidden user account table detected in research SQL')
    if re.search(r'\b(?:pbkdf2:|argon2:|bcrypt:)', schema+'\n'+seed, flags=re.I):
        errors.append('Forbidden credential hash token detected in research SQL')
    actual_seeds = set(re.findall(r'^INSERT INTO `([^`]+)`', seed, flags=re.M))
    if actual_seeds != EXPECTED_SEED_TABLES:
        errors.append(f'Seed tables are unexpected: {sorted(actual_seeds)}')
    if errors:
        if not quiet:
            for err in errors: print('FAIL:', err)
        raise ValueError('; '.join(errors))
    result = {
        'verified_input_files': len(data['files']),
        'io_2020_legacy_vs_author_verified': data['io_2020_legacy_vs_author_verified'],
        'research_schema_tables': 19,
        'reference_seed_tables': len(EXPECTED_SEED_TABLES),
        'safe_sql_checks': True,
        'numerical_model_run': False,
    }
    if not quiet:
        print('PASS: all 10 author-supplied input files match release SHA-256 digests')
        print('PASS: 2016/2020 IO shapes and 185-product identifiers')
        print('PASS: sanitized research SQL (19 non-user schemas, 7 reference-data tables)')
        print('NOTE: this is a file/schema preflight, not a numerical EEIO replication')
    return result


def stage_inputs(destination: Path | None = None, *, force: bool = False, dry_run: bool = False, quiet: bool = False) -> list[Path]:
    """Copy canonical data to local static/data, refusing to overwrite other content by default."""
    check_inputs(quiet=True)
    destination = (destination or ROOT / 'static/data').resolve()
    inputs = ROOT / 'data/inputs'
    sources = sorted(p for p in inputs.rglob('*') if p.is_file())
    planned = []
    for src in sources:
        out = destination / src.relative_to(inputs)
        if out.is_file() and sha256_file(out) != sha256_file(src) and not force:
            raise FileExistsError(f'Staged file differs from the published reference: {out}. Use --force ONLY after backing it up.')
        planned.append(out)
    if dry_run:
        if not quiet:print(f'DRY RUN: would prepare {len(planned)} local files under {destination}. No changes made.')
        return planned
    for src, dst in zip(sources, planned):
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.is_file() or sha256_file(dst) != sha256_file(src):
            shutil.copy2(src, dst)
    if destination == (ROOT / 'static/data').resolve():
        for year in ('2016','2020'):
            (ROOT/'static/buf'/year/'tmp').mkdir(parents=True,exist_ok=True)
        (ROOT/'static/buf/download').mkdir(parents=True,exist_ok=True)
        (ROOT/'static/buf/tmp').mkdir(parents=True,exist_ok=True)
    if not quiet:
        print(f'Staged {len(planned)} unchanged copies under {destination}')
        print('Original EEIO batch scripts may now locate static/data inputs.')
        print('A local MariaDB research instance is STILL required for manuscript scenarios.')
        print('Do not deploy archived Flask app with static/data exposed without access review.')
    return planned


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    actions=parser.add_subparsers(dest='command',required=True)
    actions.add_parser('check',help='Verify exact canonical data and safe research SQL, no writes')
    s=actions.add_parser('stage',help='Copy pinned input data to local legacy static/data layout')
    s.add_argument('--target',type=Path,help='Optional alternative staging directory')
    s.add_argument('--force',action='store_true',help='Explicitly replace a mismatching staged local copy')
    s.add_argument('--dry-run',action='store_true',help='Show planned action without writing')
    args=parser.parse_args(argv)
    try:
        if args.command=='check':check_inputs()
        else:stage_inputs(args.target,force=args.force,dry_run=args.dry_run)
    except (ValueError,FileExistsError,OSError) as exc:
        print('ERROR:',exc)
        return 2
    return 0

if __name__=='__main__':
    raise SystemExit(main())

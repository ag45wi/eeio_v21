# Archived EEIO application modules

These files are preserved primarily to support the manuscript's legacy calculation engine and scenarios. Their numerical Python ASTs are tracked in `configs/original_source_manifest.json`. Some non-executable internal comments were removed in version one for safer sharing.

**These are not standalone, security-reviewed web application files.** Authentication routes in `src/app.py` and `src/util_db.py` depend on operational `user` records and include legacy web patterns requiring a security review. The sanitized v2 `database/research_schema.sql` intentionally **does not** create the `user` table; therefore, it is not appropriate to deploy this archived application directly.

For research calculations, use the repository's `run_experiments.py` named entry point, with local, verified `static/data` staging and an isolated MariaDB research instance. Original model-year, carrier allocation and emission-factor caveats are explained in the main README and `docs/METHODOLOGICAL_BOUNDARIES.md`.

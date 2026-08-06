# Quality audit

The 1.1 review covered the complete feed lifecycle:

```text
upload → queue → claim → parse → normalize → validate → version → archive → export
```

## Reviewed areas

- concurrent job claiming;
- background worker lifecycle;
- retry and dead-letter behavior;
- malformed and empty files;
- file-size enforcement;
- duplicate filenames and duplicate feed content;
- decimal and integer validation;
- SKU casing;
- price and currency conflicts;
- database transaction boundaries;
- CSV and Excel formula injection;
- Docker network binding;
- responsive upload controls and table overflow;
- API status codes;
- demo, catalog, job, version, product, Swagger, CSV, and Excel routes.

## Automated verification

Run:

```bash
pytest -q
```

The suite includes unit, workflow, API, background-worker, validation, retry, reporting, and export tests.

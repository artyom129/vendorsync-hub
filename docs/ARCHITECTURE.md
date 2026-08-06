# Architecture

VendorSync Hub separates configuration, adapters, normalization, persistence, versioning, job execution, catalog conflict logic, reporting, and presentation.

## Job lifecycle

```text
pending → running → completed
                  ↘ retrying → running
                             ↘ dead
```

## Catalog lifecycle

```text
feed → supplier adapter → canonical records → validation
     → immutable version → changes → unified catalog → conflicts
```

## Reliability

- SHA-256 duplicate-file detection
- persistent job state
- bounded retry attempts
- dead-letter status
- archived accepted feeds
- rejected source preservation
- immutable product snapshots
- complete audit events

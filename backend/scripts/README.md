# Seed Scripts

## `seed_aua_kua.py` — AUA / KUA synthetic data

Generates ~1,012,350 rows of realistic Aadhaar AUA / KUA data into a Postgres database for stress-testing the semantic-reporting NL-to-SQL agent.

### Tables created

| Table | Rows | Purpose |
|---|---:|---|
| `aua_entities` | ~200 | Authentication User Agencies (banks, telcos, govt) |
| `sub_aua_entities` | ~2,000 | Branches / kiosks / vans under an AUA |
| `kua_entities` | ~150 | KYC User Agencies |
| `devices` | 10,000 | Registered biometric devices |
| `operators` | 50,000 | Field operators |
| `auth_transactions` | 500,000 | Authentication events (the fat fact table) |
| `kyc_transactions` | 200,000 | eKYC requests |
| `error_logs` | 250,000 | System / agency error logs |
| **Total** | **~1.01M** | |

### Run it

```powershell
# From d:\semantic-reporting\backend (with venv active)

python -m scripts.seed_aua_kua `
    --host     localhost `
    --port     5432 `
    --user     postgres `
    --password <YOUR_PG_PASSWORD> `
    --database aua_kua_demo `
    --create-db `
    --drop-existing
```

Or via env vars:

```powershell
$env:PGHOST="localhost"; $env:PGUSER="postgres"; $env:PGPASSWORD="<pwd>"; $env:PGDATABASE="aua_kua_demo"
python -m scripts.seed_aua_kua --create-db --drop-existing
```

Flags:

| Flag | Default | Notes |
|---|---|---|
| `--create-db` | off | `CREATE DATABASE` if missing (connects to `postgres` first) |
| `--drop-existing` | off | `DROP TABLE IF EXISTS` before recreating (idempotent re-runs) |
| `--auth-rows` | 500,000 | Tune the fat table size |
| `--kyc-rows` | 200,000 | |
| `--err-rows` | 250,000 | |
| `--device-rows` | 10,000 | |
| `--op-rows` | 50,000 | |
| `--days-back` | 90 | Time window for transactions / logs |

### Connect from the app

After loading, connect via the DataLens UI → Connection Panel → PostgreSQL tab:

* Host: `localhost`
* Port: `5432`
* Database: `aua_kua_demo`
* User: `postgres`
* Password: *(your local Postgres password)*

### Sample questions to try

| Question | Tests |
|---|---|
| How many auth transactions happened in the last 30 days? | basic count, time filter |
| Top 10 AUAs by transaction count, with a bar chart | join + group by + chart |
| Show daily auth volume trend with a line chart | time-bucket + chart |
| What's the success rate by auth type? | conditional aggregation |
| Average response time per state, top 15 | group + sort + limit |
| Top 5 error categories by count | enum group |
| Failure rate per AUA category, sorted desc | join + ratio |
| Which manufacturers have the most CRITICAL errors? | join + filter |
| Show monthly KYC volume by KYC type with a line chart | multi-series |
| Compare success rate of FINGER vs IRIS vs FACE auth | type comparison |

### Performance

Bulk-loads via `psycopg2.copy_expert` with an in-memory `StringIO` buffer. Indexes are built **after** loading. Typical wall-clock on a laptop SSD: **20–60 seconds** for 1M rows.

### Determinism

Seeded with `random.seed(42)`, so the same script run twice produces the same data — useful for reproducible tests.

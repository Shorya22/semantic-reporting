"""
seed_aua_kua.py
================

Generate ~1 million rows of synthetic AUA / KUA test data into a PostgreSQL
database, designed for stress-testing the Semantic Reporting NL-to-SQL agent.

Domain
------
AUA  = Authentication User Agency (e.g. a bank or telco that performs Aadhaar
       authentication).
KUA  = KYC User Agency  (an entity authorised to fetch eKYC data).
Sub-AUA = a branch or division operating under a parent AUA.

Tables created (~1,012,350 rows total)
--------------------------------------
    aua_entities            ~       200   parent agencies
    sub_aua_entities        ~     2,000   branches under an AUA
    kua_entities            ~       150   KYC agencies
    devices                 ~    10,000   registered biometric devices
    operators               ~    50,000   field operators
    auth_transactions       ~   500,000   authentication events  (FAT TABLE)
    kyc_transactions        ~   200,000   eKYC requests
    error_logs              ~   250,000   error / event log entries
                            ─────────────
    TOTAL                   ~ 1,012,350

Why this shape?
---------------
* Realistic categorical skew (banks dominate, some auth types rare).
* Realistic time skew (business hours busiest, weekends quieter).
* Realistic failure rates and error-code distribution.
* Geographic skew matching India's population (UP/MH/BR are biggest).
* Foreign keys across tables -> enables JOINs, cross-table aggregation
  questions in the semantic-reporting agent.

Driver
------
Uses pure-Python ``pg8000`` (no compiled DLL — works under restrictive
Windows Application Control policies). Bulk-loads via batched parameterised
INSERTs (5,000 rows per batch).

Usage
-----
::

    python -m scripts.seed_aua_kua \\
        --host     localhost \\
        --port     5432 \\
        --user     postgres \\
        --password postgres \\
        --database aua_kua_demo \\
        --create-db                  # auto-creates DB if missing
        --drop-existing              # drop tables first if they already exist

Environment variables (override CLI flags when present):

    PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator

import pg8000.dbapi as pg
import pg8000.native  # noqa: F401  (ensures package import sanity)

# ---------------------------------------------------------------------------
# Determinism — same seed = same data each run.
# ---------------------------------------------------------------------------
SEED = 42
random.seed(SEED)


# ===========================================================================
# Reference / lookup data
# ===========================================================================

# 28 states + 8 UTs of India, weighted by ~population share so transactions
# concentrate in UP / MH / BR / WB like reality.
STATE_WEIGHTS: list[tuple[str, str, float]] = [
    ("UP", "Uttar Pradesh",     20.0),
    ("MH", "Maharashtra",       11.0),
    ("BR", "Bihar",             10.0),
    ("WB", "West Bengal",        8.0),
    ("MP", "Madhya Pradesh",     7.0),
    ("TN", "Tamil Nadu",         6.5),
    ("RJ", "Rajasthan",          6.5),
    ("KA", "Karnataka",          5.5),
    ("GJ", "Gujarat",            5.5),
    ("AP", "Andhra Pradesh",     4.5),
    ("OR", "Odisha",             3.5),
    ("TG", "Telangana",          3.5),
    ("KL", "Kerala",             3.0),
    ("JH", "Jharkhand",          2.8),
    ("AS", "Assam",              2.6),
    ("PB", "Punjab",             2.4),
    ("HR", "Haryana",            2.2),
    ("CH", "Chhattisgarh",       2.2),
    ("DL", "Delhi",              1.6),
    ("UK", "Uttarakhand",        0.9),
    ("HP", "Himachal Pradesh",   0.6),
    ("TR", "Tripura",            0.4),
    ("ML", "Meghalaya",          0.3),
    ("MN", "Manipur",            0.3),
    ("NL", "Nagaland",           0.2),
    ("AR", "Arunachal Pradesh",  0.15),
    ("MZ", "Mizoram",            0.15),
    ("SK", "Sikkim",             0.10),
    ("GA", "Goa",                0.15),
    ("JK", "Jammu and Kashmir",  1.0),
    ("LA", "Ladakh",             0.05),
]
STATE_CODES = [s[0] for s in STATE_WEIGHTS]
STATE_NAMES = {s[0]: s[1] for s in STATE_WEIGHTS}
STATE_PROBS = [s[2] for s in STATE_WEIGHTS]

DISTRICTS_BY_STATE: dict[str, list[str]] = {
    "UP": ["Lucknow", "Kanpur", "Varanasi", "Agra", "Allahabad", "Meerut", "Ghaziabad"],
    "MH": ["Mumbai", "Pune", "Nagpur", "Nashik", "Thane", "Aurangabad"],
    "BR": ["Patna", "Gaya", "Muzaffarpur", "Bhagalpur", "Darbhanga"],
    "WB": ["Kolkata", "Howrah", "Durgapur", "Asansol", "Siliguri"],
    "MP": ["Bhopal", "Indore", "Jabalpur", "Gwalior", "Ujjain"],
    "TN": ["Chennai", "Coimbatore", "Madurai", "Tiruchirappalli", "Salem"],
    "RJ": ["Jaipur", "Jodhpur", "Udaipur", "Kota", "Bikaner"],
    "KA": ["Bengaluru", "Mysuru", "Mangaluru", "Hubballi", "Belagavi"],
    "GJ": ["Ahmedabad", "Surat", "Vadodara", "Rajkot", "Bhavnagar"],
    "AP": ["Visakhapatnam", "Vijayawada", "Guntur", "Tirupati", "Nellore"],
    "OR": ["Bhubaneswar", "Cuttack", "Rourkela", "Berhampur"],
    "TG": ["Hyderabad", "Warangal", "Nizamabad", "Karimnagar"],
    "KL": ["Thiruvananthapuram", "Kochi", "Kozhikode", "Thrissur"],
    "JH": ["Ranchi", "Jamshedpur", "Dhanbad", "Bokaro"],
    "AS": ["Guwahati", "Dibrugarh", "Silchar", "Jorhat"],
    "PB": ["Amritsar", "Ludhiana", "Jalandhar", "Patiala"],
    "HR": ["Gurugram", "Faridabad", "Panipat", "Karnal"],
    "CH": ["Raipur", "Bilaspur", "Bhilai", "Durg"],
    "DL": ["New Delhi", "South Delhi", "North Delhi", "East Delhi", "West Delhi"],
    "UK": ["Dehradun", "Haridwar", "Nainital", "Haldwani"],
    "HP": ["Shimla", "Mandi", "Solan", "Dharamshala"],
}
_DEFAULT_DISTRICTS = ["District 1", "District 2", "District 3"]


AUA_CATEGORY_WEIGHTS: list[tuple[str, float]] = [
    ("Banking",      45.0),
    ("Telecom",      18.0),
    ("Government",   15.0),
    ("Insurance",     7.0),
    ("Healthcare",    5.0),
    ("Fintech",       6.0),
    ("PDS",           2.5),
    ("Education",     1.5),
]
AUA_CATEGORIES = [c[0] for c in AUA_CATEGORY_WEIGHTS]
AUA_CAT_PROBS  = [c[1] for c in AUA_CATEGORY_WEIGHTS]


AUA_NAMES: dict[str, list[str]] = {
    "Banking": [
        "State Bank of India", "HDFC Bank", "ICICI Bank", "Axis Bank",
        "Punjab National Bank", "Bank of Baroda", "Canara Bank", "Union Bank of India",
        "IDBI Bank", "Indian Bank", "Central Bank of India", "Indian Overseas Bank",
        "Yes Bank", "Kotak Mahindra Bank", "IndusInd Bank", "Federal Bank",
        "Karur Vysya Bank", "South Indian Bank", "RBL Bank", "Bandhan Bank",
        "AU Small Finance Bank", "Equitas Small Finance Bank", "Ujjivan SFB",
        "Jana Small Finance Bank", "ESAF SFB",
    ],
    "Telecom": [
        "Bharti Airtel", "Reliance Jio", "Vodafone Idea", "BSNL",
        "MTNL", "Tata Teleservices",
    ],
    "Government": [
        "Income Tax Department", "Passport Seva Kendra", "EPFO", "ESIC",
        "PMJDY Initiative", "Ministry of Rural Development",
        "Department of Posts", "Election Commission",
        "Public Distribution System", "Ministry of Petroleum",
        "Ministry of External Affairs",
    ],
    "Insurance": [
        "Life Insurance Corporation", "HDFC Life", "ICICI Prudential",
        "SBI Life", "Bajaj Allianz", "Max Life Insurance",
        "Tata AIA", "Star Health",
    ],
    "Healthcare": [
        "Apollo Hospitals", "Fortis Healthcare", "Max Healthcare",
        "AIIMS", "Manipal Hospitals", "Narayana Health",
        "Practo eClinic", "Ayushman Bharat",
    ],
    "Fintech": [
        "Paytm Payments Bank", "PhonePe", "Razorpay", "MobiKwik",
        "BharatPe", "Cred", "Groww", "Zerodha", "Upstox",
        "Pine Labs",
    ],
    "PDS": [
        "FCI Distribution", "State PDS Network", "Annapurna Scheme",
        "Public Ration Authority",
    ],
    "Education": [
        "NTA", "AICTE", "UGC NET Cell", "School Education Department",
    ],
}

KUA_NAMES = [
    "Income Tax KYC Cell", "EPFO eKYC Hub", "BSNL eKYC", "Airtel eKYC",
    "Jio eKYC", "Voda Idea eKYC", "SBI eKYC Service", "HDFC eKYC",
    "ICICI Pru eKYC", "Axis eKYC", "Paytm eKYC", "PhonePe eKYC",
    "Razorpay KYC", "GST eKYC Service", "Passport eKYC", "DigiLocker eKYC",
    "NSDL eKYC", "CDSL eKYC", "BSE Star MF eKYC", "NSE eKYC",
    "Stock Holding Corp", "CKYC India", "RTA KFinTech", "Aadhaar Vault Service",
]


AUTH_TYPES = [
    ("FINGER",  "Fingerprint", 60.0),
    ("IRIS",    "Iris",         8.0),
    ("FACE",    "Face",         5.0),
    ("OTP",     "OTP",         18.0),
    ("DEMO",    "Demographic",  9.0),
]
AUTH_TYPE_CODES = [a[0] for a in AUTH_TYPES]
AUTH_TYPE_PROBS = [a[2] for a in AUTH_TYPES]

RESPONSE_CODES_FAILURE = [
    ("100", "Pi (basic) attributes of demographic data did not match"),
    ("200", "Pa (address) attributes of demographic data did not match"),
    ("300", "Biometric data did not match"),
    ("310", "Duplicate fingers used"),
    ("311", "Duplicate iris used"),
    ("400", "Invalid OTP value"),
    ("401", "OTP expired"),
    ("500", "Invalid encryption of session key"),
    ("510", "Invalid certificate identifier"),
    ("520", "Invalid device"),
    ("561", "Request expired (Pid 'ts' value is older than N hours)"),
    ("570", "Invalid key info in PID"),
    ("930", "Technical / server error"),
    ("940", "Unauthorised AUA"),
    ("950", "OTP store related technical error"),
    ("980", "Unsupported auth modality"),
    ("997", "Aadhaar not active / not in CIDR"),
    ("998", "Invalid Aadhaar number"),
    ("999", "Unknown error"),
]
FAILURE_CODE_VALUES = [c[0] for c in RESPONSE_CODES_FAILURE]
FAILURE_CODE_PROBS = [
    20, 5,
    35, 4, 2,
    8, 4,
    1, 1, 2,
    3, 1, 5, 1, 1,
    2, 1, 3, 1,
]

KYC_TYPES = [
    ("BASIC",       55.0),
    ("FULL",        25.0),
    ("BIOMETRIC",   12.0),
    ("OTP",          8.0),
]
KYC_TYPE_CODES = [k[0] for k in KYC_TYPES]
KYC_TYPE_PROBS = [k[1] for k in KYC_TYPES]


DEVICE_TYPES = [
    ("FINGER_SCANNER", 65.0),
    ("IRIS_SCANNER",   12.0),
    ("FACE_CAMERA",     7.0),
    ("MULTIMODAL",     10.0),
    ("OTP_TERMINAL",    6.0),
]
DEVICE_TYPE_CODES = [d[0] for d in DEVICE_TYPES]
DEVICE_TYPE_PROBS = [d[1] for d in DEVICE_TYPES]

DEVICE_MANUFACTURERS = [
    "Mantra Softech", "Morpho (Idemia)", "Precision Biometric", "Startek",
    "Suprema", "ZKTeco", "Cogent", "NEC India",
]


ERROR_CATEGORIES = [
    ("BIOMETRIC",   "biometric_mismatch", 28.0),
    ("NETWORK",     "network_timeout",    20.0),
    ("VALIDATION",  "input_validation",   15.0),
    ("OTP",         "otp_failure",        12.0),
    ("DEVICE",      "device_error",       10.0),
    ("SERVER",      "server_error",        8.0),
    ("CERT",        "certificate_error",   4.0),
    ("AUTH",        "unauthorised",        3.0),
]
ERROR_CAT_CODES = [e[0] for e in ERROR_CATEGORIES]
ERROR_CAT_PROBS = [e[2] for e in ERROR_CATEGORIES]

ERROR_SEVERITIES = [
    ("INFO",     20.0),
    ("WARNING",  35.0),
    ("ERROR",    35.0),
    ("CRITICAL", 10.0),
]
ERROR_SEV_CODES = [e[0] for e in ERROR_SEVERITIES]
ERROR_SEV_PROBS = [e[1] for e in ERROR_SEVERITIES]


# ===========================================================================
# Schema DDL
# ===========================================================================

SCHEMA_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS aua_entities (
        aua_id              SERIAL PRIMARY KEY,
        aua_code            VARCHAR(20)  NOT NULL UNIQUE,
        aua_name            VARCHAR(200) NOT NULL,
        aua_category        VARCHAR(40)  NOT NULL,
        aua_type            VARCHAR(20)  NOT NULL,
        license_status      VARCHAR(20)  NOT NULL,
        registered_date     DATE         NOT NULL,
        license_expiry_date DATE         NOT NULL,
        contact_email       VARCHAR(120),
        state_code          CHAR(2)      NOT NULL,
        state_name          VARCHAR(60)  NOT NULL,
        created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sub_aua_entities (
        sub_aua_id          SERIAL PRIMARY KEY,
        aua_id              INTEGER      NOT NULL REFERENCES aua_entities(aua_id) ON DELETE CASCADE,
        sub_aua_code        VARCHAR(30)  NOT NULL UNIQUE,
        sub_aua_name        VARCHAR(200) NOT NULL,
        branch_type         VARCHAR(40),
        registered_date     DATE         NOT NULL,
        status              VARCHAR(20)  NOT NULL,
        state_code          CHAR(2)      NOT NULL,
        district            VARCHAR(80),
        created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS kua_entities (
        kua_id              SERIAL PRIMARY KEY,
        kua_code            VARCHAR(20)  NOT NULL UNIQUE,
        kua_name            VARCHAR(200) NOT NULL,
        kua_type            VARCHAR(20)  NOT NULL,
        license_status      VARCHAR(20)  NOT NULL,
        registered_date     DATE         NOT NULL,
        license_expiry_date DATE         NOT NULL,
        state_code          CHAR(2)      NOT NULL,
        contact_email       VARCHAR(120),
        created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS devices (
        device_id           SERIAL PRIMARY KEY,
        device_serial       VARCHAR(40)  NOT NULL UNIQUE,
        aua_id              INTEGER      NOT NULL REFERENCES aua_entities(aua_id) ON DELETE CASCADE,
        device_type         VARCHAR(30)  NOT NULL,
        manufacturer        VARCHAR(60)  NOT NULL,
        model               VARCHAR(60)  NOT NULL,
        registered_date     DATE         NOT NULL,
        status              VARCHAR(20)  NOT NULL,
        last_used_date      DATE,
        state_code          CHAR(2)      NOT NULL,
        created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS operators (
        operator_id         SERIAL PRIMARY KEY,
        operator_code       VARCHAR(30)  NOT NULL UNIQUE,
        operator_name       VARCHAR(120) NOT NULL,
        aua_id              INTEGER      NOT NULL REFERENCES aua_entities(aua_id) ON DELETE CASCADE,
        sub_aua_id          INTEGER      REFERENCES sub_aua_entities(sub_aua_id) ON DELETE SET NULL,
        role                VARCHAR(40)  NOT NULL,
        registered_date     DATE         NOT NULL,
        status              VARCHAR(20)  NOT NULL,
        state_code          CHAR(2)      NOT NULL,
        created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS auth_transactions (
        txn_id              BIGSERIAL PRIMARY KEY,
        aua_id              INTEGER      NOT NULL REFERENCES aua_entities(aua_id) ON DELETE CASCADE,
        sub_aua_id          INTEGER      REFERENCES sub_aua_entities(sub_aua_id) ON DELETE SET NULL,
        device_id           INTEGER      REFERENCES devices(device_id) ON DELETE SET NULL,
        operator_id         INTEGER      REFERENCES operators(operator_id) ON DELETE SET NULL,
        auth_type           VARCHAR(10)  NOT NULL,
        response_code       VARCHAR(5)   NOT NULL,
        is_success          BOOLEAN      NOT NULL,
        response_time_ms    INTEGER      NOT NULL,
        txn_timestamp       TIMESTAMPTZ  NOT NULL,
        state_code          CHAR(2)      NOT NULL,
        district            VARCHAR(80),
        uid_hash            CHAR(16)     NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS kyc_transactions (
        kyc_id              BIGSERIAL PRIMARY KEY,
        kua_id              INTEGER      NOT NULL REFERENCES kua_entities(kua_id) ON DELETE CASCADE,
        operator_id         INTEGER      REFERENCES operators(operator_id) ON DELETE SET NULL,
        kyc_type            VARCHAR(15)  NOT NULL,
        response_code       VARCHAR(5)   NOT NULL,
        is_success          BOOLEAN      NOT NULL,
        response_time_ms    INTEGER      NOT NULL,
        data_fields_count   INTEGER      NOT NULL,
        kyc_timestamp       TIMESTAMPTZ  NOT NULL,
        state_code          CHAR(2)      NOT NULL,
        uid_hash            CHAR(16)     NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS error_logs (
        log_id              BIGSERIAL PRIMARY KEY,
        entity_type         VARCHAR(10)  NOT NULL,
        entity_ref_id       INTEGER,
        txn_id              BIGINT,
        error_category      VARCHAR(20)  NOT NULL,
        error_code          VARCHAR(10)  NOT NULL,
        severity            VARCHAR(10)  NOT NULL,
        error_message       VARCHAR(300) NOT NULL,
        log_timestamp       TIMESTAMPTZ  NOT NULL,
        state_code          CHAR(2)
    )
    """,
]


INDEX_STATEMENTS: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_auth_txn_ts          ON auth_transactions (txn_timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_auth_txn_aua         ON auth_transactions (aua_id)",
    "CREATE INDEX IF NOT EXISTS idx_auth_txn_state       ON auth_transactions (state_code)",
    "CREATE INDEX IF NOT EXISTS idx_auth_txn_type        ON auth_transactions (auth_type)",
    "CREATE INDEX IF NOT EXISTS idx_auth_txn_success     ON auth_transactions (is_success)",
    "CREATE INDEX IF NOT EXISTS idx_auth_txn_resp        ON auth_transactions (response_code)",

    "CREATE INDEX IF NOT EXISTS idx_kyc_txn_ts           ON kyc_transactions (kyc_timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_kyc_txn_kua          ON kyc_transactions (kua_id)",
    "CREATE INDEX IF NOT EXISTS idx_kyc_txn_state        ON kyc_transactions (state_code)",
    "CREATE INDEX IF NOT EXISTS idx_kyc_txn_success      ON kyc_transactions (is_success)",

    "CREATE INDEX IF NOT EXISTS idx_err_log_ts           ON error_logs (log_timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_err_log_cat          ON error_logs (error_category)",
    "CREATE INDEX IF NOT EXISTS idx_err_log_sev          ON error_logs (severity)",

    "CREATE INDEX IF NOT EXISTS idx_devices_aua          ON devices (aua_id)",
    "CREATE INDEX IF NOT EXISTS idx_operators_aua        ON operators (aua_id)",
    "CREATE INDEX IF NOT EXISTS idx_subaua_aua           ON sub_aua_entities (aua_id)",
]


# ===========================================================================
# Helpers
# ===========================================================================

def random_state() -> str:
    return random.choices(STATE_CODES, weights=STATE_PROBS, k=1)[0]


def random_district(state_code: str) -> str:
    return random.choice(DISTRICTS_BY_STATE.get(state_code, _DEFAULT_DISTRICTS))


def lognormal_response_time() -> int:
    val = random.lognormvariate(mu=6.1, sigma=0.55)
    return max(50, min(int(val), 15_000))


def biased_timestamp(start: datetime, end: datetime) -> datetime:
    span = end - start
    for _ in range(5):
        ts = start + timedelta(seconds=random.randint(0, int(span.total_seconds())))
        hour = ts.hour
        if hour < 6:
            w = 1
        elif hour < 9:
            w = 4
        elif hour < 18:
            w = 10
        elif hour < 21:
            w = 5
        else:
            w = 2
        if ts.weekday() >= 5:
            w = max(1, w // 2)
        if random.random() < w / 10:
            return ts
    return ts


def fake_uid_hash() -> str:
    return f"{random.getrandbits(64):016x}"


def fake_email(name: str, domain: str = "example.in") -> str:
    safe = "".join(c if c.isalnum() else "" for c in name).lower()[:24] or "contact"
    return f"{safe}@{domain}"


def progress(label: str, n: int, total: int, t0: float) -> None:
    elapsed = time.perf_counter() - t0
    rate = n / max(elapsed, 0.001)
    sys.stdout.write(
        f"\r  {label}: {n:>10,} / {total:>10,}  ({rate:>9,.0f} rows/s, {elapsed:5.1f}s)"
    )
    sys.stdout.flush()


# ===========================================================================
# Bulk insert helper
# ===========================================================================

def batched_insert(
    cursor,
    table: str,
    columns: list[str],
    rows_iter: Iterable[tuple],
    total: int,
    batch_size: int = 5_000,
    label: str = "",
) -> int:
    """Insert rows in batches using a multi-row VALUES INSERT statement."""
    cols_sql = ", ".join(columns)
    placeholders_one = "(" + ", ".join(["%s"] * len(columns)) + ")"

    n = 0
    t0 = time.perf_counter()
    batch: list[tuple] = []

    def flush() -> None:
        if not batch:
            return
        sql_text = (
            f"INSERT INTO {table} ({cols_sql}) VALUES "
            + ", ".join([placeholders_one] * len(batch))
        )
        flat: list[Any] = []
        for row in batch:
            flat.extend(row)
        cursor.execute(sql_text, flat)
        batch.clear()

    for row in rows_iter:
        batch.append(row)
        n += 1
        if len(batch) >= batch_size:
            flush()
            if label:
                progress(label, n, total, t0)
    flush()
    if label:
        progress(label, n, total, t0)
        print()
    return n


# ===========================================================================
# Generators — one per table
# ===========================================================================

def gen_aua_entities() -> tuple[list[str], list[tuple]]:
    rows: list[tuple] = []
    seq = 0
    for category, _w in AUA_CATEGORY_WEIGHTS:
        names = AUA_NAMES.get(category, [])
        for base in names:
            seq += 1
            state = random_state()
            reg_date = datetime(2018, 1, 1) + timedelta(days=random.randint(0, 2200))
            exp_date = reg_date + timedelta(days=random.choice([365, 730, 1095, 1460]))
            rows.append((
                f"AUA{seq:05d}", base, category,
                random.choices(["public", "private"], weights=[20, 80], k=1)[0],
                random.choices(["ACTIVE", "SUSPENDED", "EXPIRED"], weights=[88, 7, 5], k=1)[0],
                reg_date.date(), exp_date.date(), fake_email(base),
                state, STATE_NAMES[state],
            ))
        for _ in range(random.randint(2, 4)):
            base = random.choice(names) if names else f"{category} Agency"
            region = random.choice(["North", "South", "East", "West", "Central"])
            seq += 1
            state = random_state()
            reg_date = datetime(2018, 1, 1) + timedelta(days=random.randint(0, 2200))
            exp_date = reg_date + timedelta(days=random.choice([365, 730, 1095, 1460]))
            rows.append((
                f"AUA{seq:05d}", f"{base} ({region})", category,
                random.choices(["public", "private"], weights=[20, 80], k=1)[0],
                random.choices(["ACTIVE", "SUSPENDED", "EXPIRED"], weights=[88, 7, 5], k=1)[0],
                reg_date.date(), exp_date.date(), fake_email(base + region),
                state, STATE_NAMES[state],
            ))
    while len(rows) < 200:
        category = random.choices(AUA_CATEGORIES, weights=AUA_CAT_PROBS, k=1)[0]
        seq += 1
        name = f"{category} Agency #{seq}"
        state = random_state()
        reg_date = datetime(2018, 1, 1) + timedelta(days=random.randint(0, 2200))
        rows.append((
            f"AUA{seq:05d}", name, category,
            random.choices(["public", "private"], weights=[20, 80], k=1)[0],
            random.choices(["ACTIVE", "SUSPENDED", "EXPIRED"], weights=[88, 7, 5], k=1)[0],
            reg_date.date(),
            (reg_date + timedelta(days=random.choice([365, 730, 1095]))).date(),
            fake_email(name),
            state, STATE_NAMES[state],
        ))
    return ["aua_code", "aua_name", "aua_category", "aua_type", "license_status",
            "registered_date", "license_expiry_date", "contact_email",
            "state_code", "state_name"], rows


def gen_sub_aua_entities(parent_aua_ids: list[int]) -> tuple[list[str], list[tuple]]:
    rows: list[tuple] = []
    seq = 0
    for aua_id in parent_aua_ids:
        for _ in range(random.randint(5, 15)):
            seq += 1
            state = random_state()
            reg_date = datetime(2019, 1, 1) + timedelta(days=random.randint(0, 1900))
            rows.append((
                aua_id, f"SUB{seq:06d}", f"Branch #{seq}",
                random.choices(["BRANCH", "KIOSK", "VAN", "AGENT"],
                               weights=[60, 25, 5, 10], k=1)[0],
                reg_date.date(),
                random.choices(["ACTIVE", "INACTIVE", "SUSPENDED"],
                               weights=[88, 8, 4], k=1)[0],
                state, random_district(state),
            ))
    return ["aua_id", "sub_aua_code", "sub_aua_name", "branch_type",
            "registered_date", "status", "state_code", "district"], rows


def gen_kua_entities() -> tuple[list[str], list[tuple]]:
    rows: list[tuple] = []
    for i, name in enumerate(KUA_NAMES, start=1):
        state = random_state()
        reg_date = datetime(2018, 1, 1) + timedelta(days=random.randint(0, 2200))
        rows.append((
            f"KUA{i:04d}", name,
            random.choices(["public", "private"], weights=[30, 70], k=1)[0],
            random.choices(["ACTIVE", "SUSPENDED", "EXPIRED"], weights=[90, 6, 4], k=1)[0],
            reg_date.date(),
            (reg_date + timedelta(days=random.choice([365, 730, 1095, 1460]))).date(),
            state, fake_email(name),
        ))
    while len(rows) < 150:
        i = len(rows) + 1
        name = f"KYC Service Agency #{i}"
        state = random_state()
        reg_date = datetime(2019, 1, 1) + timedelta(days=random.randint(0, 1900))
        rows.append((
            f"KUA{i:04d}", name, "private",
            random.choices(["ACTIVE", "SUSPENDED", "EXPIRED"], weights=[90, 6, 4], k=1)[0],
            reg_date.date(),
            (reg_date + timedelta(days=730)).date(),
            state, fake_email(name),
        ))
    return ["kua_code", "kua_name", "kua_type", "license_status",
            "registered_date", "license_expiry_date", "state_code", "contact_email"], rows


def gen_devices(parent_aua_ids: list[int], total: int) -> tuple[list[str], list[tuple]]:
    rows: list[tuple] = []
    for i in range(1, total + 1):
        aua_id = random.choice(parent_aua_ids)
        dtype = random.choices(DEVICE_TYPE_CODES, weights=DEVICE_TYPE_PROBS, k=1)[0]
        manufacturer = random.choice(DEVICE_MANUFACTURERS)
        model = f"{manufacturer.split()[0]}-{random.choice(['Pro', 'Lite', 'Plus', 'X'])}{random.randint(100, 999)}"
        reg_date = datetime(2020, 1, 1) + timedelta(days=random.randint(0, 1500))
        last_used = reg_date + timedelta(days=random.randint(0, 800))
        state = random_state()
        rows.append((
            f"DEV{i:08d}", aua_id, dtype, manufacturer, model,
            reg_date.date(),
            random.choices(["ACTIVE", "INACTIVE", "RETIRED"], weights=[80, 12, 8], k=1)[0],
            last_used.date(), state,
        ))
    return ["device_serial", "aua_id", "device_type", "manufacturer", "model",
            "registered_date", "status", "last_used_date", "state_code"], rows


def gen_operators(parent_aua_ids: list[int], sub_aua_ids: list[int],
                  total: int) -> tuple[list[str], list[tuple]]:
    rows: list[tuple] = []
    for i in range(1, total + 1):
        aua_id = random.choice(parent_aua_ids)
        sub_aua_id = random.choice(sub_aua_ids) if random.random() < 0.8 else None
        state = random_state()
        reg_date = datetime(2020, 1, 1) + timedelta(days=random.randint(0, 1500))
        rows.append((
            f"OP{i:07d}", f"Operator #{i}", aua_id, sub_aua_id,
            random.choices(["AGENT", "SUPERVISOR", "KIOSK"],
                           weights=[80, 8, 12], k=1)[0],
            reg_date.date(),
            random.choices(["ACTIVE", "INACTIVE", "TERMINATED"],
                           weights=[85, 10, 5], k=1)[0],
            state,
        ))
    return ["operator_code", "operator_name", "aua_id", "sub_aua_id", "role",
            "registered_date", "status", "state_code"], rows


def gen_auth_transactions(
    parent_aua_ids: list[int],
    sub_aua_ids: list[int],
    device_ids: list[int],
    operator_ids: list[int],
    total: int,
    days_back: int,
) -> tuple[list[str], Iterator[tuple]]:
    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=days_back)

    def _gen() -> Iterator[tuple]:
        for _ in range(total):
            aua_id = random.choice(parent_aua_ids)
            sub_aua_id = random.choice(sub_aua_ids) if random.random() < 0.85 else None
            device_id = random.choice(device_ids)  if random.random() < 0.92 else None
            op_id     = random.choice(operator_ids) if random.random() < 0.88 else None
            auth_type = random.choices(AUTH_TYPE_CODES, weights=AUTH_TYPE_PROBS, k=1)[0]
            is_success = random.random() < 0.93
            code = "000" if is_success else random.choices(
                FAILURE_CODE_VALUES, weights=FAILURE_CODE_PROBS, k=1
            )[0]
            response_time = lognormal_response_time()
            if not is_success:
                response_time = int(response_time * random.uniform(1.1, 2.2))
            ts = biased_timestamp(start_ts, end_ts)
            state = random_state()
            yield (
                aua_id, sub_aua_id, device_id, op_id,
                auth_type, code, is_success, response_time,
                ts, state, random_district(state), fake_uid_hash(),
            )

    return ["aua_id", "sub_aua_id", "device_id", "operator_id",
            "auth_type", "response_code", "is_success", "response_time_ms",
            "txn_timestamp", "state_code", "district", "uid_hash"], _gen()


def gen_kyc_transactions(
    kua_ids: list[int],
    operator_ids: list[int],
    total: int,
    days_back: int,
) -> tuple[list[str], Iterator[tuple]]:
    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=days_back)

    def _gen() -> Iterator[tuple]:
        for _ in range(total):
            kua_id = random.choice(kua_ids)
            op_id = random.choice(operator_ids) if random.random() < 0.6 else None
            kyc_type = random.choices(KYC_TYPE_CODES, weights=KYC_TYPE_PROBS, k=1)[0]
            is_success = random.random() < 0.95
            code = "000" if is_success else random.choices(
                FAILURE_CODE_VALUES, weights=FAILURE_CODE_PROBS, k=1
            )[0]
            response_time = lognormal_response_time()
            if kyc_type in ("FULL", "BIOMETRIC"):
                response_time = int(response_time * 1.4)
            field_count = {
                "BASIC": random.randint(4, 8),
                "FULL":  random.randint(8, 14),
                "BIOMETRIC": random.randint(10, 16),
                "OTP":   random.randint(2, 6),
            }[kyc_type]
            yield (
                kua_id, op_id, kyc_type, code, is_success, response_time,
                field_count, biased_timestamp(start_ts, end_ts),
                random_state(), fake_uid_hash(),
            )

    return ["kua_id", "operator_id", "kyc_type", "response_code", "is_success",
            "response_time_ms", "data_fields_count", "kyc_timestamp",
            "state_code", "uid_hash"], _gen()


def gen_error_logs(
    parent_aua_ids: list[int],
    kua_ids: list[int],
    total: int,
    days_back: int,
) -> tuple[list[str], Iterator[tuple]]:
    end_ts = datetime.now(timezone.utc)
    start_ts = end_ts - timedelta(days=days_back)

    error_msg_templates = {
        "BIOMETRIC":  ["Biometric did not match", "Quality score too low: {q}", "Finger position incorrect"],
        "NETWORK":    ["UIDAI gateway timeout after {t}ms", "TLS handshake failed", "Connection reset by peer"],
        "VALIDATION": ["Invalid Aadhaar checksum", "Demographic field mismatch: {f}", "PID block malformed"],
        "OTP":        ["OTP not delivered", "OTP expired before submission", "Wrong OTP entered (attempt {n}/3)"],
        "DEVICE":     ["Device not registered", "Device certificate expired", "Sensor malfunction code {c}"],
        "SERVER":     ["Internal server error 500", "Worker queue overflow", "DB connection pool exhausted"],
        "CERT":       ["Public key certificate invalid", "Certificate chain incomplete"],
        "AUTH":       ["AUA license expired", "Sub-AUA not authorised for this auth type"],
    }

    def _gen() -> Iterator[tuple]:
        for _ in range(total):
            cat_choice = random.choices(ERROR_CATEGORIES, weights=ERROR_CAT_PROBS, k=1)[0]
            cat_code = cat_choice[0]
            entity_type = random.choices(["AUA", "KUA", "SYSTEM"], weights=[70, 20, 10], k=1)[0]
            entity_ref = (
                random.choice(parent_aua_ids) if entity_type == "AUA"
                else random.choice(kua_ids)   if entity_type == "KUA"
                else None
            )
            template = random.choice(error_msg_templates[cat_code])
            message = template.format(
                q=random.randint(20, 90),
                t=random.randint(2000, 8000),
                f=random.choice(["name", "dob", "address"]),
                n=random.randint(1, 3),
                c=random.randint(100, 999),
            )
            severity = random.choices(ERROR_SEV_CODES, weights=ERROR_SEV_PROBS, k=1)[0]
            error_code = f"E{cat_code[:3]}{random.randint(100, 999)}"
            ts = biased_timestamp(start_ts, end_ts)
            txn_id = random.randint(1, 500_000) if random.random() < 0.4 else None
            yield (
                entity_type, entity_ref, txn_id, cat_code, error_code,
                severity, message, ts, random_state() if random.random() < 0.85 else None,
            )

    return ["entity_type", "entity_ref_id", "txn_id", "error_category",
            "error_code", "severity", "error_message", "log_timestamp", "state_code"], _gen()


# ===========================================================================
# Orchestration
# ===========================================================================

def maybe_create_database(args: argparse.Namespace) -> None:
    """Connect to the maintenance DB and CREATE DATABASE if missing."""
    print(f"\n-> Ensuring database '{args.database}' exists ...")
    admin_conn = pg.connect(
        host=args.host, port=args.port, user=args.user,
        password=args.password, database="postgres",
    )
    admin_conn.autocommit = True
    cur = admin_conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (args.database,))
    exists = cur.fetchone() is not None
    if exists:
        print(f"  Database '{args.database}' already exists.")
    else:
        cur.execute(f'CREATE DATABASE "{args.database}"')
        print(f"  Created database '{args.database}'.")
    cur.close()
    admin_conn.close()


def maybe_drop_existing(conn) -> None:
    print("\n-> Dropping existing tables ...")
    cur = conn.cursor()
    for t in [
        "error_logs", "auth_transactions", "kyc_transactions",
        "operators", "devices", "sub_aua_entities",
        "kua_entities", "aua_entities",
    ]:
        cur.execute(f'DROP TABLE IF EXISTS "{t}" CASCADE')
    conn.commit()
    cur.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed AUA/KUA synthetic data into a Postgres database."
    )
    parser.add_argument("--host",     default=os.environ.get("PGHOST",     "localhost"))
    parser.add_argument("--port",     default=int(os.environ.get("PGPORT", 5432)), type=int)
    parser.add_argument("--user",     default=os.environ.get("PGUSER",     "postgres"))
    parser.add_argument("--password", default=os.environ.get("PGPASSWORD", "postgres"))
    parser.add_argument("--database", default=os.environ.get("PGDATABASE", "aua_kua_demo"))
    parser.add_argument("--create-db",      action="store_true")
    parser.add_argument("--drop-existing",  action="store_true")

    parser.add_argument("--auth-rows",   default=500_000, type=int)
    parser.add_argument("--kyc-rows",    default=200_000, type=int)
    parser.add_argument("--err-rows",    default=250_000, type=int)
    parser.add_argument("--device-rows", default=10_000,  type=int)
    parser.add_argument("--op-rows",     default=50_000,  type=int)
    parser.add_argument("--days-back",   default=90,      type=int)

    args = parser.parse_args()

    if args.create_db:
        maybe_create_database(args)

    print(f"\n-> Connecting to {args.user}@{args.host}:{args.port}/{args.database} ...")
    conn = pg.connect(
        host=args.host, port=args.port, user=args.user,
        password=args.password, database=args.database,
    )
    conn.autocommit = False

    if args.drop_existing:
        maybe_drop_existing(conn)

    print("\n-> Creating schema ...")
    cur = conn.cursor()
    for stmt in SCHEMA_STATEMENTS:
        cur.execute(stmt)
    conn.commit()

    overall_start = time.perf_counter()

    # ---------------------- Reference tables --------------------------------
    print("\n[1/8] aua_entities ...")
    cols, rows = gen_aua_entities()
    n = batched_insert(cur, "aua_entities", cols, iter(rows), len(rows), batch_size=500)
    conn.commit()
    print(f"   inserted {n:,}")

    cur.execute("SELECT aua_id FROM aua_entities ORDER BY aua_id")
    aua_ids = [r[0] for r in cur.fetchall()]

    print("\n[2/8] sub_aua_entities ...")
    cols, rows = gen_sub_aua_entities(aua_ids)
    n = batched_insert(cur, "sub_aua_entities", cols, iter(rows), len(rows), batch_size=1000)
    conn.commit()
    print(f"   inserted {n:,}")

    cur.execute("SELECT sub_aua_id FROM sub_aua_entities ORDER BY sub_aua_id")
    sub_aua_ids = [r[0] for r in cur.fetchall()]

    print("\n[3/8] kua_entities ...")
    cols, rows = gen_kua_entities()
    n = batched_insert(cur, "kua_entities", cols, iter(rows), len(rows), batch_size=500)
    conn.commit()
    print(f"   inserted {n:,}")

    cur.execute("SELECT kua_id FROM kua_entities ORDER BY kua_id")
    kua_ids = [r[0] for r in cur.fetchall()]

    print(f"\n[4/8] devices ({args.device_rows:,}) ...")
    cols, rows = gen_devices(aua_ids, args.device_rows)
    n = batched_insert(cur, "devices", cols, iter(rows), len(rows),
                       batch_size=2000, label="devices")
    conn.commit()

    cur.execute("SELECT device_id FROM devices ORDER BY device_id")
    device_ids = [r[0] for r in cur.fetchall()]

    print(f"\n[5/8] operators ({args.op_rows:,}) ...")
    cols, rows = gen_operators(aua_ids, sub_aua_ids, args.op_rows)
    n = batched_insert(cur, "operators", cols, iter(rows), len(rows),
                       batch_size=2000, label="operators")
    conn.commit()

    cur.execute("SELECT operator_id FROM operators ORDER BY operator_id")
    operator_ids = [r[0] for r in cur.fetchall()]

    # ---------------------- Big tables --------------------------------------
    print(f"\n[6/8] auth_transactions ({args.auth_rows:,}) ...")
    cols, rows_iter = gen_auth_transactions(
        aua_ids, sub_aua_ids, device_ids, operator_ids,
        total=args.auth_rows, days_back=args.days_back,
    )
    n = batched_insert(cur, "auth_transactions", cols, rows_iter, args.auth_rows,
                       batch_size=2000, label="auth_transactions")
    conn.commit()

    print(f"\n[7/8] kyc_transactions ({args.kyc_rows:,}) ...")
    cols, rows_iter = gen_kyc_transactions(
        kua_ids, operator_ids,
        total=args.kyc_rows, days_back=args.days_back,
    )
    n = batched_insert(cur, "kyc_transactions", cols, rows_iter, args.kyc_rows,
                       batch_size=2000, label="kyc_transactions")
    conn.commit()

    print(f"\n[8/8] error_logs ({args.err_rows:,}) ...")
    cols, rows_iter = gen_error_logs(
        aua_ids, kua_ids, total=args.err_rows, days_back=args.days_back,
    )
    n = batched_insert(cur, "error_logs", cols, rows_iter, args.err_rows,
                       batch_size=2000, label="error_logs")
    conn.commit()

    # ---------------------- Indexes + ANALYZE -------------------------------
    print("\n-> Building indexes (post-load for speed) ...")
    for stmt in INDEX_STATEMENTS:
        cur.execute(stmt)
    conn.commit()

    print("-> Running ANALYZE on all tables ...")
    conn.autocommit = True
    cur.execute("ANALYZE")
    conn.autocommit = False

    # ---------------------- Summary -----------------------------------------
    print("\n-> Row counts:")
    for t in ["aua_entities", "sub_aua_entities", "kua_entities",
              "devices", "operators",
              "auth_transactions", "kyc_transactions", "error_logs"]:
        cur.execute(f'SELECT COUNT(*) FROM "{t}"')
        print(f"   {t:<20s}  {cur.fetchone()[0]:>12,}")

    elapsed = time.perf_counter() - overall_start
    print(f"\n[OK] Done in {elapsed:.1f}s.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()

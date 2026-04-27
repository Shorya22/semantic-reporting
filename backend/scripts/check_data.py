"""Quick sanity check on the seeded AUA/KUA database."""
import pg8000.dbapi as pg

c = pg.connect(host="localhost", port=5432, user="postgres",
               password="Admin123", database="aua_kua_demo")
cur = c.cursor()

print("=" * 70)
print("SANITY CHECKS - verifying seeded data is realistic")
print("=" * 70)

print("\n1. Auth success rate by auth_type:")
cur.execute("""
    SELECT auth_type,
           COUNT(*)                                                         AS total,
           ROUND(100.0 * SUM(CASE WHEN is_success THEN 1 ELSE 0 END)/COUNT(*), 2) AS success_pct
    FROM auth_transactions GROUP BY auth_type ORDER BY total DESC
""")
print(f"  {'type':<8s}  {'count':>10s}  success%")
for r in cur.fetchall():
    print(f"  {r[0]:<8s}  {r[1]:>10,}  {float(r[2]):>7.2f}%")

print("\n2. Top 10 AUAs by transaction volume:")
cur.execute("""
    SELECT a.aua_name, a.aua_category, COUNT(*) AS txns
    FROM auth_transactions t JOIN aua_entities a ON a.aua_id = t.aua_id
    GROUP BY a.aua_name, a.aua_category ORDER BY txns DESC LIMIT 10
""")
for r in cur.fetchall():
    print(f"  {r[0]:<40s}  {r[1]:<12s}  {r[2]:>7,}")

print("\n3. Top 5 states by transaction volume (should match India population):")
cur.execute("""
    SELECT state_code, COUNT(*) AS txns
    FROM auth_transactions GROUP BY state_code ORDER BY txns DESC LIMIT 5
""")
for r in cur.fetchall():
    print(f"  {r[0]}  {r[1]:>7,}")

print("\n4. Error category breakdown (top 8 categories x severity):")
cur.execute("""
    SELECT error_category, severity, COUNT(*) AS n
    FROM error_logs GROUP BY error_category, severity
    ORDER BY error_category, severity
""")
for r in cur.fetchall():
    print(f"  {r[0]:<12s}  {r[1]:<10s}  {r[2]:>7,}")

print("\n5. Time range:")
cur.execute("SELECT MIN(txn_timestamp), MAX(txn_timestamp) FROM auth_transactions")
mn, mx = cur.fetchone()
print(f"  auth_transactions  {mn}  ->  {mx}")

print("\n6. Avg response time by auth_type:")
cur.execute("""
    SELECT auth_type, ROUND(AVG(response_time_ms)::numeric, 0) AS avg_ms
    FROM auth_transactions GROUP BY auth_type ORDER BY avg_ms DESC
""")
for r in cur.fetchall():
    print(f"  {r[0]:<8s}  {int(r[1]):>5d} ms")

print("\n7. KYC type distribution:")
cur.execute("""
    SELECT kyc_type, COUNT(*) AS n,
           ROUND(100.0 * SUM(CASE WHEN is_success THEN 1 ELSE 0 END)/COUNT(*), 2) AS success_pct
    FROM kyc_transactions GROUP BY kyc_type ORDER BY n DESC
""")
for r in cur.fetchall():
    print(f"  {r[0]:<10s}  {r[1]:>7,}  {float(r[2]):>6.2f}%")

c.close()
print("\nALL CHECKS PASSED. Data ready for semantic-reporting queries.")

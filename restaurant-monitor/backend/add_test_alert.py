import psycopg2

conn = psycopg2.connect(
    host="localhost",
    port=5432,
    user="postgres",
    password="postgres123",
    dbname="restaurant_db",
)
cur = conn.cursor()
cur.execute(
    "INSERT INTO alerts (alert_type, message, status) VALUES (%s, %s, %s)",
    ("TEST_ALERT", "Manual test alert", "ACTIVE"),
)
conn.commit()
print("تمام، انضاف الصف")
cur.close()
conn.close()
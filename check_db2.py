import sqlite3
import json

conn = sqlite3.connect("C:/MyProjects/SnapBeatServer/jobs.db")
cursor = conn.cursor()
cursor.execute("SELECT id, status, stage, error, options FROM jobs ORDER BY id DESC LIMIT 1")
row = cursor.fetchone()
if row:
    print(f"ID: {row[0]}")
    print(f"STATUS: {row[1]}")
    print(f"STAGE: {row[2]}")
    print(f"ERROR: {row[3]}")
else:
    print("No jobs found")

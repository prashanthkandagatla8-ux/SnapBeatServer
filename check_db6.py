import sqlite3
import json

conn = sqlite3.connect("C:/MyProjects/SnapBeatServer/jobs/beatcanvas.db")
cursor = conn.cursor()
cursor.execute("SELECT id, status, stage, error, progress FROM jobs ORDER BY id DESC LIMIT 1")
rows = cursor.fetchall()
for row in rows:
    print(f"ID: {row[0]}, STATUS: {row[1]}, STAGE: '{row[2]}', PROGRESS: {row[4]}")

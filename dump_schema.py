import sqlite3
import sys

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding='utf-8')

con = sqlite3.connect('app/data/aura_store.db')
cur = con.cursor()
cur.execute("SELECT sql FROM sqlite_master WHERE type='table'")
for row in cur.fetchall():
    print(row[0])

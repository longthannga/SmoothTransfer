"""Small per-profile settings (for example: include Cal-GETC in my plan), kept in SQLite.
Stored in their own table so store.py does not need to change."""
import json

import store


def _db():
    store.db.execute("CREATE TABLE IF NOT EXISTS profile_settings(profile_code TEXT NOT NULL, key TEXT NOT NULL, "
                     "value TEXT, PRIMARY KEY(profile_code, key))")
    return store.db


def get_setting(profile_code, key, default=None):
    if not profile_code:
        return default
    row = _db().execute("SELECT value FROM profile_settings WHERE profile_code=? AND key=?",
                        (profile_code, key)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(profile_code, key, value):
    if not profile_code:
        return
    db = _db()
    db.execute("INSERT OR REPLACE INTO profile_settings(profile_code, key, value) VALUES (?,?,?)",
               (profile_code, key, json.dumps(value)))
    db.commit()

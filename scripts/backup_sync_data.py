#!/usr/bin/env python3
# Read-only snapshot of live Firestore sync stats, so a future "the count looks
# wrong" report has something to diff against instead of guessing (see the
# 2026-08-16 cross-contamination incident, where there was no reference point).
# Never writes to Firestore. Codes come from sync_codes.local.json (gitignored,
# not this script) so real sync codes never end up in the public repo -- backups
# are keyed by an arbitrary "account_N" label, not the code itself.
import datetime
import hashlib
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = None

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODES_FILE = os.path.join(REPO_ROOT, "sync_codes.local.json")
BACKUPS_DIR = os.path.join(REPO_ROOT, "backups")

API_KEY = "AIzaSyDnw1zOg6OtUI-akXzXG33PAOXOcLReRUc"
PROJECT_ID = "ila101-flashcards"


def sha256_hex(s):
    return hashlib.sha256(s.encode()).hexdigest()


def post_json(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as r:
        return json.loads(r.read())


def get_json(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as r:
        return json.loads(r.read())


def unwrap(fv):
    if fv is None:
        return None
    if "integerValue" in fv:
        return int(fv["integerValue"])
    if "doubleValue" in fv:
        return fv["doubleValue"]
    if "booleanValue" in fv:
        return fv["booleanValue"]
    if "mapValue" in fv:
        return {k: unwrap(v) for k, v in fv["mapValue"].get("fields", {}).items()}
    return None


def main():
    if not os.path.exists(CODES_FILE):
        print(f"No {CODES_FILE} -- nothing to back up, skipping.")
        return 0
    with open(CODES_FILE) as f:
        codes = json.load(f)["codes"]

    try:
        auth = post_json(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={API_KEY}",
            {"returnSecureToken": True},
        )
        token = auth["idToken"]
    except Exception as e:
        print(f"Backup skipped -- couldn't reach Firebase auth: {e}")
        return 0

    snapshot = {"takenAt": datetime.datetime.utcnow().isoformat() + "Z", "accounts": []}
    for i, code in enumerate(codes, 1):
        label = f"account_{i}"
        doc_id = sha256_hex(code)
        url = (f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}"
               f"/databases/(default)/documents/syncs/{doc_id}")
        try:
            doc = get_json(url, token)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                snapshot["accounts"].append({"account": label, "docId": doc_id, "exists": False})
            else:
                print(f"Backup skipped for {label} -- HTTP {e.code}")
            continue
        except Exception as e:
            print(f"Backup skipped for {label} -- {e}")
            continue

        fields = doc.get("fields", {})
        stats = unwrap(fields.get("stats")) or {}
        card_entries = [s for s in stats.values() if isinstance(s, dict)]
        daily_log = unwrap(fields.get("dailyLog")) or {}
        snapshot["accounts"].append({
            "account": label,
            "docId": doc_id,
            "exists": True,
            "totalSeen": sum(s.get("seen", 0) for s in card_entries),
            "totalGot": sum(s.get("got", 0) for s in card_entries),
            "totalMissed": sum(s.get("missed", 0) for s in card_entries),
            "cardCount": sum(1 for s in card_entries if s.get("seen")),
            "dailyLog": daily_log,
        })

    os.makedirs(BACKUPS_DIR, exist_ok=True)
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_path = os.path.join(BACKUPS_DIR, f"backup_{ts}.json")
    with open(out_path, "w") as f:
        json.dump(snapshot, f, indent=1)
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

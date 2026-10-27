"""Only called by explicit daily storage provisioning, not application startup."""
import json
import sys
from daily_guard import Ledger

values = json.load(sys.stdin)
ledger = Ledger('/run/daily/usage.sqlite3', values, None, create=sys.argv[1]=='init', recover=False)
if sys.argv[1] == 'resume':
    if len(sys.argv) != 3 or sys.argv[2] != '--acknowledge':
        raise SystemExit('Explicit acknowledgement required; inspect uncertain requests first.')
    ledger.resume()
print(json.dumps(ledger.status()))

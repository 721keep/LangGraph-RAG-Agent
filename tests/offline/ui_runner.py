"""Current UI and prior cases against one internal-only fictional HTTP service."""
import subprocess
import sys

for script in ('/m3/test_conversations.py', '/m3/test_previous.py'):
    subprocess.run([sys.executable, '-B', script], check=True)

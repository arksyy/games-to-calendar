#!/bin/bash
# Run games-to-calendar using the project's virtualenv.
#   ./run.sh        L'Antichambre B2 (Interligue)
#   ./run.sh gab    L’Antichambre F6+ (Saint-Aug)
# Works from any directory; resolves paths relative to this script.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/.venv/bin/python" "$DIR/main.py" "$@"

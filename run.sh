#\!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export GST_PLUGIN_PATH="$DIR/lib"
export LD_LIBRARY_PATH="$DIR/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

exec python3 "$DIR/main.py" "$@"

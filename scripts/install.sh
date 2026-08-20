#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
mkdir -p "$BIN_DIR"
chmod +x "$ROOT/herdctl.py"
cat > "$BIN_DIR/herdctl" <<EOF
#!/usr/bin/env bash
exec python3 "$ROOT/herdctl.py" "\$@"
EOF
chmod +x "$BIN_DIR/herdctl"
cat > "$BIN_DIR/infinity" <<EOF
#!/usr/bin/env bash
PY="$ROOT/.venv/bin/python"
if [ ! -x "\$PY" ]; then
  PY=python3
fi
exec "\$PY" "$ROOT/infinity.py" "\$@"
EOF
chmod +x "$BIN_DIR/infinity"
echo "Installed: $BIN_DIR/herdctl"
echo "Installed: $BIN_DIR/infinity"
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  echo 'Add to your shell profile: export PATH="$HOME/.local/bin:$PATH"'
fi
echo "Next: cd into your repo and run: herdctl init"

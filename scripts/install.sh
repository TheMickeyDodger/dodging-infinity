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
echo "Installed: $BIN_DIR/herdctl"
chmod +x "$ROOT/codexgw.py"
cat > "$BIN_DIR/codexgw" <<EOF
#!/usr/bin/env bash
exec python3 "$ROOT/codexgw.py" "\$@"
EOF
chmod +x "$BIN_DIR/codexgw"
echo "Installed: $BIN_DIR/codexgw"
chmod +x "$ROOT/tgop.py"
cat > "$BIN_DIR/tgop" <<EOF
#!/usr/bin/env bash
exec python3 "$ROOT/tgop.py" "\$@"
EOF
chmod +x "$BIN_DIR/tgop"
echo "Installed: $BIN_DIR/tgop"
chmod +x "$ROOT/dirun.py"
cat > "$BIN_DIR/dirun" <<EOF
#!/usr/bin/env bash
exec python3 "$ROOT/dirun.py" "\$@"
EOF
chmod +x "$BIN_DIR/dirun"
echo "Installed: $BIN_DIR/dirun"
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  echo 'Add to your shell profile: export PATH="$HOME/.local/bin:$PATH"'
fi
echo "Next: cd into your repo and run: herdctl init"

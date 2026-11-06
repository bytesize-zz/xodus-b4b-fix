#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "============================================================"
echo "  xodus-eac-fix — EAC Online Fix for Xodus Nexus"
echo "============================================================"
echo -e "${NC}"

# ── Detect Xodus B4B ──
B4B_DIR=""
for candidate in \
    "$HOME/Games/Xodus Nexus/Back-4-Blood" \
    "$HOME/Games/XodusNexus/Back-4-Blood" \
    "$HOME/.local/share/xodus-nexus/games/Back-4-Blood"; do
    if [ -d "$candidate/EasyAntiCheat" ]; then
        B4B_DIR="$candidate"
        break
    fi
done

if [ -z "$B4B_DIR" ]; then
    echo -e "${YELLOW}Back 4 Blood (Xodus) nao encontrado automaticamente.${NC}"
    echo "Digita o caminho da pasta do jogo:"
    read -r B4B_DIR
    if [ ! -d "$B4B_DIR/EasyAntiCheat" ]; then
        echo -e "${RED}Pasta invalida.${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}Xodus B4B:${NC} $B4B_DIR"
XODUS_DIR="$(dirname "$B4B_DIR")"

# ── Detect Steam B4B (for verification only) ──
STEAM_B4B=""
for candidate in \
    "$HOME/.local/share/Steam/steamapps/common/Back 4 Blood"; do
    if [ -f "$candidate/EasyAntiCheat/Certificates/runtime.conf" ]; then
        STEAM_B4B="$candidate"
        break
    fi
done

if [ -n "$STEAM_B4B" ]; then
    echo -e "${GREEN}Steam B4B:${NC} $STEAM_B4B"

    # Verify deployment IDs match
    XODUS_PID=$(grep -o '"productid"[^"]*"[^"]*"' "$B4B_DIR/EasyAntiCheat/Settings.json" | grep -o '"[^"]*"$' | tr -d '"')
    STEAM_PID=$(grep -o '"productid"[^"]*"[^"]*"' "$STEAM_B4B/EasyAntiCheat/Settings.json" | grep -o '"[^"]*"$' | tr -d '"')

    if [ "$XODUS_PID" = "$STEAM_PID" ]; then
        echo -e "${GREEN}EAC deployment IDs match.${NC}"
    else
        echo -e "${RED}WARNING: deployment IDs differ! Fix may not work.${NC}"
    fi

    # Copy EAC files from Steam to local files/ dir
    mkdir -p "$SCRIPT_DIR/files"
    cp "$STEAM_B4B/EasyAntiCheat/Certificates/runtime.conf" "$SCRIPT_DIR/files/"
    cp "$STEAM_B4B/EasyAntiCheat/EasyAntiCheat_EOS_Setup.exe" "$SCRIPT_DIR/files/"
    echo -e "${GREEN}EAC files sourced from Steam installation.${NC}"
else
    echo -e "${RED}Steam B4B not found!${NC}"
    echo "This fix requires Back 4 Blood installed on Steam"
    echo "for the Win64 EAC files. Install it and run again."
    exit 1
fi

# ── Install dependency ──
if ! command -v inotifywait &>/dev/null; then
    echo ""
    echo -e "${CYAN}Installing inotify-tools...${NC}"
    if command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm inotify-tools
    elif command -v apt &>/dev/null; then
        sudo apt install -y inotify-tools
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y inotify-tools
    elif command -v zypper &>/dev/null; then
        sudo zypper install -y inotify-tools
    else
        echo -e "${RED}Install inotify-tools manually and run again.${NC}"
        exit 1
    fi
fi

# ── Install runtime.conf ──
echo ""
CERT_DIR="$B4B_DIR/EasyAntiCheat/Certificates"
if [ -f "$CERT_DIR/runtime.conf" ]; then
    echo -e "${GREEN}[1/3] runtime.conf already present.${NC}"
else
    cp "$SCRIPT_DIR/files/runtime.conf" "$CERT_DIR/runtime.conf"
    echo -e "${GREEN}[1/3] runtime.conf installed.${NC}"
fi

# ── Install watcher ──
WATCHER="$XODUS_DIR/b4b-eac-fix.sh"
STEAM_EAC_EXE="$SCRIPT_DIR/files/EasyAntiCheat_EOS_Setup.exe"

cat > "$WATCHER" << EOF
#!/bin/bash
XODUS_DIR="$XODUS_DIR"
STEAM_EAC="$STEAM_EAC_EXE"

echo "============================================================"
echo "  xodus-eac-fix — EAC Chain Watcher"
echo "  Monitoring for chain directories..."
echo "  Launch B4B through Xodus Nexus normally."
echo "  Ctrl+C to stop when done playing."
echo "============================================================"
echo ""

inotifywait -m -r "\$XODUS_DIR" -e create -e moved_to --format '%w%f' 2>/dev/null | \\
while IFS= read -r filepath; do
    filename=\$(basename "\$filepath")
    if [[ "\$filename" == "EasyAntiCheat_EOS_Setup.exe" ]]; then
        sleep 0.05
        cp "\$STEAM_EAC" "\$filepath" 2>/dev/null
        if [ \$? -eq 0 ]; then
            echo "[\$(date +%H:%M:%S)] EAC fixed — GDK swapped to Win64. Play online!"
        else
            echo "[\$(date +%H:%M:%S)] Failed to swap EAC exe."
        fi
    fi
done
EOF

chmod +x "$WATCHER"
echo -e "${GREEN}[2/3] Watcher installed: $WATCHER${NC}"

# ── Desktop shortcut ──
DESKTOP_FILE="$HOME/.local/share/applications/b4b-eac-fix.desktop"
cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Name=B4B EAC Fix
Comment=Run before Back 4 Blood to enable online play on Xodus Nexus
Exec=bash $WATCHER
Terminal=true
Type=Application
Icon=applications-games
Categories=Game;
EOF
echo -e "${GREEN}[3/3] Desktop shortcut created.${NC}"

echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  Done! To play online:${NC}"
echo -e "${GREEN}  1. Open 'B4B EAC Fix' from app menu${NC}"
echo -e "${GREEN}  2. Launch B4B through Xodus Nexus${NC}"
echo -e "${GREEN}  3. Play online — Ctrl+C when done${NC}"
echo -e "${GREEN}============================================================${NC}"

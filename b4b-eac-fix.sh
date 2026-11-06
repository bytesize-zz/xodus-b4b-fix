#!/bin/bash
# EAC Chain Interceptor para Back 4 Blood (Xodus Nexus)
#
# O Xodus valida EasyAntiCheat_EOS_Setup.exe contra o manifesto MSIXVC (GDK, 962560 bytes).
# Mas o Proton EAC Runtime só reconhece a versão Win64 (Steam).
# Este script monitora a criação da chain directory e troca o exe 
# DEPOIS da validação do Xodus, ANTES do game process ler o arquivo.

XODUS_DIR="/home/lucascachyos/Games/Xodus Nexus"
STEAM_EAC="/home/lucascachyos/.local/share/Steam/steamapps/common/Back 4 Blood/EasyAntiCheat/EasyAntiCheat_EOS_Setup.exe"

echo "╔═══════════════════════════════════════════════════╗"
echo "║   EAC Chain Fix — Back 4 Blood (Xodus Nexus)     ║"
echo "╠═══════════════════════════════════════════════════╣"
echo "║  Monitorando criação de chain directories...      ║"
echo "║  Lança o B4B pelo Xodus Nexus normalmente.       ║"
echo "║  Ctrl+C pra parar quando terminar de jogar.      ║"
echo "╚═══════════════════════════════════════════════════╝"
echo ""

# Monitora criação de arquivos dentro de qualquer .xodus-chain-* dir
inotifywait -m -r "$XODUS_DIR" -e create -e moved_to --format '%w%f' 2>/dev/null | \
while IFS= read -r filepath; do
    filename=$(basename "$filepath")
    if [[ "$filename" == "EasyAntiCheat_EOS_Setup.exe" ]]; then
        # Pequeno delay pra garantir que o arquivo terminou de ser escrito
        sleep 0.05
        # Trocar pelo Steam Win64 version
        cp "$STEAM_EAC" "$filepath" 2>/dev/null
        if [ $? -eq 0 ]; then
            echo "[$(date +%H:%M:%S)] ✅ EAC exe trocado na chain: $filepath"
            echo "                   GDK → Win64 (Steam) — Proton EAC Runtime vai reconhecer"
        else
            echo "[$(date +%H:%M:%S)] ❌ Falhou ao trocar: $filepath"
        fi
    fi
done

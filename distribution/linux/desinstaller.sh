#!/usr/bin/env bash
# Désinstalle « Atelier partitions » pour l'utilisateur courant.
# Audiveris, Tesseract et Python restent installés : d'autres programmes peuvent s'en servir.
set -euo pipefail

DEST="${XDG_DATA_HOME:-$HOME/.local/share}/atelier-partitions"
PROGRAM="$DEST/programme"

if [ -x "$PROGRAM/.venv/bin/python" ]; then
  "$PROGRAM/.venv/bin/python" "$PROGRAM/lanceur.py" --arreter >/dev/null 2>&1 || true
fi
rm -rf "$PROGRAM"
rm -f "$HOME/.local/bin/atelier-partitions" "${XDG_DATA_HOME:-$HOME/.local/share}/applications/atelier-partitions.desktop"
echo "Le programme est désinstallé."

if [ -d "$DEST/data" ] || [ -d "$DEST/private" ]; then
  answer=n
  if [ "${1:-}" = --tout ]; then
    answer=o
  elif [ -t 0 ]; then
    read -r -p "Supprimer aussi vos conversions et vos réglages ($DEST) ? Cette action est définitive. [o/N] " answer
  fi
  case "$answer" in
    o|O|oui|OUI) rm -rf "$DEST"; echo "Conversions et réglages supprimés." ;;
    *) echo "Vos conversions et réglages sont conservés dans : $DEST" ;;
  esac
fi
echo "Audiveris et Tesseract restent installés. Pour les retirer : sudo apt remove audiveris tesseract-ocr tesseract-ocr-fra"

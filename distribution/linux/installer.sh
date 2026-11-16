#!/usr/bin/env bash
# Installe « Atelier partitions » pour l'utilisateur courant (Ubuntu, Debian et dérivés, 64 bits).
#
# Internet est nécessaire pendant l'installation (environ 300 Mo) ; ensuite tout fonctionne hors ligne.
# Les droits administrateur (sudo) ne sont demandés que pour installer les logiciels manquants :
# Python, Tesseract (lecture du texte) et Audiveris (lecture des notes).
set -euo pipefail

AUDIVERIS_VERSION=5.11.0
AUDIVERIS_BASE="https://github.com/Audiveris/audiveris/releases/download/${AUDIVERIS_VERSION}"
AUDIVERIS_2204=Audiveris-5.11.0-ubuntu22.04-x86_64.deb
AUDIVERIS_2204_SHA256=ae714594f40e54b1a4951fc3f914f08ae38fe5d07b7f2283b1a904fdb6e0a318
AUDIVERIS_2404=Audiveris-5.11.0-ubuntu24.04-x86_64.deb
AUDIVERIS_2404_SHA256=f20113aaa33b3149ec8d6a09b2a7963360e65fafd92d69389987a85bbc3ec7a3
PACKAGES=(python3 python3-venv tesseract-ocr tesseract-ocr-fra)
# Langues de lecture du texte pour Audiveris (dépôt officiel tesseract-ocr/tessdata, version 4.1.0).
TESSDATA_BASE="https://raw.githubusercontent.com/tesseract-ocr/tessdata/4.1.0"
TESSDATA_SHA256_eng=daa0c97d651c19fba3b25e81317cd697e9908c8208090c94c3905381c23fc047
TESSDATA_SHA256_fra=eac01c1d72540d6090facb7b2f42dd0a2ee8fc57c5be1b20548ae668e2761913
AUDIVERIS_TESSDATA="${XDG_CONFIG_HOME:-$HOME/.config}/AudiverisLtd/audiveris/tessdata"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$HERE/programme"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/atelier-partitions"
PROGRAM="$DEST/programme"
BIN="$HOME/.local/bin"
LAUNCHER="$BIN/atelier-partitions"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mErreur : %s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$SOURCE/app.py" ] || fail "dossier « programme » introuvable à côté de ce script."
[ "$(uname -m)" = x86_64 ] || fail "seuls les PC 64 bits (x86_64) sont pris en charge par Audiveris."
command -v apt-get >/dev/null || fail "ce script est prévu pour Ubuntu, Debian et leurs dérivés (apt)."

# 1. Logiciels du système ----------------------------------------------------------
missing=()
for package in "${PACKAGES[@]}"; do
  dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q "install ok installed" || missing+=("$package")
done
if [ ${#missing[@]} -gt 0 ]; then
  say "Installation des logiciels manquants : ${missing[*]} (mot de passe administrateur demandé)"
  sudo apt-get update
  sudo apt-get install -y "${missing[@]}"
else
  say "Python et Tesseract sont déjà installés."
fi

# 2. Audiveris ---------------------------------------------------------------------
if command -v audiveris >/dev/null || command -v Audiveris >/dev/null || [ -x /opt/audiveris/bin/Audiveris ]; then
  say "Audiveris est déjà installé."
else
  . /etc/os-release
  major="${VERSION_ID%%.*}"
  if { [ "${ID:-}" = ubuntu ] && [ "${major:-0}" -ge 24 ]; } || { [ "${ID:-}" = debian ] && [ "${major:-0}" -ge 13 ]; } \
     || { [ "${ID_LIKE:-}" != "${ID_LIKE#*ubuntu}" ] && [ "${UBUNTU_CODENAME:-}" != jammy ] && [ "${UBUNTU_CODENAME:-}" != focal ]; }; then
    file="$AUDIVERIS_2404"; sum="$AUDIVERIS_2404_SHA256"
  else
    file="$AUDIVERIS_2204"; sum="$AUDIVERIS_2204_SHA256"
  fi
  say "Téléchargement d'Audiveris ${AUDIVERIS_VERSION} (environ 80 Mo)…"
  temp="$(mktemp -d)"
  trap 'rm -rf "$temp"' EXIT
  curl -fL --retry 3 -o "$temp/$file" "$AUDIVERIS_BASE/$file" || fail "téléchargement d'Audiveris impossible. Vérifiez la connexion internet."
  echo "$sum  $temp/$file" | sha256sum -c --quiet - || fail "le fichier téléchargé ne correspond pas à l'original publié par Audiveris."
  say "Installation d'Audiveris (mot de passe administrateur demandé)"
  sudo apt-get install -y "$temp/$file"
fi

# 2 bis. Français et anglais pour qu'Audiveris lise les paroles ----------------------
mkdir -p "$AUDIVERIS_TESSDATA"
for language in eng fra; do
  sum_var="TESSDATA_SHA256_$language"
  target="$AUDIVERIS_TESSDATA/$language.traineddata"
  if [ -f "$target" ] && echo "${!sum_var}  $target" | sha256sum -c --quiet - 2>/dev/null; then
    continue
  fi
  say "Téléchargement de la langue « $language » pour la lecture des paroles…"
  part="$(mktemp "$AUDIVERIS_TESSDATA/.$language.XXXXXX")"
  curl -fL --retry 3 -o "$part" "$TESSDATA_BASE/$language.traineddata" || { rm -f "$part"; fail "téléchargement de la langue $language impossible."; }
  echo "${!sum_var}  $part" | sha256sum -c --quiet - || { rm -f "$part"; fail "le fichier de langue $language ne correspond pas à l'original."; }
  chmod 644 "$part"
  mv "$part" "$target"
done

# 3. Programme et bibliothèques Python ----------------------------------------------
say "Installation du programme dans $PROGRAM"
if [ -x "$PROGRAM/.venv/bin/python" ]; then
  "$PROGRAM/.venv/bin/python" "$PROGRAM/lanceur.py" --arreter >/dev/null 2>&1 || true
fi
rm -rf "$PROGRAM"
mkdir -p "$DEST"
cp -r "$SOURCE" "$PROGRAM"
python3 -m venv "$PROGRAM/.venv"
say "Installation des bibliothèques Python (PyMuPDF, numpy, SciPy, Pillow)…"
"$PROGRAM/.venv/bin/python" -m pip install --disable-pip-version-check --quiet -r "$PROGRAM/requirements-paroles.txt" \
  || fail "installation des bibliothèques Python impossible. Vérifiez la connexion internet."

# 4. Commande et icône dans le menu -------------------------------------------------
mkdir -p "$BIN" "$DESKTOP_DIR"
cat > "$LAUNCHER" <<EOF
#!/bin/sh
exec "$PROGRAM/.venv/bin/python" "$PROGRAM/lanceur.py" "\$@"
EOF
chmod 755 "$LAUNCHER"
cat > "$DESKTOP_DIR/atelier-partitions.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Atelier partitions
Comment=Convertir des partitions PDF en MusicXML et extraire les paroles
Exec="$LAUNCHER"
Icon=$PROGRAM/static/icone.svg
Terminal=false
Categories=AudioVideo;Audio;Music;
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true

say "Vérification…"
"$LAUNCHER" --sans-navigateur || fail "l'application ne démarre pas. Détails dans $DEST/atelier.log"
"$PROGRAM/.venv/bin/python" - <<'EOF'
import json, os
from urllib.request import urlopen
base = f"http://127.0.0.1:{os.environ.get('ATELIER_PORT', '8765')}"
engine = json.load(urlopen(base + '/api/health', timeout=5))['engine']
lyrics = json.load(urlopen(base + '/api/paroles/statut', timeout=5))
print('  Lecture des notes (Audiveris) :', 'prête' if engine else 'INTROUVABLE')
print('  Extraction des paroles       :', 'prête' if lyrics['available'] else 'INDISPONIBLE')
print('  Lecture du texte des scans   :', 'prête' if lyrics['french'] else 'sans le français')
EOF
if [ -f "$AUDIVERIS_TESSDATA/fra.traineddata" ]; then
  echo "  Paroles lues par Audiveris   : prêtes (français)"
else
  echo "  Paroles lues par Audiveris   : sans le français"
fi

say "Atelier partitions est installé."
echo "  • Ouvrez-le depuis le menu des applications (« Atelier partitions »)"
echo "    ou avec la commande : atelier-partitions"
echo "  • Vos conversions sont rangées dans : $DEST/data"
echo "  • Pour désinstaller : $PROGRAM/desinstaller.sh"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "  (La commande sera disponible après une reconnexion : $BIN n'est pas encore dans le PATH.)" ;; esac

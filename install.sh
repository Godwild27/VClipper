#!/bin/bash
# install.sh — Installation "De A à Z" de VClipper sur un Ubuntu VPS frais
# Usage : sudo bash install.sh

set -e

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║      VClipper — Installation Complète    ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Vérifier si on est en root (utile pour l'installation système globale)
if [ "$EUID" -ne 0 ]
  then echo "❌ Veuillez lancer ce script avec sudo : sudo bash install.sh"
  exit
fi

# ── 1. Mise à jour système ───────────────────────────────────────────────────
echo "▶ 1/5 - Mise à jour des paquets système..."
apt-get update -y -q
apt-get upgrade -y -q

# ── 2. Dépendances de base & FFmpeg ──────────────────────────────────────────
echo "▶ 2/5 - Installation de FFmpeg, curl et Python..."
apt-get install -y -q curl wget software-properties-common
apt-get install -y -q ffmpeg python3 python3-pip python3-venv

# ── 3. Cloudflared (Tunnel) ──────────────────────────────────────────────────
echo "▶ 3/5 - Installation de Cloudflared..."
if ! command -v cloudflared &> /dev/null; then
  wget -q "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb" -O /tmp/cloudflared.deb
  dpkg -i /tmp/cloudflared.deb
  rm /tmp/cloudflared.deb
  echo "  ✅ cloudflared $(cloudflared --version) installé"
else
  echo "  ✅ cloudflared est déjà installé."
fi

# ── 4. Node.js & NPM (v22 via NodeSource) ────────────────────────────────────
echo "▶ 4/5 - Installation de Node.js v22..."
if ! command -v node &> /dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y -q nodejs
  echo "  ✅ Node $(node -v) et NPM $(npm -v) installés"
else
  echo "  ✅ Node.js est déjà installé ($(node -v))."
fi

# ── 5. Installation Globale de VClipper ──────────────────────────────────────
echo "▶ 5/5 - Installation de VClipper via NPM..."
# On installe le paquet globalement depuis le dossier courant
npm install -g .

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     🎉  Installation terminée de A à Z !  ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Vous pouvez maintenant lancer l'application de n'importe où sur votre VPS en tapant :"
echo "👉  vclipper"
echo ""
echo "(N'oubliez pas que lors du premier lancement, vclipper installera automatiquement ses modules Python)."

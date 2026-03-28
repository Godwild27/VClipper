# VClipper 🎬

Transformez automatiquement vos longues vidéos en **clips TikTok viraux** grâce à l'IA.

## Architecture

```
Votre VPS ──► python main.py ──► Tunnel Cloudflare ──► URL publique https://xxx.trycloudflare.com
                   │                                         │
                FastAPI Server ◄────────────────────── Navigateur (n'importe où)
                   │
                Pipeline 13 étapes
                   ├─ FFmpeg (extraction, découpe, export)
                   ├─ Gemini API (transcription + analyse viralité)
                   └─ OpenCV (face detection CSRT + reframe 9:16)
```

## Installation (Ubuntu VPS)

```bash
# Cloner / copier le projet sur votre VPS
cd VClipper

# Lancer le script d'installation automatique
bash install.sh

# Configurer la clé API Gemini
nano .env
# Mettre : GEMINI_API_KEY=votre_cle_ici

# Activer l'environnement et lancer
source .venv/bin/activate
python main.py
```

## Démarrage

```
🚀 Démarrage du tunnel Cloudflare...
🖥️  Démarrage du serveur sur le port 8000...

══════════════════════════════════════════════════════════
🌐  VClipper est accessible publiquement !
🔗  URL : https://abc123xyz.trycloudflare.com
══════════════════════════════════════════════════════════
```

Ouvrez l'URL dans n'importe quel navigateur 🌍

## Variables d'environnement

| Variable | Obligatoire | Défaut | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | ✅ | — | Clé API Google Gemini |
| `MAX_THREADS` | ❌ | `2` | Cœurs CPU max pour FFmpeg |
| `MAX_CLIPS` | ❌ | `5` | Nombre max de clips générés |

## Pipeline

| Étape | Nom | Outil |
|---|---|---|
| 1 | Validation vidéo | FFmpeg/ffprobe |
| 2 | Extraction audio WAV 16kHz | FFmpeg |
| 3 | Transcription avec timestamps | Gemini API |
| 4 | Segmentation (blocs 10-30s) | Python |
| 5 | Analyse viralité TikTok | Gemini API |
| 6 | Sélection meilleurs clips | Python |
| 7 | Découpe segments | FFmpeg |
| 8 | Détection visage | OpenCV Haar |
| 9 | Suivi visage (CSRT) | OpenCV |
| 10 | Reframe 9:16 dynamique | OpenCV |
| 11 | Génération SRT | Python |
| 12 | Incrustation sous-titres | FFmpeg |
| 13 | Export 1080x1920 TikTok | FFmpeg |

## Dépendances système

- **FFmpeg** — `sudo apt install ffmpeg`
- **cloudflared** — installé via `install.sh`
- **Python 3.10+** — préinstallé sur Ubuntu 22.04+

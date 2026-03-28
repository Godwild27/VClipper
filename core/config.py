"""
core/config.py
--------------
Configuration centrale de VClipper.
Lit les variables d'environnement depuis .env et expose les chemins du projet.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Charge le fichier .env à la racine du projet
load_dotenv()

# ── Chemins ──────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent.parent
UPLOAD_DIR  = BASE_DIR / "uploads"
TEMP_DIR    = BASE_DIR / "temp"
OUTPUT_DIR  = BASE_DIR / "outputs"
STATIC_DIR  = BASE_DIR / "static"

# Création automatique des dossiers nécessaires
for _dir in (UPLOAD_DIR, TEMP_DIR, OUTPUT_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# ── Clés API ─────────────────────────────────────────────────────────────────
NVIDIA_API_KEY: str = os.getenv("NVIDIA_API_KEY", "")

def set_nvidia_api_key(api_key: str):
    """Met à jour la clé API en mémoire et dans le fichier .env"""
    global NVIDIA_API_KEY
    NVIDIA_API_KEY = api_key.strip()

    env_path = BASE_DIR / ".env"
    lines = []
    found = False
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
    with open(env_path, "w", encoding="utf-8") as f:
        for line in lines:
            if line.startswith("NVIDIA_API_KEY="):
                f.write(f"NVIDIA_API_KEY={NVIDIA_API_KEY}\n")
                found = True
            else:
                f.write(line)
        if not found:
            f.write(f"NVIDIA_API_KEY={NVIDIA_API_KEY}\n")

# ── Paramètres de traitement ─────────────────────────────────────────────────
MAX_THREADS: int = int(os.getenv("MAX_THREADS", "2"))   # Cœurs CPU max pour FFmpeg
MAX_CLIPS:   int = int(os.getenv("MAX_CLIPS",   "5"))   # Clips max par vidéo

# ── Paramètres vidéo cible ───────────────────────────────────────────────────
TARGET_WIDTH:  int = 1080
TARGET_HEIGHT: int = 1920
TARGET_RATIO:  float = TARGET_WIDTH / TARGET_HEIGHT   # 9/16 = 0.5625

# ── Score minimum pour sélectionner un clip ──────────────────────────────────
MIN_CLIP_SCORE: float = 6.0   # Sur 10

# ── Paramètres du modèle NVIDIA ─────────────────────────────────────────────
NVIDIA_BASE_URL:    str = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL_TEXT:  str = "nvidia/nemotron-3-super-120b-a12b"
# Nvidia/OpenAI utilise standardement 'whisper-1' ou un modèle équivalent pour le STT v1
NVIDIA_MODEL_AUDIO: str = "" # Laissé vide pour utiliser le modèle STT par défaut de l'API s'il est requis, ou whisper-1

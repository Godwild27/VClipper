"""
utils/youtube.py
----------------
Téléchargement de vidéos YouTube via yt-dlp.
- Sans filigrane / watermark
- Meilleure qualité disponible (mais limitée à 1080p pour économiser le CPU)
- Nettoyage automatique si annulé
- Auto-mise à jour de yt-dlp au démarrage (YouTube change régulièrement)
"""

import logging
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("vclipper.youtube")


def ensure_ytdlp_updated():
    """Met à jour yt-dlp au démarrage pour éviter les erreurs liées
    aux changements fréquents de YouTube."""
    try:
        logger.info("🔄 Mise à jour de yt-dlp...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            # Extraire la version installée
            for line in result.stdout.splitlines():
                if "Successfully installed" in line:
                    logger.info(f"✅ {line.strip()}")
                    return
            logger.info("✅ yt-dlp est déjà à jour.")
        else:
            logger.warning(f"⚠️ Mise à jour yt-dlp échouée : {result.stderr[-200:]}")
    except Exception as e:
        logger.warning(f"⚠️ Impossible de mettre à jour yt-dlp : {e}")


# Auto-update au chargement du module
ensure_ytdlp_updated()


# Patterns d'URL YouTube acceptés
YOUTUBE_PATTERNS = [
    r"(?:https?://)?(?:www\.)?youtube\.com/watch\?v=[\w-]+",
    r"(?:https?://)?(?:www\.)?youtu\.be/[\w-]+",
    r"(?:https?://)?(?:www\.)?youtube\.com/shorts/[\w-]+",
    r"(?:https?://)?(?:m\.)?youtube\.com/watch\?v=[\w-]+",
]


def is_youtube_url(url: str) -> bool:
    """Vérifie si une chaîne est une URL YouTube valide."""
    url = url.strip()
    return any(re.match(pattern, url) for pattern in YOUTUBE_PATTERNS)


def download_youtube(url: str, output_dir: Path, job_id: str) -> Path:
    """
    Télécharge une vidéo YouTube avec yt-dlp.

    Stratégie :
    - Meilleure qualité vidéo ≤ 1080p + meilleur audio
    - Fusion dans un fichier MP4 (pas de filigrane)
    - Nom de fichier déterministe basé sur le job_id

    Args:
        url        : URL YouTube (watch, shorts, youtu.be)
        output_dir : dossier où sauvegarder la vidéo
        job_id     : identifiant du job pour nommer le fichier

    Returns:
        Path de la vidéo téléchargée (.mp4)

    Raises:
        RuntimeError si le téléchargement échoue
        ValueError si l'URL est invalide ou privée
    """
    url = url.strip()
    output_path = output_dir / f"{job_id}_yt.mp4"

    logger.info(f"📥 Téléchargement YouTube : {url}")

    # Vérifier d'abord les métadonnées (URL valide ? Vidéo accessible ?)
    _check_video_accessible(url)

    cmd = [
        sys.executable, "-m", "yt_dlp",    # Toujours utiliser l'environnement virtuel actuel
        "--no-playlist",                   # Ignorer les playlists, 1 seule vidéo
        # Sélection du format : meilleure vidéo ≤1080p + meilleur audio → merge MP4
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "--merge-output-format", "mp4",    # Toujours sortir en MP4
        "--no-warnings",
        "--no-part",                       # Pas de fichiers .part temporaires
        "--newline",                       # Log ligne par ligne (pour le suivi)
        "--no-check-certificate",
        "--extractor-args", "youtube:player_client=web,default",
        "-o", str(output_path),            # Chemin de sortie fixe
        url,
    ]

    logger.debug(f"[yt-dlp] {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,   # 10 minutes max pour les longues vidéos
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        # Parser les erreurs communes pour un message lisible
        if "Private video" in stderr or "This video is private" in stderr:
            raise ValueError("Cette vidéo est privée et ne peut pas être téléchargée.")
        if "Video unavailable" in stderr or "unavailable" in stderr.lower():
            raise ValueError("Cette vidéo est indisponible ou a été supprimée.")
        if "Sign in" in stderr or "age" in stderr.lower():
            raise ValueError("Cette vidéo requiert une connexion ou a une restriction d'âge.")
        if "HTTP Error 429" in stderr:
            raise RuntimeError("Trop de requêtes YouTube. Réessayez dans quelques minutes.")
        logger.error(f"[yt-dlp] Erreur:\n{stderr}")
        raise RuntimeError(f"Échec du téléchargement : {stderr[-400:]}")

    if not output_path.exists():
        # yt-dlp peut parfois changer l'extension → chercher le fichier créé
        alternatives = list(output_dir.glob(f"{job_id}_yt.*"))
        if alternatives:
            actual = alternatives[0]
            actual.rename(output_path)
        else:
            raise RuntimeError("Le fichier téléchargé est introuvable après yt-dlp.")

    size_mb = output_path.stat().st_size / (1024 * 1024)
    logger.info(f"✅ Vidéo YouTube téléchargée : {output_path.name} ({size_mb:.1f} MB)")
    return output_path


def _check_video_accessible(url: str):
    """Vérifie rapidement que la vidéo est accessible avant de la télécharger."""
    result = subprocess.run(
        [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist",
            "--skip-download",
            "--dump-json",
            "--no-check-certificate",
            "--no-cache-dir",
            "--extractor-args", "youtube:player_client=web,default",
            url
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        err = result.stderr.strip()
        logger.error(f"[yt-dlp check] Erreur brute : {err}")
        if "Private" in err:
            raise ValueError("Vidéo privée.")
        if "unavailable" in err.lower():
            raise ValueError("Vidéo indisponible ou supprimée.")
        if "Sign in" in err:
            raise ValueError("Cette vidéo nécessite une connexion YouTube (restriction d'âge ou contenu limité).")
        if "HTTP Error 403" in err or "Forbidden" in err:
            raise ValueError("YouTube a bloqué la requête (erreur 403). yt-dlp est peut-être obsolète.")
        if "HTTP Error 429" in err:
            raise ValueError("Trop de requêtes YouTube. Réessayez dans quelques minutes.")
        # Message générique avec la vraie erreur
        raise ValueError(f"Impossible de récupérer la vidéo. Erreur : {err[-300:]}")


def get_video_info(url: str) -> dict:
    """
    Récupère les métadonnées d'une vidéo YouTube sans la télécharger.

    Returns:
        dict avec 'title', 'duration', 'uploader', 'thumbnail'
    """
    import json

    result = subprocess.run(
        [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist",
            "--skip-download",
            "--dump-json",
            "--no-check-certificate",
            "--no-cache-dir",
            "--extractor-args", "youtube:player_client=web,default",
            url.strip()
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        error_msg = result.stderr.strip()
        logger.error(f"[yt-dlp info] Erreur brute : {error_msg}")
        # Messages précis selon l'erreur
        if "Sign in to confirm" in error_msg or "Sign in" in error_msg:
            raise ValueError("Cette vidéo nécessite une connexion YouTube (restriction d'âge ou contenu limité).")
        if "Private video" in error_msg:
            raise ValueError("Cette vidéo est privée.")
        if "Video unavailable" in error_msg or "unavailable" in error_msg.lower():
            raise ValueError("Cette vidéo est indisponible ou a été supprimée.")
        if "HTTP Error 403" in error_msg or "Forbidden" in error_msg:
            raise ValueError("YouTube a bloqué la requête. Essayez de mettre à jour yt-dlp : pip install -U yt-dlp")
        if "HTTP Error 429" in error_msg:
            raise ValueError("Trop de requêtes YouTube. Réessayez dans quelques minutes.")
        if "Incomplete data" in error_msg or "Got error" in error_msg:
            raise ValueError("Erreur de connexion avec YouTube. Vérifiez votre réseau et réessayez.")
        # Dernier recours : afficher la fin de l'erreur brute
        last_line = error_msg.split('\n')[-1] if error_msg else "Erreur inconnue"
        raise ValueError(f"Impossible de récupérer les données de la vidéo. {last_line}")

    try:
        info = json.loads(result.stdout)
        return {
            "title":     info.get("title", "Vidéo YouTube"),
            "duration":  info.get("duration", 0),
            "uploader":  info.get("uploader", ""),
            "thumbnail": info.get("thumbnail", ""),
        }
    except json.JSONDecodeError:
        logger.error(f"[yt-dlp] JSON invalide reçu, stdout={result.stdout[:200]}")
        raise ValueError("Erreur lors de la lecture des données YouTube. Réessayez.")

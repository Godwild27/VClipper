"""
utils/video.py
--------------
Toutes les opérations vidéo via FFmpeg :
  - Validation du fichier d'entrée
  - Extraction audio WAV
  - Découpe de segments
  - Recadrage 9:16 avec cropbox lissée
  - Incrustation des sous-titres
  - Export final 1080x1920

Toutes les commandes FFmpeg limitent les threads CPU via MAX_THREADS.
"""

import subprocess
import logging
import json
import shutil
from pathlib import Path

from core.config import MAX_THREADS, TARGET_WIDTH, TARGET_HEIGHT

logger = logging.getLogger("vclipper.video")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internes
# ─────────────────────────────────────────────────────────────────────────────

def _run(cmd: list[str], step_name: str = "") -> subprocess.CompletedProcess:
    """Lance une commande FFmpeg et lève une exception si elle échoue."""
    logger.debug(f"[FFmpeg:{step_name}] {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"[FFmpeg:{step_name}] ERREUR:\n{result.stderr}")
        raise RuntimeError(
            f"FFmpeg a échoué lors de '{step_name}': {result.stderr[-500:]}"
        )
    return result


def _probe(video_path: Path) -> dict:
    """Utilise ffprobe pour lire les métadonnées d'une vidéo."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(f"Impossible de lire la vidéo : {video_path.name}")
    return json.loads(result.stdout)


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPE 1 — Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_video(video_path: Path) -> dict:
    """
    Valide le fichier vidéo et retourne ses métadonnées.

    Returns:
        dict avec 'duration', 'width', 'height', 'fps', 'has_audio'

    Raises:
        ValueError si le fichier est invalide ou trop court.
    """
    ALLOWED_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}
    MIN_DURATION = 30  # secondes

    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {path}")
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Format non supporté : {path.suffix}")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg n'est pas installé ou pas dans le PATH.")

    probe = _probe(path)
    fmt   = probe.get("format", {})
    duration = float(fmt.get("duration", 0))

    if duration < MIN_DURATION:
        raise ValueError(
            f"Vidéo trop courte ({duration:.0f}s). Minimum requis : {MIN_DURATION}s"
        )

    # Extraire infos du flux vidéo principal
    video_stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "video"),
        {}
    )
    audio_stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "audio"),
        None
    )

    # Calcul FPS
    fps_raw = video_stream.get("r_frame_rate", "25/1")
    try:
        num, den = map(int, fps_raw.split("/"))
        fps = num / den if den else 25.0
    except Exception:
        fps = 25.0

    metadata = {
        "duration":  duration,
        "width":     int(video_stream.get("width",  0)),
        "height":    int(video_stream.get("height", 0)),
        "fps":       round(fps, 3),
        "has_audio": audio_stream is not None,
        "codec":     video_stream.get("codec_name", "unknown"),
    }

    logger.info(
        f"✅ Vidéo validée : {path.name} — "
        f"{metadata['duration']:.1f}s, {metadata['width']}x{metadata['height']}, "
        f"{metadata['fps']}fps"
    )
    return metadata


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPE 2 — Extraction audio
# ─────────────────────────────────────────────────────────────────────────────

def extract_audio(video_path: Path, output_dir: Path) -> Path:
    """
    Extrait l'audio de la vidéo en WAV mono 16kHz (optimal pour Gemini/Whisper).

    Args:
        video_path : chemin de la vidéo source
        output_dir : dossier de sortie

    Returns:
        Path du fichier audio .wav extrait
    """
    wav_path = output_dir / f"{Path(video_path).stem}_audio.wav"

    cmd = [
        "ffmpeg", "-y",
        "-i",    str(video_path),
        "-vn",                        # Pas de flux vidéo
        "-ac",   "1",                 # Mono
        "-ar",   "16000",             # 16 kHz
        "-acodec", "pcm_s16le",       # WAV non compressé
        "-threads", str(MAX_THREADS),
        str(wav_path),
    ]
    _run(cmd, "extract_audio")
    logger.info(f"✅ Audio extrait : {wav_path}")
    return wav_path


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPE 7 — Découpe des clips
# ─────────────────────────────────────────────────────────────────────────────

def cut_clip(
    video_path: Path,
    start:      float,
    end:        float,
    output_path: Path,
) -> Path:
    """
    Découpe un segment de la vidéo sans re-encodage (très rapide).

    Args:
        video_path  : vidéo source
        start       : timestamp de début (secondes)
        end         : timestamp de fin (secondes)
        output_path : chemin du clip découpé

    Returns:
        Path du clip découpé
    """
    duration = end - start
    if duration <= 0:
        raise ValueError(f"Durée invalide : start={start}, end={end}")

    cmd = [
        "ffmpeg", "-y",
        "-ss",       str(start),
        "-i",        str(video_path),
        "-t",        str(duration),
        "-c",        "copy",             # Pas de re-encodage → rapide
        "-avoid_negative_ts", "make_zero",
        str(output_path),
    ]
    _run(cmd, "cut_clip")
    logger.info(f"✅ Clip découpé : {output_path.name} ({start:.1f}s → {end:.1f}s)")
    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPES 10 + 12 + 13 — Reframe + Sous-titres + Export final
# ─────────────────────────────────────────────────────────────────────────────

def apply_reframe_and_subtitles(
    clip_path:    Path,
    srt_path:     Path,
    output_path:  Path,
    crop_centers: list[tuple[int, int]],   # [(cx, cy), ...] par frame
    source_width: int,
    source_height: int,
) -> Path:
    """
    Applique le recadrage 9:16 dynamique ET l'incrustation des sous-titres
    en un seul passage FFmpeg pour économiser les ressources CPU.

    La cropbox est calculée depuis les centres de visage lissés produits par cv.py.
    Si crop_centers est vide (pas de visage détecté), on centrage fixe.

    Args:
        clip_path    : clip découpé (brut)
        srt_path     : fichier .srt des sous-titres
        output_path  : chemin de la vidéo finale
        crop_centers : liste de (cx, cy) lissés pour chaque frame
        source_width : largeur de la vidéo source
        source_height: hauteur de la vidéo source

    Returns:
        Path de la vidéo finale exportée
    """
    # Calcul de la taille du crop : on veut 9:16 dans la source
    # crop_h = source_height (pleine hauteur), crop_w = crop_h * 9/16
    crop_h = source_height
    crop_w = int(crop_h * (TARGET_WIDTH / TARGET_HEIGHT))

    # Si la source est plus large que le crop, on a de la marge → bon
    # Si la source est moins large (vidéo très portait déjà), adapter
    if crop_w > source_width:
        crop_w = source_width
        crop_h = int(crop_w * (TARGET_HEIGHT / TARGET_WIDTH))

    # Centre de crop : si des centres de visage sont disponibles, on les utilise.
    # On prend la médiane pour un plan stable (pas frame-by-frame ici,
    # la version frame-by-frame est gérée dans cv.py pour le rendu OpenCV).
    if crop_centers:
        cx_median = sorted(c[0] for c in crop_centers)[len(crop_centers) // 2]
        cy_median = sorted(c[1] for c in crop_centers)[len(crop_centers) // 2]
    else:
        # Fallback : centre de l'image
        cx_median = source_width  // 2
        cy_median = source_height // 2

    # S'assurer que le crop ne déborde pas
    x_off = max(0, min(cx_median - crop_w // 2, source_width  - crop_w))
    y_off = max(0, min(cy_median - crop_h // 2, source_height - crop_h))

    # Filtre vidéo : crop → scale 1080x1920 → sous-titres
    # On échappe le chemin SRT pour FFmpeg (Windows/Linux compatible)
    srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")
    vf = (
        f"crop={crop_w}:{crop_h}:{x_off}:{y_off},"
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:flags=lanczos,"
        f"subtitles='{srt_escaped}'"
        f":force_style='FontSize=18,Bold=1,Alignment=2,"
        f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2'"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(clip_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",        # Bon compromis vitesse/qualité sur VPS
        "-crf", "23",             # Qualité visuelle (18=excellent, 28=petit fichier)
        "-c:a", "aac",
        "-b:a", "128k",
        "-threads", str(MAX_THREADS),
        "-movflags", "+faststart",  # Streaming web
        str(output_path),
    ]
    _run(cmd, "reframe+subtitles+export")
    logger.info(f"✅ Export final : {output_path.name}")
    return output_path


def export_final_ffmpeg_only(
    clip_path:   Path,
    srt_path:    Path,
    output_path: Path,
    source_width: int,
    source_height: int,
) -> Path:
    """
    Version simplifiée sans face tracking : crop centré + sous-titres.
    Utilisée quand cv.py retourne une liste vide (aucun visage détecté).
    """
    return apply_reframe_and_subtitles(
        clip_path=clip_path,
        srt_path=srt_path,
        output_path=output_path,
        crop_centers=[],
        source_width=source_width,
        source_height=source_height,
    )

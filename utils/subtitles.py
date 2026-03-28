"""
utils/subtitles.py
------------------
Génération de fichiers SRT à partir des segments transcrits par Gemini.
"""

import re
import logging
from pathlib import Path

logger = logging.getLogger("vclipper.subtitles")


def seconds_to_srt_time(seconds: float) -> str:
    """Convertit des secondes en format SRT : HH:MM:SS,mmm"""
    hours   = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs    = int(seconds % 60)
    millis  = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def split_text_into_lines(text: str, max_chars: int = 40) -> str:
    """
    Coupe un texte long en lignes de max_chars caractères.
    Évite les coupures au milieu d'un mot.
    """
    words  = text.split()
    lines  = []
    cur    = ""
    for word in words:
        if len(cur) + len(word) + 1 <= max_chars:
            cur = f"{cur} {word}".strip()
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def generate_srt(
    segments:   list[dict],
    output_path: Path,
    clip_start:  float = 0.0,
    max_chars:   int   = 40,
) -> Path:
    """
    Génère un fichier .srt pour un clip donné.

    Args:
        segments    : liste de dicts {'start': float, 'end': float, 'text': str}
        output_path : chemin de sortie du fichier .srt
        clip_start  : timestamp de début du clip dans la vidéo originale (pour recaler les timestamps)
        max_chars   : largeur max d'une ligne de sous-titre

    Returns:
        Path du fichier .srt généré
    """
    output_path = Path(output_path)
    lines = []

    # Filtrer les segments qui appartiennent au clip (avec une tolérance)
    clip_segments = [
        s for s in segments
        if s.get("start", 0) >= clip_start - 0.5
    ]

    if not clip_segments:
        logger.warning(f"Aucun segment trouvé pour le clip démarrant à {clip_start}s")
        # Écrire un SRT vide valide
        output_path.write_text("", encoding="utf-8")
        return output_path

    for idx, seg in enumerate(clip_segments, start=1):
        # Recaler les timestamps par rapport au début du clip
        start_rel = max(0.0, seg["start"] - clip_start)
        end_rel   = max(0.1, seg["end"]   - clip_start)

        text = seg.get("text", "").strip()
        if not text:
            continue

        formatted_text = split_text_into_lines(text, max_chars)

        lines.append(str(idx))
        lines.append(f"{seconds_to_srt_time(start_rel)} --> {seconds_to_srt_time(end_rel)}")
        lines.append(formatted_text)
        lines.append("")   # Ligne vide entre chaque entrée SRT

    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"✅ SRT généré : {output_path} ({len(clip_segments)} segments)")
    return output_path


def validate_srt(srt_path: Path) -> bool:
    """Valide qu'un fichier SRT a bien le bon format."""
    try:
        content = Path(srt_path).read_text(encoding="utf-8")
        # Un SRT valide contient au moins un timestamp
        return bool(re.search(r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}", content))
    except Exception:
        return False

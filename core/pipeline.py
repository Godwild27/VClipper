"""
core/pipeline.py
----------------
Orchestrateur principal du pipeline VClipper en 13 étapes.
Lance toutes les étapes séquentiellement et met à jour le statut du job en temps réel.

Le pipeline est conçu pour tourner dans un thread d'arrière-plan FastAPI (BackgroundTask).
"""

import logging
import shutil
from pathlib import Path

from core.config import TEMP_DIR, OUTPUT_DIR
from utils import video as vid
from utils import ai as gemini
from utils import cv as vision
from utils import subtitles as subs

logger = logging.getLogger("vclipper.pipeline")


# ─────────────────────────────────────────────────────────────────────────────
# Registre des jobs en mémoire
# ─────────────────────────────────────────────────────────────────────────────

JOBS: dict[str, dict] = {}   # job_id → état du job


def _update_status(job_id: str, step: int, step_name: str, detail: str = ""):
    """Met à jour le statut d'un job."""
    JOBS[job_id]["step"]      = step
    JOBS[job_id]["step_name"] = step_name
    JOBS[job_id]["detail"]    = detail
    logger.info(f"[Job {job_id[:8]}] Étape {step}/13 — {step_name} {detail}")


def _fail_job(job_id: str, error: str):
    """Marque un job comme échoué."""
    JOBS[job_id]["status"] = "failed"
    JOBS[job_id]["error"]  = error
    logger.error(f"[Job {job_id[:8]}] ❌ ÉCHEC : {error}")


# ─────────────────────────────────────────────────────────────────────────────
# Entrée principale du pipeline
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(job_id: str, video_path: Path):
    """
    Exécute le pipeline complet pour un fichier vidéo donné.
    Doit être lancé dans un thread d'arrière-plan.

    Args:
        job_id     : identifiant unique du job (UUID)
        video_path : chemin de la vidéo uploadée
    """
    # Initialiser l'état du job
    JOBS[job_id] = {
        "status":    "running",
        "step":      0,
        "step_name": "Initialisation",
        "detail":    "",
        "clips":     [],
        "error":     None,
    }

    # Dossier temporaire dédié à ce job
    job_dir = TEMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── ÉTAPE 1 : Validation ─────────────────────────────────────────────
        _update_status(job_id, 1, "Validation vidéo")
        metadata = vid.validate_video(video_path)
        JOBS[job_id]["metadata"] = metadata

        # ── ÉTAPE 2 : Extraction audio ───────────────────────────────────────
        _update_status(job_id, 2, "Extraction audio")
        wav_path = vid.extract_audio(video_path, job_dir)

        # ── ÉTAPE 3 : Transcription NVIDIA ───────────────────────────────────
        _update_status(job_id, 3, "Transcription audio IA", "(peut prendre 1-2 min)")
        segments = gemini.transcribe_audio(wav_path)

        if not segments:
            raise ValueError("Aucun segment audio transcrit. Vérifier la qualité audio.")

        # ── ÉTAPE 4 : Segmentation ───────────────────────────────────────────
        _update_status(job_id, 4, "Segmentation de la transcription")
        blocks = gemini.segment_transcript(segments)

        if not blocks:
            raise ValueError("Impossible de créer des blocs depuis la transcription.")

        # ── ÉTAPE 5 : Analyse IA ─────────────────────────────────────────────
        _update_status(job_id, 5, "Analyse IA des moments viraux", "(Nemotron en cours)")
        analyses = gemini.analyze_segments(blocks)

        if not analyses:
            raise ValueError("L'analyse IA n'a retourné aucun résultat.")

        # ── ÉTAPE 6 : Sélection des clips ────────────────────────────────────
        _update_status(job_id, 6, "Sélection des meilleurs clips")
        best_clips = gemini.select_best_clips(analyses)

        if not best_clips:
            raise ValueError(
                "Aucun clip suffisamment viral trouvé. "
                "Essayer une vidéo avec plus de contenu engageant."
            )

        # ── ÉTAPES 7-13 : Traitement de chaque clip ──────────────────────────
        final_clips = []

        for clip_idx, clip_data in enumerate(best_clips, start=1):
            clip_start = float(clip_data["start_time"])
            clip_end   = float(clip_data["end_time"])
            clip_score = float(clip_data.get("score", 0))
            clip_label = f"clip_{clip_idx:02d}"

            logger.info(
                f"\n{'='*50}\n▶ Traitement {clip_label} "
                f"({clip_start:.1f}s → {clip_end:.1f}s | Score: {clip_score})\n{'='*50}"
            )

            try:
                # ── ÉTAPE 7 : Découpe ────────────────────────────────────────
                _update_status(job_id, 7, f"Découpe {clip_label}", f"({clip_start:.0f}s → {clip_end:.0f}s)")
                raw_clip_path = job_dir / f"{clip_label}_raw.mp4"
                vid.cut_clip(video_path, clip_start, clip_end, raw_clip_path)

                # ── ÉTAPE 8+9 : Détection + Tracking visage ──────────────────
                _update_status(job_id, 8, f"Détection/Tracking visage {clip_label}")
                crop_centers = vision.compute_smooth_crop_centers(raw_clip_path)

                # ── ÉTAPE 10 : Reframe OpenCV si visage détecté ──────────────
                if crop_centers:
                    _update_status(job_id, 10, f"Reframe 9:16 {clip_label}", "(face tracking)")
                    reframed_noaudio_path = job_dir / f"{clip_label}_reframed_noaudio.mp4"
                    vision.render_reframed_clip(raw_clip_path, reframed_noaudio_path, crop_centers)
                    # Recomposer l'audio depuis le clip brut
                    reframed_video_path = job_dir / f"{clip_label}_reframed.mp4"
                    _merge_audio(raw_clip_path, reframed_noaudio_path, reframed_video_path)
                else:
                    # Pas de visage → la vidéo brute sera utilisée, FFmpeg recadrera au centre
                    logger.warning(f"⚠️ Pas de visage pour {clip_label}, crop centré utilisé")
                    reframed_video_path = raw_clip_path

                # ── ÉTAPE 11 : Génération SRT ────────────────────────────────
                _update_status(job_id, 11, f"Génération sous-titres {clip_label}")
                srt_path = job_dir / f"{clip_label}.srt"
                subs.generate_srt(
                    segments=segments,
                    output_path=srt_path,
                    clip_start=clip_start,
                )

                # ── ÉTAPES 12+13 : Sous-titres + Export final ────────────────
                _update_status(job_id, 12, f"Incrustation sous-titres + Export {clip_label}")

                final_output_path = OUTPUT_DIR / f"{job_id[:8]}_{clip_label}.mp4"

                if crop_centers:
                    # OpenCV a fait le reframe → FFmpeg ajoute juste les sous-titres
                    _apply_subtitles_only(reframed_video_path, srt_path, final_output_path)
                else:
                    # FFmpeg fait tout : reframe centré + sous-titres
                    vid.export_final_ffmpeg_only(
                        clip_path=raw_clip_path,
                        srt_path=srt_path,
                        output_path=final_output_path,
                        source_width=metadata["width"],
                        source_height=metadata["height"],
                    )

                final_clips.append({
                    "clip_id":    f"{job_id[:8]}_{clip_label}",
                    "filename":   final_output_path.name,
                    "path":       str(final_output_path),
                    "start_time": clip_start,
                    "end_time":   clip_end,
                    "duration":   round(clip_end - clip_start, 1),
                    "score":      clip_score,
                    "emotion":    clip_data.get("emotion", ""),
                    "raison":     clip_data.get("raison", ""),
                    "has_face":   bool(crop_centers),
                })
                logger.info(f"✅ {clip_label} terminé → {final_output_path.name}")

            except Exception as e:
                logger.error(f"❌ Erreur sur {clip_label} : {e}", exc_info=True)
                # Continuer avec les autres clips même en cas d'erreur sur l'un d'eux

        if not final_clips:
            raise RuntimeError("Tous les clips ont échoué lors du traitement.")

        # ── Finalisation ─────────────────────────────────────────────────────
        JOBS[job_id]["status"] = "done"
        JOBS[job_id]["clips"]  = final_clips
        JOBS[job_id]["step"]   = 13
        JOBS[job_id]["step_name"] = "Terminé"
        logger.info(f"🎉 Job {job_id[:8]} terminé ! {len(final_clips)} clip(s) générés.")

    except Exception as e:
        _fail_job(job_id, str(e))

    finally:
        # Nettoyage du dossier temporaire
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
            logger.debug(f"🗑️ Dossier temp supprimé : {job_dir}")
        except Exception:
            pass


def _apply_subtitles_only(
    video_path:  Path,
    srt_path:    Path,
    output_path: Path,
):
    """Ajoute uniquement les sous-titres sur une vidéo déjà recadrée."""
    import subprocess
    from core.config import MAX_THREADS

    srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")
    vf = (
        f"subtitles='{srt_escaped}'"
        f":force_style='FontSize=18,Bold=1,Alignment=2,"
        f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2'"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-threads", str(MAX_THREADS),
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg subtitles failed: {result.stderr[-300:]}")


def _merge_audio(
    source_with_audio: Path,
    video_no_audio:    Path,
    output_path:       Path,
):
    """
    Fusionne la piste audio de source_with_audio avec la vidéo de video_no_audio.
    Utilisé après render_reframed_clip() qui produit un fichier mp4v sans audio.
    On re-encode en libx264 pour garantir la compatibilité.
    """
    import subprocess
    from core.config import MAX_THREADS

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_no_audio),      # Vidéo recadrée (sans audio, codec mp4v)
        "-i", str(source_with_audio),   # Clip brut (avec audio)
        "-c:v", "libx264",              # Re-encoder en H.264 (mp4v→H264 requis)
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-map", "0:v:0",                # Vidéo depuis le fichier recadré
        "-map", "1:a:0",                # Audio depuis le clip brut
        "-shortest",                    # Durée = le plus court des deux
        "-threads", str(MAX_THREADS),
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg merge audio failed: {result.stderr[-300:]}")


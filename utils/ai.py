"""
utils/ai.py
-----------
Intégration NVIDIA API pour :
  - Étape 3 : Transcription audio avec timestamps (Whisper via NVIDIA)
  - Étape 4 : Segmentation de la transcription
  - Étape 5 : Analyse IA des meilleurs moments (Nemotron - score viralité TikTok)
  - Étape 6 : Sélection des top clips
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

from openai import OpenAI

from core.config import (
    NVIDIA_API_KEY,
    NVIDIA_BASE_URL,
    NVIDIA_MODEL_TEXT,
    NVIDIA_MODEL_AUDIO,
    MAX_CLIPS,
    MIN_CLIP_SCORE,
)

logger = logging.getLogger("vclipper.ai")

# Initialisation du client OpenAI (NVIDIA)
client = OpenAI(
    base_url=NVIDIA_BASE_URL,
    api_key=NVIDIA_API_KEY if NVIDIA_API_KEY else "dummy-key-to-init"
)

def update_api_key(key: str):
    """Met à jour la clé API NVIDIA à chaud"""
    if key:
        global client
        client = OpenAI(
            base_url=NVIDIA_BASE_URL,
            api_key=key
        )


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPE 3 — Transcription audio avec timestamps
# ─────────────────────────────────────────────────────────────────────────────

TRANSCRIPTION_PROMPT = """
Transcris cet extrait audio en fournissant chaque segment avec son timestamp précis.
Réponds UNIQUEMENT en JSON valide, avec ce format exact :
{
  "segments": [
    {"start": 0.0, "end": 4.2, "text": "Texte du segment", "language": "fr"},
    {"start": 4.2, "end": 9.8, "text": "Autre segment", "language": "fr"}
  ]
}
- Les timestamps sont en secondes avec décimales.
- Chaque segment dure entre 3 et 15 secondes.
- Détecte automatiquement la langue.
- Transcris TOUT ce qui est dit, sans résumé ni coupure.
"""


def transcribe_audio(wav_path: Path) -> list[dict]:
    """
    Envoie l'audio WAV à NVIDIA (Whisper) et retourne les segments transcrits avec timestamps.
    """
    wav_path = Path(wav_path)
    file_size_mb = wav_path.stat().st_size / (1024 * 1024)
    logger.info(f"📤 Upload audio vers NVIDIA STT ({file_size_mb:.1f} MB) : {wav_path.name}")

    try:
        with open(wav_path, "rb") as audio_file:
            response = client.audio.transcriptions.create(
                model=NVIDIA_MODEL_AUDIO or "whisper-1", # fallback standard si vide
                file=audio_file,
                response_format="verbose_json"
            )
    except Exception as e:
        logger.error(f"Échec transcription NVIDIA : {e}")
        # Si le fichier est trop long ou non supporté par l'endpoint audio de nvidia
        raise RuntimeError(f"Échec transcription NVIDIA : {e}")

    # Parser la réponse. L'API Whisper retourne un objet avec un attribut .segments
    segments = []
    if hasattr(response, "segments") and response.segments:
        for seg in response.segments:
            segments.append({
                "start": float(seg.start),
                "end":   float(seg.end),
                "text":  str(seg.text).strip(),
                "language": getattr(response, "language", "unknown")
            })
    else:
        # Fallback pour du texte brut sans timestamps (si l'API ne retourne pas de verbose_json complet)
        text = getattr(response, "text", str(response))
        segments.append({
            "start": 0.0,
            "end":   max(1.0, float(file_size_mb * 5)), # Estimation grossière
            "text":  text.strip(),
            "language": "unknown"
        })

    logger.info(f"✅ Transcription : {len(segments)} segments extraits")
    return segments


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPE 4 — Segmentation de la transcription
# ─────────────────────────────────────────────────────────────────────────────

def segment_transcript(
    segments:     list[dict],
    min_duration: float = 10.0,
    max_duration: float = 30.0,
) -> list[dict]:
    """
    Regroupe les segments de transcription en blocs exploitables (10-30 secondes).
    Cela crée des "fenêtres" cohérentes pour l'analyse IA.

    Args:
        segments     : liste de segments bruts issus de la transcription
        min_duration : durée minimale d'un bloc (secondes)
        max_duration : durée maximale d'un bloc (secondes)

    Returns:
        Liste de blocs : [{'start': float, 'end': float, 'text': str, 'segments': list}]
    """
    if not segments:
        return []

    blocks = []
    current_block_segments = [segments[0]]

    for seg in segments[1:]:
        current_start = current_block_segments[0]["start"]
        current_end   = seg["end"]
        current_dur   = current_end - current_start

        if current_dur <= max_duration:
            current_block_segments.append(seg)
        else:
            # Clore le bloc actuel s'il est assez long
            block_dur = current_block_segments[-1]["end"] - current_block_segments[0]["start"]
            if block_dur >= min_duration:
                blocks.append(_make_block(current_block_segments))
            current_block_segments = [seg]

    # Dernier bloc
    if current_block_segments:
        block_dur = current_block_segments[-1]["end"] - current_block_segments[0]["start"]
        if block_dur >= min_duration:
            blocks.append(_make_block(current_block_segments))

    logger.info(f"✅ Segmentation : {len(segments)} segments → {len(blocks)} blocs")
    return blocks


def _make_block(segs: list[dict]) -> dict:
    return {
        "start":    segs[0]["start"],
        "end":      segs[-1]["end"],
        "text":     " ".join(s["text"] for s in segs),
        "segments": segs,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPE 5 — Analyse IA des meilleurs moments
# ─────────────────────────────────────────────────────────────────────────────

ANALYSIS_PROMPT_TEMPLATE = """
Tu es un expert en création de contenu viral pour TikTok.
Analyse les extraits suivants d'une vidéo et identifie les meilleurs moments pour créer des clips courts.

EXTRAITS (format JSON) :
{blocks_json}

Pour CHAQUE extrait, évalue :
1. score_viralite (0-10) : potentiel viral TikTok
2. emotion : émotion principale détectée (humour, surprise, inspiration, colère, émotion, info, autre)
3. hook : y a-t-il une accroche forte au début ? (true/false)
4. clarte_message : le message est-il clair et percutant ? (0-10)
5. potentiel_tiktok (0-10) : adapté au format court TikTok
6. score_final (0-10) : score global (moyenne pondérée)
7. raison : explication courte (max 20 mots) pourquoi ce moment est bon ou mauvais

Réponds UNIQUEMENT en JSON valide :
{{
  "analyses": [
    {{
      "start_time": 0.0,
      "end_time":   15.0,
      "score": 8.5,
      "emotion": "inspiration",
      "hook": true,
      "clarte_message": 9,
      "potentiel_tiktok": 8,
      "raison": "Révélation choc avec hook fort en ouverture"
    }}
  ]
}}
"""


def analyze_segments(blocks: list[dict]) -> list[dict]:
    """
    Envoie les blocs de transcription à NVIDIA Nemotron pour analyse de viralité TikTok.

    Args:
        blocks : liste de blocs issus de segment_transcript()

    Returns:
        Liste d'analyses : [{'start_time', 'end_time', 'score', 'emotion', 'raison', ...}]
    """
    if not blocks:
        return []

    # Simplifier les blocs pour le prompt (réduire les tokens)
    simplified = [
        {
            "index":  i,
            "start":  b["start"],
            "end":    b["end"],
            "texte":  b["text"][:500],  # Tronquer si très long
        }
        for i, b in enumerate(blocks)
    ]

    blocks_json = json.dumps(simplified, ensure_ascii=False, indent=2)
    prompt = ANALYSIS_PROMPT_TEMPLATE.format(blocks_json=blocks_json)

    try:
        completion = client.chat.completions.create(
            model=NVIDIA_MODEL_TEXT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            top_p=0.95,
            max_tokens=4000,
            # Activation du reasoning pour Nemotron !
            extra_body={"chat_template_kwargs": {"enable_thinking": True}, "reasoning_budget": 4000}
        )
    except Exception as e:
        raise RuntimeError(f"Erreur analyse NVIDIA Nemotron : {e}")

    choice = completion.choices[0]
    
    # Logger le raisonnement cognitif de Nemotron (si l'API le retourne)
    if hasattr(choice, "message"):
        msg = choice.message
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            logger.info(f"🧠 [Nemotron Reasoning] : {str(msg.reasoning_content)[:200]}...")
            
    raw_response = completion.choices[0].message.content

    analyses = _parse_analysis_response(raw_response)
    logger.info(f"✅ Analyse IA : {len(analyses)} moments évalués")
    return analyses


def _parse_analysis_response(raw: str) -> list[dict]:
    """Parse la réponse JSON de Nemotron pour l'analyse de viralité."""
    if not raw:
        return []
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    # Parfois les modèles rajoutent du texte avant le JSON
    if "{" in raw and "}" in raw:
        raw = raw[raw.find("{"):raw.rfind("}")+1]
        
    try:
        data = json.loads(raw)
        return data.get("analyses", [])
    except json.JSONDecodeError as e:
        logger.error(f"Impossible de parser l'analyse JSON : {e}\nRaw: {raw[:300]}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPE 6 — Sélection des meilleurs clips
# ─────────────────────────────────────────────────────────────────────────────

def select_best_clips(analyses: list, max_clips: Optional[int] = None) -> list:
    """
    Sélectionne les meilleurs moments selon leur score de viralité.
    Évite les clips qui se chevauchent.

    Args:
        analyses  : résultats d'analyze_segments()
        max_clips : nombre max de clips à sélectionner (défaut : config MAX_CLIPS)

    Returns:
        Liste triée des meilleurs clips sélectionnés
    """
    if max_clips is None:
        max_clips = MAX_CLIPS

    # Filtrer par score minimum
    candidates = [
        a for a in analyses
        if float(a.get("score", 0)) >= MIN_CLIP_SCORE
    ]

    # Trier par score décroissant
    candidates.sort(key=lambda x: float(x.get("score", 0)), reverse=True)

    selected = []
    for candidate in candidates:
        if len(selected) >= max_clips:
            break

        start = float(candidate.get("start_time", 0))
        end   = float(candidate.get("end_time",   0))

        # Vérifier qu'il ne chevauche pas un clip déjà sélectionné
        overlaps = any(
            not (end <= s["start_time"] or start >= s["end_time"])
            for s in selected
        )
        if not overlaps:
            selected.append(candidate)

    logger.info(
        f"✅ Clips sélectionnés : {len(selected)}/{len(candidates)} candidats "
        f"(score ≥ {MIN_CLIP_SCORE})"
    )
    for i, clip in enumerate(selected, 1):
        logger.info(
            f"   Clip {i}: {clip['start_time']:.1f}s → {clip['end_time']:.1f}s "
            f"| Score: {clip['score']} | {clip.get('raison', '')}"
        )
    return selected

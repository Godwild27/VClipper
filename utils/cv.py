"""
utils/cv.py
-----------
Traitement OpenCV pour le reframe intelligent en 9:16 :
  - Étape 8 : Détection du visage principal (Haar Cascade)
  - Étape 9 : Suivi CSRT frame par frame
  - Étape 10 : Calcul des centres lissés + rendu du crop dynamique

Optimisé pour VPS sans GPU :
  - Traitement frame par frame avec libération mémoire
  - Lissage exponentiel pour éviter les mouvements brusques
  - Skip frames pour accélérer le tracking (analyse 1 frame / 2)
"""

import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from core.config import TARGET_WIDTH, TARGET_HEIGHT, MAX_THREADS

logger = logging.getLogger("vclipper.cv")

# Nombre de threads OpenCV
cv2.setNumThreads(MAX_THREADS)

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

# Facteur de lissage exponentiel (0 = pas de lissage, 1 = immobile)
# 0.93 → transitions très douces (cinematic pan)
SMOOTH_FACTOR = 0.93

# On analyse 1 frame sur N pour économiser le CPU
SKIP_FRAMES = 2

# Chemin du Haar Cascade frontal face
HAAR_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPE 8 — Détection du visage dans une frame
# ─────────────────────────────────────────────────────────────────────────────

def detect_largest_face(frame, cascade) -> Optional[tuple]:
    """
    Détecte le plus grand visage dans une frame via Haar Cascade.

    Args:
        frame   : image BGR (numpy array)
        cascade : cv2.CascadeClassifier chargé

    Returns:
        (x, y, w, h) du plus grand visage, ou None si aucun visage trouvé
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)   # Améliore la détection en basse lumière

    faces = cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(60, 60),
        flags=cv2.CASCADE_SCALE_IMAGE,
    )

    if len(faces) == 0:
        return None

    # Sélectionner le plus grand visage (surface max)
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])

    # ── Amélioration "Cinématique" ───────────────────────────
    # Au lieu de traquer uniquement le visage (qui bouge vite et de manière saccadée),
    # on agrandit la bounding box pour inclure la tête ENTIÈRE et les ÉPAULES.
    # Le tracker CSRT suivra tout le buste, ce qui donne un rendu beaucoup plus stable
    # et "télévisuel" (conservation du contexte).
    new_w = int(w * 1.8)
    new_h = int(h * 2.5)
    new_x = max(0, x - int(w * 0.4))
    new_y = max(0, y - int(h * 0.4))

    return (new_x, new_y, new_w, new_h)


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPE 8.1 — Détection du centre visuel (Fallback si pas de visage)
# ─────────────────────────────────────────────────────────────────────────────

def detect_visual_center(gray_frame) -> Optional[tuple]:
    """
    Détecte le "centre d'intérêt" d'une image sans visage (voiture, jeu, objet).
    Utilise la détection de coins de Shi-Tomasi pour trouver la zone la plus détaillée.
    
    Returns:
        (cx, cy) pointant vers la zone d'intérêt, ou None
    """
    # Downscale pour la performance
    small = cv2.resize(gray_frame, (320, 180))
    corners = cv2.goodFeaturesToTrack(small, maxCorners=50, qualityLevel=0.03, minDistance=5)
    
    if corners is not None and len(corners) > 0:
        # On utilise la médiane pour ignorer le bruit isolé
        cx_small = int(np.median(corners[:, 0, 0]))
        cy_small = int(np.median(corners[:, 0, 1]))
        
        scale_x = gray_frame.shape[1] / 320.0
        scale_y = gray_frame.shape[0] / 180.0
        return int(cx_small * scale_x), int(cy_small * scale_y)
    
    return None


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPES 9 + 10 — Tracking CSRT + Calcul centres lissés
# ─────────────────────────────────────────────────────────────────────────────

def compute_smooth_crop_centers(clip_path: Path) -> list:
    """
    Analyse le clip frame par frame :
    1. Détecte le visage dans la première frame (ou jusqu'à en trouver un)
    2. Suit le visage avec le tracker CSRT
    3. Réinitialise la détection si le tracker perd le visage
    4. Applique un lissage exponentiel sur les positions

    Args:
        clip_path : chemin du clip découpé

    Returns:
        Liste de (cx, cy) lissés, un par frame de la vidéo.
        Peut être vide si aucun visage détecté dans tout le clip.
    """
    clip_path = Path(clip_path)
    cap = cv2.VideoCapture(str(clip_path))

    if not cap.isOpened():
        logger.warning(f"Impossible d'ouvrir le clip : {clip_path.name}")
        return []

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width        = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height       = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    logger.info(f"📹 Analyse faces : {clip_path.name} ({total_frames} frames, {width}x{height})")

    # Charger le cascade Haar
    cascade = cv2.CascadeClassifier(HAAR_CASCADE_PATH)
    if cascade.empty():
        logger.error("Haar Cascade non trouvé. Vérifier l'installation d'OpenCV.")
        cap.release()
        return []

    tracker = None
    tracking = False
    raw_centers: list = []
    frame_idx = 0
    lost_count = 0          # Nombre de frames consécutives sans visage
    MAX_LOST = 60           # On attend plus longtemps avant d'abandonner (2-3 sec)

    # Centre courant lissé (initialisé au centre de l'image)
    smooth_cx = float(width  // 2)
    smooth_cy = float(height // 2)
    has_face = False

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # ── Tracking CSRT ────────────────────────────────────────────────────
        if tracking and tracker is not None:
            success, bbox = tracker.update(frame)

            if success:
                x, y, w, h = [int(v) for v in bbox]
                cx = x + w // 2
                cy = y + h // 2
                lost_count = 0
                has_face = True
            else:
                lost_count += 1
                tracking = False
                tracker = None
                success = False

        else:
            success = False

        # ── Ré-initialisation du tracker si nécessaire ───────────────────────
        if not success and (frame_idx % SKIP_FRAMES == 0 or lost_count >= MAX_LOST):
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            face = detect_largest_face(frame, cascade)
            if face is not None:
                x, y, w, h = face
                cx = x + w // 2
                cy = y + h // 2
                has_face = True
                lost_count = 0

                # Créer un nouveau tracker CSRT
                # Compatibilité OpenCV 4.5+ (cv2.legacy) et versions antérieures
                try:
                    tracker = cv2.legacy.TrackerCSRT_create()
                except AttributeError:
                    tracker = cv2.TrackerCSRT_create()  # type: ignore
                tracker.init(frame, (x, y, w, h))
                tracking = True
            else:
                # ── Fallback 1 : Tracker le centre visuel (objets, jeux) ──
                v_center = detect_visual_center(gray)
                if v_center is not None:
                    # On guide doucement le centre vers ce point d'intérêt
                    cx, cy = v_center
                    # Force un lissage encore plus fort quand on suit des objets
                    # pour éviter le tremblement "corner-tracking"
                    cx = int(smooth_cx * 0.9 + cx * 0.1)
                    cy = int(smooth_cy * 0.9 + cy * 0.1)
                    lost_count += 0.5  # On incrémente doucement
                else:
                    # ── Fallback 2 : Dérive au centre de l'image ──────────
                    lost_count += 1
                    if lost_count > 15:
                        drift_v = 0.02
                        cx = int(cx * (1 - drift_v) + (width / 2) * drift_v)
                    else:
                        cx = int(smooth_cx)
                        cy = int(smooth_cy)

        elif not success:
            # Même dérive si Tracking CSRT échoue
            if lost_count > 15:
                drift_v = 0.02
                cx = int(cx * (1 - drift_v) + (width / 2) * drift_v)
            else:
                cx = int(smooth_cx)
                cy = int(smooth_cy)

        # ── Lissage exponentiel ───────────────────────────────────────────────
        smooth_cx = SMOOTH_FACTOR * smooth_cx + (1 - SMOOTH_FACTOR) * cx
        smooth_cy = SMOOTH_FACTOR * smooth_cy + (1 - SMOOTH_FACTOR) * cy
        raw_centers.append((int(smooth_cx), int(smooth_cy)))

        frame_idx += 1

    cap.release()

    if not has_face:
        logger.warning(f"⚠️ Aucun visage détecté dans {clip_path.name}. Crop centré utilisé.")
        return []

    logger.info(f"✅ Tracking terminé : {len(raw_centers)} centres calculés")
    return raw_centers


# ─────────────────────────────────────────────────────────────────────────────
# ÉTAPE 10 — Rendu vidéo avec crop dynamique (OpenCV writer)
# ─────────────────────────────────────────────────────────────────────────────

def render_reframed_clip(
    clip_path:    Path,
    output_path:  Path,
    crop_centers: list,
) -> Path:
    """
    Relit le clip frame par frame et applique le crop 9:16 centré sur le visage.
    Écrit la vidéo recadrée via OpenCV VideoWriter.

    Utilisé si cv.py gère le rendu (sinon FFmpeg s'en charge).
    Note : audio non inclus → à recomposer avec FFmpeg.

    Args:
        clip_path    : clip d'entrée
        output_path  : clip recadré de sortie (sans audio)
        crop_centers : liste de (cx, cy) par frame issue de compute_smooth_crop_centers()

    Returns:
        Path de la vidéo recadrée (sans audio)
    """
    cap = cv2.VideoCapture(str(clip_path))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    src_width    = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_height   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps          = cap.get(cv2.CAP_PROP_FPS) or 25.0

    # Calcul du crop 9:16
    crop_h = src_height
    crop_w = int(crop_h * TARGET_WIDTH / TARGET_HEIGHT)
    if crop_w > src_width:
        crop_w = src_width
        crop_h = int(crop_w * TARGET_HEIGHT / TARGET_WIDTH)

    # Writer de sortie
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(
        str(output_path), fourcc, fps,
        (TARGET_WIDTH, TARGET_HEIGHT)
    )

    frame_idx = 0
    default_cx = src_width  // 2
    default_cy = src_height // 2

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx < len(crop_centers):
            cx, cy = crop_centers[frame_idx]
        else:
            cx, cy = default_cx, default_cy

        # Calcul du coin haut-gauche du crop
        x_off = max(0, min(cx - crop_w // 2, src_width  - crop_w))
        y_off = max(0, min(cy - crop_h // 2, src_height - crop_h))

        cropped = frame[y_off:y_off + crop_h, x_off:x_off + crop_w]
        resized  = cv2.resize(cropped, (TARGET_WIDTH, TARGET_HEIGHT),
                              interpolation=cv2.INTER_LANCZOS4)
        writer.write(resized)
        frame_idx += 1

    cap.release()
    writer.release()
    logger.info(f"✅ Reframe OpenCV : {output_path.name}")
    return output_path

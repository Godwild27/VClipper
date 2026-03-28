"""
main.py
-------
Point d'entrée de VClipper :
  - Lance le serveur FastAPI (Uvicorn)
  - Ouvre un tunnel Cloudflare public au démarrage
  - Affiche l'URL publique dans les logs
  - Sert l'interface web depuis /static
  - Expose les endpoints API REST

Usage :
    python main.py
"""

import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import aiofiles
import uvicorn
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core import config
from core.config import OUTPUT_DIR, STATIC_DIR, UPLOAD_DIR, BASE_DIR

# Fichier pour stocker l'URL publique générée (accessible par le CLI)
import os
import platform
if platform.system() == "Windows":
    URL_FILE_PATH = BASE_DIR / ".public_url"
else:
    URL_FILE_PATH = Path("/tmp/vclipper.url")

from core.pipeline import JOBS, run_pipeline
from utils.youtube import is_youtube_url, download_youtube, get_video_info

# ─────────────────────────────────────────────────────────────────────────────
# Configuration des logs
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("vclipper.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("vclipper.main")

# ─────────────────────────────────────────────────────────────────────────────
# Vérifications au démarrage
# ─────────────────────────────────────────────────────────────────────────────

def check_requirements():
    """Vérifie que toutes les dépendances système sont disponibles."""
    import shutil
    errors = []
    if not shutil.which("ffmpeg"):
        errors.append("❌ FFmpeg non trouvé. Installer : sudo apt install ffmpeg")
    if not shutil.which("cloudflared"):
        errors.append("❌ cloudflared non trouvé. Voir : https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/")
    # La clé API Gemini n'est plus obligatoire au démarrage (configurable via l'interface)
    if errors:
        for e in errors:
            logger.error(e)
        logger.error("\n⚠️  Corrigez les erreurs ci-dessus puis relancez le serveur.")
        sys.exit(1)
    logger.info("✅ Toutes les dépendances sont disponibles.")


# ─────────────────────────────────────────────────────────────────────────────
# Tunnel Cloudflare
# ─────────────────────────────────────────────────────────────────────────────

PUBLIC_URL: str = ""


def start_cloudflare_tunnel(port: int = 8000):
    """
    Lance cloudflared en arrière-plan et extrait l'URL publique générée.
    L'URL est affichée dans les logs et stockée dans PUBLIC_URL.
    Lit la sortie de cloudflared directement via stdout/stderr (PIPE)
    au lieu de se fier à --logfile qui ne capture pas toujours l'URL.
    """
    global PUBLIC_URL
    import re

    def _run_tunnel():
        global PUBLIC_URL

        import shutil
        if not shutil.which("cloudflared"):
            logger.error("❌ cloudflared non trouvé.")
            return

        # 1. Tuer les anciens tunnels pour éviter les conflits
        try:
            subprocess.run(["pkill", "-f", "cloudflared"], capture_output=True)
            time.sleep(1)
        except Exception:
            pass

        # 2. Lancer cloudflared — on capture TOUT (stderr merge dans stdout)
        cmd = [
            "cloudflared", "tunnel", "--url", f"http://localhost:{port}",
            "--no-autoupdate",
        ]
        logger.info(f"🚀 Tunnel Cloudflare : http://localhost:{port} -> ???")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # ← stderr fusionné dans stdout
        )

        # 3. Lire la sortie ligne par ligne dans CE thread (daemon)
        found = False
        start_time = time.time()

        for raw_line in iter(proc.stdout.readline, b""):
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line:
                continue

            # Log chaque ligne de cloudflared pour le debug
            logger.debug(f"[cloudflared] {line}")

            # Chercher l'URL publique (elle apparaît une seule fois)
            if not found and "trycloudflare.com" in line:
                match = re.search(
                    r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com", line
                )
                if match:
                    PUBLIC_URL = match.group(0)
                    logger.info(f"🌐 URL PUBLIQUE : {PUBLIC_URL}")
                    # Écrire l'URL dans le fichier pour le CLI JS
                    try:
                        with open(URL_FILE_PATH, "w", encoding="utf-8") as url_file:
                            url_file.write(PUBLIC_URL)
                    except Exception:
                        pass
                    found = True
                    # NE PAS break — on continue de drainer stdout
                    # pour éviter que le buffer se remplisse et bloque cloudflared

            # Timeout de sécurité si l'URL n'est jamais trouvée
            if not found and (time.time() - start_time) > 90:
                logger.error(
                    "❌ Timeout 90s : cloudflared n'a pas fourni d'URL. "
                    "Vérifiez votre connexion réseau."
                )
                break

        # Si on arrive ici, cloudflared s'est arrêté
        exit_code = proc.poll()
        if not found:
            logger.error(
                f"❌ cloudflared s'est arrêté sans fournir d'URL (code={exit_code}). "
                "Vérifiez que cloudflared est à jour et que le réseau est accessible."
            )

    thread = threading.Thread(target=_run_tunnel, daemon=True)
    thread.start()
    time.sleep(1)


# ─────────────────────────────────────────────────────────────────────────────
# Application FastAPI
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="VClipper API",
    description="Convert long videos into viral TikTok clips automatically.",
    version="1.0.0",
)

# Servir les fichiers statiques (interface web)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
# Servir les clips générés
app.mount("/outputs", StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root():
    """Sert l'interface web principale."""
    index_path = STATIC_DIR / "index.html"
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"))


@app.get("/health")
async def health():
    """Endpoint de santé du serveur."""
    return {
        "status":     "ok",
        "public_url": PUBLIC_URL or "Tunnel en cours de démarrage...",
        "nvidia_ok":  bool(config.NVIDIA_API_KEY),
    }

@app.post("/api-key")
async def update_api_key_endpoint(request: Request):
    """Enregistre la clé API NVIDIA via l'interface web"""
    data = await request.json()
    key = data.get("api_key", "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="La clé API est vide.")
        
    # Mise à jour dans le config (.env + variables) et dans ai.py
    from utils import ai
    config.set_nvidia_api_key(key)
    ai.update_api_key(key)
    
    return {"message": "Clé API NVIDIA enregistrée avec succès !", "nvidia_ok": True}


@app.post("/upload")
async def upload_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """
    Upload une vidéo et lance le pipeline de traitement en arrière-plan.

    Returns:
        job_id : identifiant unique pour suivre la progression
    """
    ALLOWED_TYPES = {"video/mp4", "video/x-matroska", "video/quicktime",
                     "video/avi", "video/webm"}

    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Format non supporté : {file.content_type}"
        )

    job_id    = str(uuid.uuid4())
    save_path = UPLOAD_DIR / f"{job_id}_{file.filename}"

    # Sauvegarder le fichier uploadé
    async with aiofiles.open(save_path, "wb") as out_file:
        while chunk := await file.read(1024 * 1024):   # 1MB par chunk
            await out_file.write(chunk)

    logger.info(f"📥 Vidéo reçue : {file.filename} → job {job_id[:8]}")

    # Lancer le pipeline en arrière-plan
    background_tasks.add_task(run_pipeline, job_id, save_path)

    return JSONResponse({
        "job_id":   job_id,
        "filename": file.filename,
        "message":  "Traitement lancé. Vérifiez /status/{job_id} pour la progression.",
    })


@app.post("/upload-url")
async def upload_from_youtube(
    background_tasks: BackgroundTasks,
    request:          Request,
):
    """
    Accepte une URL YouTube, la télécharge et lance le pipeline.

    Body JSON : {"url": "https://youtu.be/..."}
    Returns   : job_id pour suivre la progression
    """
    data = await request.json()
    url  = data.get("url", "").strip()

    if not url:
        raise HTTPException(status_code=400, detail="URL manquante.")
    if not is_youtube_url(url):
        raise HTTPException(status_code=400, detail="URL YouTube invalide.")

    job_id = str(uuid.uuid4())
    logger.info(f"🎬 YouTube URL reçue → job {job_id[:8]} : {url}")

    # Le téléchargement se fait dans le background task
    # (peut durer 1-3 min pour les longues vidéos)
    background_tasks.add_task(_download_and_run, job_id, url)

    return JSONResponse({
        "job_id":  job_id,
        "url":     url,
        "message": "Téléchargement YouTube lancé. Vérifiez /status/{job_id}.",
    })


@app.get("/video-info")
async def video_info(url: str):
    """
    Retourne les métadonnées d'une vidéo YouTube sans la télécharger.
    Utilisé par l'interface pour afficher le titre et la durée avant confirmation.
    """
    url = url.strip()
    if not is_youtube_url(url):
        raise HTTPException(status_code=400, detail="URL YouTube invalide.")
    try:
        info = get_video_info(url)
        return JSONResponse(info)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))


def _download_and_run(job_id: str, url: str):
    """
    Télécharge la vidéo YouTube puis lance le pipeline classique.
    Si le téléchargement échoue, on marque le job comme failed.
    """
    JOBS[job_id] = {
        "status":    "running",
        "step":      0,
        "step_name": "Téléchargement YouTube",
        "detail":    "(peut prendre 1-3 minutes)",
        "clips":     [],
        "error":     None,
    }
    try:
        # On télécharge dans le dossier upload
        video_path = download_youtube(url, UPLOAD_DIR, job_id)
        # Puis on lance la pipeline normale
        run_pipeline(job_id, video_path)
    except Exception as e:
        logger.error(f"[Job {job_id[:8]}] Échec téléchargement YouTube : {e}")
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"]  = str(e)


@app.get("/status/{job_id}")
async def get_status(job_id: str):
    """Retourne l'état actuel d'un job de traitement."""
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job introuvable.")

    job = JOBS[job_id]
    return JSONResponse({
        "job_id":    job_id,
        "status":    job["status"],          # running | done | failed
        "step":      job["step"],            # 0-13
        "step_name": job["step_name"],
        "detail":    job.get("detail", ""),
        "clips":     job.get("clips", []),
        "error":     job.get("error"),
    })


@app.get("/download/{filename}")
async def download_clip(filename: str):
    """Télécharge un clip généré."""
    file_path = OUTPUT_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    return FileResponse(
        path=str(file_path),
        media_type="video/mp4",
        filename=filename,
    )


@app.delete("/job/{job_id}")
async def delete_job(job_id: str):
    """Supprime les clips et les données d'un job terminé."""
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job introuvable.")

    job = JOBS[job_id]
    # Supprimer les fichiers de sortie
    for clip in job.get("clips", []):
        try:
            Path(clip["path"]).unlink(missing_ok=True)
        except Exception:
            pass

    del JOBS[job_id]
    return {"message": f"Job {job_id[:8]} supprimé."}


# ─────────────────────────────────────────────────────────────────────────────
# Démarrage
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    check_requirements()

    PORT = int(os.getenv("PORT", 8000))

    # Lancer le tunnel Cloudflare dans un thread séparé
    logger.info("🚀 Démarrage du tunnel Cloudflare...")
    start_cloudflare_tunnel(PORT)

    logger.info(f"🖥️  Démarrage du serveur sur le port {PORT}...")
    uvicorn.run(
        "main:app",
        host="127.0.0.1",   # Écoute local uniquement (le tunnel assure l'accès public)
        port=PORT,
        log_level="warning",  # Uvicorn silencieux, on gère nos propres logs
        reload=False,
    )

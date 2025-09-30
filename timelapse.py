# timelapse.py
# Module timelapse SANS optical flow : extraction robuste avec reprise, construction vidéo finale.
# Toutes les écritures se font sous /tmp/appdata pour éviter tout trigger de reload.

import os
import cv2
import subprocess
import shutil
import stat
import tarfile
import time
import json
from pathlib import Path
from typing import Optional, Tuple, List

BASE_DIR = Path("/tmp/appdata")
TIMELAPSE_DIR = BASE_DIR / "timelapse_jobs"
TIMELAPSE_DIR.mkdir(parents=True, exist_ok=True)

# ---------------- Détection / fallback ffmpeg ----------------

def _telecharger_ffmpeg_statique(dest_dir: Path) -> str:
    """
    Télécharge un build statique ffmpeg amd64 et renvoie le chemin du binaire.
    Attention : nécessite l’accès réseau côté plateforme.
    """
    import urllib.request
    dest_dir.mkdir(parents=True, exist_ok=True)
    url = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
    archive = dest_dir / "ffmpeg-release-amd64-static.tar.xz"
    urllib.request.urlretrieve(url, str(archive))
    with tarfile.open(archive, "r:xz") as tf:
        members = [m for m in tf.getmembers() if m.name.endswith("/ffmpeg")]
        if not members:
            raise RuntimeError("Archive ffmpeg invalide : binaire non trouvé.")
        tf.extractall(path=dest_dir)
    for p in dest_dir.glob("ffmpeg-*-amd64-static/ffmpeg"):
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return str(p)
    raise RuntimeError("Binaire ffmpeg introuvable après extraction.")

def _resoudre_binaire(nom_env: str, nom: str) -> Optional[str]:
    """
    Recherche un binaire :
    1) variable d’environnement
    2) shutil.which
    3) imageio-ffmpeg
    4) cache statique sous /tmp/appdata/ffmpeg-bin, sinon téléchargement
    """
    import shutil as _sh
    cand = os.environ.get(nom_env)
    if cand and Path(cand).exists():
        return cand
    which = _sh.which(nom)
    if which:
        return which
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and Path(p).exists():
            return p
    except Exception:
        pass
    cache_dir = BASE_DIR / "ffmpeg-bin"
    for p in cache_dir.glob("ffmpeg-*-amd64-static/ffmpeg"):
        if p.exists():
            return str(p)
    try:
        return _telecharger_ffmpeg_statique(cache_dir)
    except Exception:
        return None

def chemin_ffmpeg() -> str:
    """
    Renvoie le chemin ffmpeg, lève RuntimeError si introuvable et téléchargement impossible.
    """
    p = _resoudre_binaire("FFMPEG_BINARY", "ffmpeg")
    if not p:
        raise RuntimeError("ffmpeg introuvable et fallback impossible (réseau bloqué ?).")
    return p

# ---------------- Utilitaires timelapse ----------------

def progres_path(job_dir: Path) -> Path:
    return job_dir / "progress.json"

def charger_progress(job_dir: Path) -> dict:
    p = progres_path(job_dir)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def sauver_progress(job_dir: Path, d: dict) -> None:
    p = progres_path(job_dir)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

def ouvrir_capture(chemin_video: str, debut: Optional[int], fin: Optional[int]) -> Tuple[cv2.VideoCapture, float, int, int]:
    """
    Ouvre la vidéo avec OpenCV et renvoie (cap, fps, frame_start, frame_end).
    """
    cap = cv2.VideoCapture(chemin_video)
    if not cap.isOpened():
        raise RuntimeError("Impossible d’ouvrir la vidéo source (OpenCV).")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    nb = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if debut is None: debut = 0
    if fin is None or fin <= 0: fin = int(round(nb / fps))
    if fin <= debut: fin = debut + 1
    frame_start = int(round(debut * fps))
    frame_end = min(nb, int(round(fin * fps)))
    frame_start = max(0, frame_start)
    frame_end = max(frame_start + 1, frame_end)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_start)
    return cap, float(fps), frame_start, frame_end

def extraire_images_avec_reprise(src_path: str, job_dir: Path, fps_cible: int,
                                 debut: Optional[int], fin: Optional[int], batch_frames: int = 1200) -> Tuple[int, int]:
    """
    Parcourt la vidéo par lots, échantillonne à fps_cible et sauve dans job_dir/images en reprenant si besoin.
    Retourne (fps_source_arrondi, nb_images_total).
    """
    images_dir = job_dir / "images"
    images_dir.mkdir(exist_ok=True)

    cap, fps, frame_start, frame_end = ouvrir_capture(src_path, debut, fin)
    ratio_saut = max(1, int(round(fps / float(fps_cible))))

    existantes = sorted(images_dir.glob("frame_*.jpg"))
    next_index = 0
    if existantes:
        try:
            next_index = int(existantes[-1].stem.split("_")[1]) + 1
        except Exception:
            next_index = len(existantes)

    frame_pos = frame_start + next_index * ratio_saut
    if frame_pos < frame_end:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_pos)

    total = next_index
    while frame_pos < frame_end:
        courant = 0
        lot: List = []
        while courant < batch_frames and frame_pos < frame_end:
            ok, img = cap.read()
            if not ok:
                break
            if ((frame_pos - frame_start) % ratio_saut) == 0:
                lot.append(img)
            courant += 1
            frame_pos += 1

        if not lot:
            continue

        for img in lot:
            cv2.imwrite(str(images_dir / f"frame_{total:06d}.jpg"), img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            total += 1

        sauver_progress(job_dir, {
            "fps_source": fps,
            "frame_start": frame_start,
            "frame_end": frame_end,
            "ratio_saut": ratio_saut,
            "images_sauvegardees": total
        })
        time.sleep(0.02)

    cap.release()
    return int(round(fps)), total

def construire_video_depuis_images(job_dir: Path, fps_sortie: int, base_nom: str) -> str:
    """
    Construit la vidéo MP4 à partir des JPEG du job (writer OpenCV),
    puis reconditionne en H.264 + faststart si ffmpeg est disponible.
    """
    images_dir = job_dir / "images"
    fichiers = sorted(images_dir.glob("frame_*.jpg"))
    if not fichiers:
        raise RuntimeError("Aucune image prête pour le timelapse.")
    img0 = cv2.imread(str(fichiers[0]))
    if img0 is None:
        raise RuntimeError("Impossible de lire la première image.")
    h, w = img0.shape[:2]

    out_brut = job_dir / f"{base_nom}_timelapse_{fps_sortie}fps_brut.mp4"
    vw = cv2.VideoWriter(str(out_brut), cv2.VideoWriter_fourcc(*"mp4v"), fps_sortie, (w, h))
    for fp in fichiers:
        im = cv2.imread(str(fp))
        if im is None:
            continue
        if im.shape[:2] != (h, w):
            im = cv2.resize(im, (w, h))
        vw.write(im)
    vw.release()

    out_final = job_dir / f"{base_nom}_timelapse_{fps_sortie}fps.mp4"
    try:
        ffmpeg = chemin_ffmpeg()
    except Exception:
        ffmpeg = None

    if ffmpeg:
        try:
            subprocess.run(
                [ffmpeg, "-y", "-i", str(out_brut), "-vcodec", "libx264", "-preset", "fast", "-crf", "23",
                 "-movflags", "+faststart", str(out_final)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
            )
            return str(out_final)
        except Exception:
            return str(out_brut)
    else:
        return str(out_brut)

def executer_timelapse(src_path: str, job_id: str, base_nom: str, fps: int,
                       debut: Optional[int], fin: Optional[int]) -> Tuple[str, int]:
    """
    Exécute le pipeline timelapse avec reprise. Renvoie (chemin_fichier_final, nb_images).
    """
    job_dir = TIMELAPSE_DIR / f"job_{job_id}"
    (job_dir / "images").mkdir(parents=True, exist_ok=True)
    fps_src, nb = extraire_images_avec_reprise(src_path, job_dir, fps, debut, fin)
    out = construire_video_depuis_images(job_dir, fps, base_nom)
    return out, nb

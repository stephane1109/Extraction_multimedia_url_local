# timelapse.py
# ---------------- Imports ----------------
import os
import cv2
import subprocess
import shutil
import stat
import tarfile
from pathlib import Path

# ---------------- Fallback ffmpeg (env -> which -> imageio-ffmpeg -> binaire statique) ----------------

def _telecharger_ffmpeg_statique(dest_dir: Path) -> str:
    """
    Télécharge un build statique ffmpeg amd64 et renvoie le chemin du binaire.
    ATTENTION : nécessite l’accès réseau côté plateforme.
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

def _resoudre_binaire(nom_env, nom, chemins_standards=("/usr/bin", "/usr/local/bin", "/bin")):
    """
    Recherche un binaire :
    1) variable d’environnement
    2) shutil.which
    3) chemins standards
    4) imageio-ffmpeg
    5) téléchargement d’un build statique dans /tmp/appdata/ffmpeg-bin
    """
    cand_env = os.environ.get(nom_env)
    if cand_env and os.path.exists(cand_env):
        return cand_env
    cand = shutil.which(nom)
    if cand:
        return cand
    for d in chemins_standards:
        p = os.path.join(d, nom)
        if os.path.exists(p):
            return p
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    try:
        cache_dir = Path("/tmp/appdata/ffmpeg-bin")
        for p in cache_dir.glob("ffmpeg-*-amd64-static/ffmpeg"):
            if p.exists():
                return str(p)
        return _telecharger_ffmpeg_statique(cache_dir)
    except Exception:
        return None

def chemin_ffmpeg():
    """
    Retourne un chemin exécutable vers ffmpeg, sinon lève RuntimeError.
    """
    p = _resoudre_binaire("FFMPEG_BINARY", "ffmpeg")
    if not p:
        raise RuntimeError("ffmpeg introuvable et fallback impossible (réseau bloqué ?).")
    return p

# ---------------- Timelapse ----------------

def appliquer_optical_flow(images):
    """
    Applique la visualisation du flux optique sur des images successives.
    """
    images_avec_flow = []
    for i in range(len(images) - 1):
        img1 = cv2.cvtColor(images[i], cv2.COLOR_BGR2GRAY)
        img2 = cv2.cvtColor(images[i + 1], cv2.COLOR_BGR2GRAY)
        flow = cv2.calcOpticalFlowFarneback(img1, img2, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        vis = images[i].copy()
        h, w = img1.shape
        step = 16
        for y in range(0, h, step):
            for x in range(0, w, step):
                fx, fy = flow[y, x]
                cv2.arrowedLine(vis, (x, y), (int(x + fx), int(y + fy)), (0, 255, 0), 1, tipLength=0.4)
        images_avec_flow.append(vis)
    if len(images) > 0:
        images_avec_flow.append(images[-1])
    return images_avec_flow

def extraire_images_echantillonnees(chemin_video, dossier_sortie, fps_cible, avec_flow=False):
    """
    Extrait des images à intervalle régulier pour créer un timelapse (effet stop motion).
    Retourne (fps_original, nb_images).
    """
    cap = cv2.VideoCapture(chemin_video)
    if not cap.isOpened():
        raise RuntimeError("Impossible d’ouvrir la vidéo pour extraction des images.")
    fps_original = cap.get(cv2.CAP_PROP_FPS) or 25.0
    ratio_saut = max(1, int(round(fps_original / float(fps_cible))))
    images_extraites, index = [], 0
    while True:
        ok, image = cap.read()
        if not ok:
            break
        if index % ratio_saut == 0:
            images_extraites.append(image)
        index += 1
    cap.release()
    if avec_flow and len(images_extraites) > 1:
        images_extraites = appliquer_optical_flow(images_extraites)
    os.makedirs(dossier_sortie, exist_ok=True)
    for i, img in enumerate(images_extraites):
        cv2.imwrite(os.path.join(dossier_sortie, f"image_{i:05d}.jpg"), img)
    return int(round(fps_original)), len(images_extraites)

def creer_video_depuis_images(dossier_images, chemin_sortie, fps=12):
    """
    Construit une vidéo MP4 à partir des images JPEG d’un dossier.
    """
    fichiers = sorted([f for f in os.listdir(dossier_images) if f.endswith(".jpg")])
    if not fichiers:
        return None
    image_exemple = cv2.imread(os.path.join(dossier_images, fichiers[0]))
    if image_exemple is None:
        return None
    h, w, _ = image_exemple.shape
    codec = cv2.VideoWriter_fourcc(*'mp4v')
    video = cv2.VideoWriter(chemin_sortie, codec, fps, (w, h))
    for f in fichiers:
        img = cv2.imread(os.path.join(dossier_images, f))
        if img is None:
            continue
        if img.shape[:2] != (h, w):
            img = cv2.resize(img, (w, h))
        video.write(img)
    video.release()
    return chemin_sortie

def reencoder_video_h264(chemin_entree, chemin_sortie):
    """
    Réencode une vidéo en H.264 pour compatibilité maximale (lecteur web).
    """
    ffmpeg = chemin_ffmpeg()
    subprocess.run(
        [ffmpeg, "-y", "-i", chemin_entree, "-vcodec", "libx264", "-preset", "fast", "-crf", "23",
         "-movflags", "+faststart", chemin_sortie],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
    )

def generer_timelapse(chemin_video_source, dossier_sortie, base_court, fps_cible=12, avec_flow=False):
    """
    Pipeline timelapse complet.
    """
    dossier_images = os.path.join(dossier_sortie, f"timelapse_{fps_cible}fps_{base_court}")
    os.makedirs(dossier_images, exist_ok=True)
    fps_origine, nb = extraire_images_echantillonnees(chemin_video_source, dossier_images, fps_cible, avec_flow=avec_flow)
    if nb == 0:
        raise RuntimeError("Aucune image extraite pour le timelapse.")
    chemin_brut = os.path.join(dossier_sortie, f"{base_court}_timelapse_{fps_cible}fps_brut.mp4")
    if creer_video_depuis_images(dossier_images, chemin_brut, fps=fps_cible) is None:
        raise RuntimeError("Echec de la création de la vidéo timelapse brute.")
    chemin_final = os.path.join(dossier_sortie, f"{base_court}_timelapse_{fps_cible}fps.mp4")
    reencoder_video_h264(chemin_brut, chemin_final)
    return chemin_final

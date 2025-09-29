# pip install streamlit yt_dlp

# ---------------- Imports ----------------
import os
# Désactiver le watcher pour éviter "inotify instance limit reached" sur Streamlit Cloud
os.environ["STREAMLIT_SERVER_FILE_WATCHER_TYPE"] = "none"

import streamlit as st
import subprocess
import re
import glob
import unicodedata
import shutil
import zipfile
from io import BytesIO
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# ---------------- Constantes ----------------
SEUIL_APERCU_OCTETS = 160 * 1024 * 1024  # ~160 Mo pour éviter de charger de très gros fichiers en mémoire
LONGUEUR_TITRE_MAX = 24                  # longueur max du titre nettoyé
LONGUEUR_PREFIX_ID = 8                   # longueur de l'ID vidéo utilisé dans le nom
REPERTOIRE_SORTIE = os.path.abspath("fichiers")  # répertoire unique de sortie

# ---------------- Fonctions utilitaires ----------------

def vider_cache():
    # Nettoyage explicite du cache Streamlit
    st.cache_data.clear()

def nettoyer_titre(titre):
    # Normalisation robuste du titre vers un nom de fichier ASCII sûr et court
    if not titre:
        titre = "video"
    titre = titre.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    remplacement = {
        '«': '', '»': '', '“': '', '”': '', '’': '', '‘': '', '„': '',
        '"': '', "'": '', ':': '-', '/': '-', '\\': '-', '|': '-',
        '?': '', '*': '', '<': '', '>': '', '\u00A0': ' '
    }
    for k, v in remplacement.items():
        titre = titre.replace(k, v)
    titre = unicodedata.normalize('NFKD', titre)
    titre = ''.join(c for c in titre if not unicodedata.combining(c))
    titre = re.sub(r'[^\w\s-]', '', titre, flags=re.UNICODE)
    titre = re.sub(r'\s+', '_', titre.strip())
    if not titre:
        titre = "video"
    return titre[:LONGUEUR_TITRE_MAX]

def generer_nom_base(video_id, titre):
    # Préfixe très court et stable: <id8>_<titreCourt>
    vid = (video_id or "vid")[:LONGUEUR_PREFIX_ID]
    tit = nettoyer_titre(titre)
    return f"{vid}_{tit}"

def ffmpeg_disponible():
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False

def renommer_sans_collision(src_path, dest_path_base, ext=".mp4"):
    # Renommage atomique en évitant toute collision
    candidat = dest_path_base + ext
    i = 1
    while os.path.exists(candidat):
        candidat = f"{dest_path_base}_{i}{ext}"
        i += 1
    shutil.move(src_path, candidat)
    return candidat

def taille_fichier(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return None

def lister_fichiers(patterns):
    # Retourne une liste de chemins correspondant à une liste de patterns glob
    files = []
    for p in patterns:
        files.extend(glob.glob(p))
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files

def zipper_fichiers(fichiers, nom_zip_sans_ext):
    # Crée un ZIP en mémoire avec uniquement les fichiers donnés
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for abs_path in fichiers:
            if os.path.isfile(abs_path):
                zf.write(abs_path, arcname=os.path.basename(abs_path))
    buffer.seek(0)
    return buffer, f"{nom_zip_sans_ext}.zip"

def afficher_video_depuis_fichier(video_path, placeholder):
    # Affichage SANS JAMAIS appeler st.video(path). On passe des bytes à st.video, via un seul placeholder.
    if not os.path.exists(video_path):
        return
    size = taille_fichier(video_path)
    if size is None or size > SEUIL_APERCU_OCTETS:
        return
    try:
        with open(video_path, "rb") as f:
            data = f.read()
        with placeholder:
            st.video(data, format="video/mp4")
    except Exception:
        pass

def duree_video_seconds(video_path):
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, check=True
        )
        return int(float(res.stdout.strip()))
    except Exception:
        return None

def collecter_sorties_run(prefix):
    # Récupère tous les fichiers produits pour ce run, identifiables par leur préfixe
    patterns = [
        os.path.join(REPERTOIRE_SORTIE, f"{prefix}*.mp4"),
        os.path.join(REPERTOIRE_SORTIE, f"{prefix}*.mp3"),
        os.path.join(REPERTOIRE_SORTIE, f"{prefix}*.wav"),
        os.path.join(REPERTOIRE_SORTIE, f"img1_{prefix}", "i_*.jpg"),
        os.path.join(REPERTOIRE_SORTIE, f"img25_{prefix}", "i_*.jpg"),
        os.path.join(REPERTOIRE_SORTIE, f"img1_full_{prefix}", "i_*.jpg"),
        os.path.join(REPERTOIRE_SORTIE, f"img25_full_{prefix}", "i_*.jpg"),
    ]
    return lister_fichiers(patterns)

# ---------------- Téléchargement + préparation vidéo ----------------

def telecharger_preparer_video(url, cookies_path, verbose, qualite):
    # Téléchargement via yt-dlp, puis préparation de la vidéo de base selon la qualité choisie
    st.write("Téléchargement / préparation de la vidéo en cours...")

    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0"
    http_headers = {
        'User-Agent': user_agent,
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Referer': 'https://www.youtube.com/'
    }

    formats_fallbacks = [
        "bv*[ext=mp4][height<=2160]+ba[ext=m4a]/b[ext=mp4]/b",
        "bv*+ba/b"
    ]

    base_opts = {
        'paths': {'home': REPERTOIRE_SORTIE},
        'outtmpl': {'default': '%(id)s.%(ext)s'},  # nom temporaire sûr
        'noplaylist': True,
        'quiet': not verbose,
        'no_warnings': not verbose,
        'merge_output_format': 'mp4',
        'retries': 10,
        'fragment_retries': 10,
        'continuedl': True,
        'concurrent_fragment_downloads': 1,
        'http_headers': http_headers,
        'geo_bypass': True,
        'nocheckcertificate': True,
        'restrictfilenames': True,
        'trim_file_name': 80,
        'extractor_args': {'youtube': {'player_client': ['android', 'ios', 'mweb', 'web']}}
    }

    if cookies_path:
        base_opts['cookiefile'] = cookies_path

    derniere_erreur = None
    info = None
    fichier_final = None

    for fmt in formats_fallbacks:
        ydl_opts = base_opts.copy()
        ydl_opts['format'] = fmt
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                _ = ydl.prepare_filename(info)
            candidats = lister_fichiers([os.path.join(REPERTOIRE_SORTIE, f"*.{ext}") for ext in ['mp4','mkv','webm','m4a','mp3']])
            if not candidats:
                raise DownloadError("Téléchargement terminé mais aucun fichier détecté (download is empty).")
            fichier_final = candidats[0]
            break
        except Exception as e:
            derniere_erreur = e
            continue

    if fichier_final is None:
        msg = str(derniere_erreur) if derniere_erreur else "Echec inconnu."
        if "403" in msg or "Forbidden" in msg:
            msg += " — HTTP 403 détecté. Fournis un cookies.txt exporté de ton navigateur."
        return None, None, None, msg

    video_id = (info.get('id') if info else "vid") or "vid"
    titre_brut = (info.get('title') if info else os.path.splitext(os.path.basename(fichier_final))[0]) or "video"
    base_court = generer_nom_base(video_id, titre_brut)

    # Déplacement du fichier source vers un nom propre intermédiaire
    ext_src = os.path.splitext(fichier_final)[1]
    src_base = os.path.join(REPERTOIRE_SORTIE, f"{base_court}_src")
    chemin_source_propre = renommer_sans_collision(fichier_final, src_base, ext=ext_src)

    # Préparer la vidéo « de travail » selon la qualité demandée
    if qualite == "Compressée (1280p, CRF 28)":
        cible = os.path.join(REPERTOIRE_SORTIE, f"{base_court}_video.mp4")
        try:
            subprocess.run([
                "ffmpeg", "-y", "-i", chemin_source_propre,
                "-vf", "scale=1280:-2",
                "-c:v", "libx264",
                "-preset", "slow",
                "-crf", "28",
                "-c:a", "aac", "-b:a", "96k",
                cible
            ], check=True)
        except Exception as e:
            return None, None, None, f"Echec de la compression : {e}"
    else:
        # HD (max) : copie/remux vers mp4
        cible = os.path.join(REPERTOIRE_SORTIE, f"{base_court}_video.mp4")
        try:
            subprocess.run(["ffmpeg", "-y", "-i", chemin_source_propre, "-c", "copy", "-movflags", "+faststart", cible], check=True)
        except Exception:
            try:
                subprocess.run([
                    "ffmpeg", "-y", "-i", chemin_source_propre,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "192k", cible
                ], check=True)
            except Exception as e:
                return None, None, None, f"Echec du remux/transcodage : {e}"

    # Nettoyage de la source temporaire
    try:
        if os.path.exists(chemin_source_propre):
            os.remove(chemin_source_propre)
    except Exception:
        pass

    return cible, base_court, info, None

# ---------------- Extraction des ressources ----------

# pip install streamlit yt_dlp opencv-python-headless numpy imageio-ffmpeg

# ---------------- Imports ----------------
import os
os.environ["STREAMLIT_SERVER_FILE_WATCHER_TYPE"] = "none"  # éviter les watchers inotify

import streamlit as st
import subprocess
import re
import glob
import unicodedata
import shutil
import zipfile
import tempfile
from io import BytesIO
import importlib.util
import pathlib
import cv2
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# ---------------- Import résilient timelapse ----------------
def _import_timelapse_resilient():
    here = pathlib.Path(__file__).parent
    try:
        from timelapse import generer_timelapse, chemin_ffmpeg
        return generer_timelapse, chemin_ffmpeg
    except Exception:
        mod_path = here / "timelapse.py"
        if mod_path.exists():
            spec = importlib.util.spec_from_file_location("timelapse_dynamic", str(mod_path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.generer_timelapse, mod.chemin_ffmpeg
        raise

generer_timelapse, chemin_ffmpeg = _import_timelapse_resilient()

# ---------------- Répertoires: tout dans /tmp ----------------
BASE_DIR = os.path.join(tempfile.gettempdir(), "extraction_mm")
REPERTOIRE_SORTIE = os.path.join(BASE_DIR, "out")
REPERTOIRE_TEMP = os.path.join(BASE_DIR, "tmp")
os.makedirs(REPERTOIRE_SORTIE, exist_ok=True)
os.makedirs(REPERTOIRE_TEMP, exist_ok=True)

# ---------------- Constantes ----------------
SEUIL_APERCU_OCTETS = 160 * 1024 * 1024
LONGUEUR_TITRE_MAX = 24
LONGUEUR_PREFIX_ID = 8

# ---------------- Utilitaires ----------------

def vider_cache():
    st.cache_data.clear()

def ffmpeg_disponible():
    try:
        _ = chemin_ffmpeg()
        return True
    except Exception:
        return False

def nettoyer_titre(titre):
    if not titre:
        titre = "video"
    titre = titre.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    remplacement = {
        '«':'','»':'','“':'','”':'','’':'','‘':'','„':'',
        '"':'',"'":'',
        ':':'-','/':'-','\\':'-','|':'-','?':'','*':'','<':'','>':'','\u00A0':' '
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
    vid = (video_id or "vid")[:LONGUEUR_PREFIX_ID]
    tit = nettoyer_titre(titre)
    return f"{vid}_{tit}"

def renommer_sans_collision(src_path, dest_path_base, ext=".mp4"):
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
    files = []
    for p in patterns:
        files.extend(glob.glob(p))
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files

def zipper_fichiers(fichiers, nom_zip_sans_ext):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for abs_path in fichiers:
            if os.path.isfile(abs_path):
                zf.write(abs_path, arcname=os.path.basename(abs_path))
    buffer.seek(0)
    return buffer, f"{nom_zip_sans_ext}.zip"

def duree_video_seconds(video_path):
    """
    Calcule la durée via OpenCV.
    """
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        if fps > 0:
            return int(round(frames / fps))
        return None
    except Exception:
        return None

def collecter_sorties_run(prefix):
    patterns = [
        os.path.join(REPERTOIRE_SORTIE, f"{prefix}*.mp4"),
        os.path.join(REPERTOIRE_SORTIE, f"{prefix}*.mp3"),
        os.path.join(REPERTOIRE_SORTIE, f"{prefix}*.wav"),
        os.path.join(REPERTOIRE_SORTIE, f"img1_{prefix}", "i_*.jpg"),
        os.path.join(REPERTOIRE_SORTIE, f"img25_{prefix}", "i_*.jpg"),
        os.path.join(REPERTOIRE_SORTIE, f"img1_full_{prefix}", "i_*.jpg"),
        os.path.join(REPERTOIRE_SORTIE, f"img25_full_{prefix}", "i_*.jpg"),
        os.path.join(REPERTOIRE_SORTIE, f"timelapse_*_{prefix}", "*.jpg"),
    ]
    return lister_fichiers(patterns)

def copier_upload_local_stable(uploader, titre_hint="local"):
    if uploader is None:
        return None, None
    base = generer_nom_base("local", nettoyer_titre(os.path.splitext(uploader.name)[0] or titre_hint))
    dest = os.path.join(REPERTOIRE_TEMP, f"{base}_upload.mp4")
    with open(dest, "wb") as f:
        f.write(uploader.read())
    return dest, base

class _SilentLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass

# ---------------- Téléchargement / préparation vidéo ----------------

def telecharger_preparer_video(url, cookies_path, verbose, qualite, utiliser_intervalle, debut, fin):
    st.write("Téléchargement / préparation de la vidéo en cours...")

    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0"
    http_headers = {'User-Agent': user_agent, 'Accept': '*/*', 'Accept-Language': 'en-US,en;q=0.5', 'Referer': 'https://www.youtube.com/'}

    base_opts = {
        'paths': {'home': REPERTOIRE_SORTIE},
        'outtmpl': {'default': '%(id)s.%(ext)s'},
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
        'extractor_args': {'youtube': {'player_client': ['android', 'ios', 'mweb', 'web']}},
    }
    if not verbose:
        base_opts['logger'] = _SilentLogger()

    if utiliser_intervalle:
        base_opts['download_sections'] = [{'section': f"*{debut}-{fin}"}]
        base_opts['force_keyframes_at_cuts'] = True

    if cookies_path:
        base_opts['cookiefile'] = cookies_path

    formats_fallbacks = [
        "bv*[ext=mp4][height<=2160]+ba[ext=m4a]/b[ext=mp4]/b",
        "bv*+ba/b"
    ]

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
            msg = str(e) or repr(e)
            derniere_erreur = e
            if "403" in msg or "Forbidden" in msg:
                if not cookies_path:
                    return None, None, None, "HTTP 403 détecté. La vidéo semble restreinte. Fournis un fichier cookies.txt (Firefox : cookies.txt) puis relance."
                return None, None, None, "HTTP 403 persistant malgré cookies. Vérifie que le cookies.txt est valide et récent."
            continue

    if fichier_final is None:
        return None, None, None, (str(derniere_erreur) if derniere_erreur else "Echec inconnu au téléchargement.")

    video_id = (info.get('id') if info else "vid") or "vid"
    titre_brut = (info.get('title') if info else os.path.splitext(os.path.basename(fichier_final))[0]) or "video"
    base_court = generer_nom_base(video_id, titre_brut)

    # Renom propre intermédiaire
    ext_src = os.path.splitext(fichier_final)[1]
    src_base = os.path.join(REPERTOIRE_SORTIE, f"{base_court}_src")
    chemin_source_propre = renommer_sans_collision(fichier_final, src_base, ext=ext_src)

    # Préparer la vidéo de travail
    cible = os.path.join(REPERTOIRE_SORTIE, f"{base_court}_video.mp4")
    ffmpeg = chemin_ffmpeg()

    def _run_ffmpeg(args):
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    if qualite == "Compressée (1280p, CRF 28)":
        try:
            _run_ffmpeg([
                ffmpeg, "-y", "-i", chemin_source_propre,
                "-vf", "scale=1280:-2",
                "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", cible
            ])
        except Exception as e:
            return None, None, None, f"Echec de la compression : {e}"
    else:
        try:
            _run_ffmpeg([ffmpeg, "-y", "-i", chemin_source_propre, "-c", "copy", "-movflags", "+faststart", cible])
        except Exception:
            try:
                _run_ffmpeg([
                    ffmpeg, "-y", "-i", chemin_source_propre,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", cible
                ])
            except Exception as e:
                return None, None, None, f"Echec du remux/transcodage : {e}"

    try:
        if os.path.exists(chemin_source_propre):
            os.remove(chemin_source_propre)
    except Exception:
        pass

    return cible, base_court, info, None

# ---------------- Extraction des ressources ----------------

def extraire_ressources(video_path, debut, fin, base_court, options, utiliser_intervalle):
    try:
        ffmpeg = chemin_ffmpeg()

        def _run_ffmpeg(args):
            subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        def cmd_segment(sortie):
            if utiliser_intervalle:
                return [ffmpeg, "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path,
                        "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", sortie]
            else:
                return [ffmpeg, "-y", "-i", video_path,
                        "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", sortie]

        def cmd_audio(sortie, codec_args):
            if utiliser_intervalle:
                return [ffmpeg, "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path] + codec_args + ["-movflags", "+faststart", sortie]
            else:
                return [ffmpeg, "-y", "-i", video_path] + codec_args + ["-movflags", "+faststart", sortie]

        def cmd_images(output_pattern, fps):
            vf = f"fps={fps},scale=1920:1080"
            if utiliser_intervalle:
                return [ffmpeg, "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path, "-vf", vf, "-q:v", "1", output_pattern]
            else:
                return [ffmpeg, "-y", "-i", video_path, "-vf", vf, "-q:v", "1", output_pattern]

        if options.get("mp4"):
            nom = f"{base_court}_seg.mp4" if utiliser_intervalle else f"{base_court}_full.mp4"
            _run_ffmpeg(cmd_segment(os.path.join(REPERTOIRE_SORTIE, nom)))

        if options.get("mp3"):
            nom = f"{base_court}_seg.mp3" if utiliser_intervalle else f"{base_court}_full.mp3"
            _run_ffmpeg(cmd_audio(os.path.join(REPERTOIRE_SORTIE, nom), ["-vn", "-acodec", "libmp3lame", "-q:a", "5"]))

        if options.get("wav"):
            nom = f"{base_court}_seg.wav" if utiliser_intervalle else f"{base_court}_full.wav"
            _run_ffmpeg(cmd_audio(os.path.join(REPERTOIRE_SORTIE, nom), ["-vn", "-acodec", "adpcm_ima_wav"]))

        if options.get("img1") or options.get("img25"):
            for fps in [1, 25]:
                if (fps == 1 and options.get("img1")) or (fps == 25 and options.get("img25")):
                    dossier = f"img{fps}_{base_court}" if utiliser_intervalle else f"img{fps}_full_{base_court}"
                    rep = os.path.join(REPERTOIRE_SORTIE, dossier)
                    os.makedirs(rep, exist_ok=True)
                    tmp_pattern = os.path.join(rep, "tmp_%06d.jpg")
                    _run_ffmpeg(cmd_images(tmp_pattern, fps))

                    images_gen = sorted(glob.glob(os.path.join(rep, "tmp_*.jpg")))
                    start_offset = debut if utiliser_intervalle else 0
                    for i, src in enumerate(images_gen):
                        t_sec_float = start_offset + (i / float(fps))
                        sec = int(t_sec_float)
                        if fps == 1:
                            nom_cible = f"i_{sec}s_1fps.jpg"
                        else:
                            frame_in_sec = int(round((t_sec_float - sec) * fps))
                            if frame_in_sec >= fps:
                                frame_in_sec = fps - 1
                            nom_cible = f"i_{sec}s_{fps}fps_{frame_in_sec:02d}.jpg"
                        dst = os.path.join(rep, nom_cible)
                        j = 1
                        base_dst, ext = os.path.splitext(dst)
                        while os.path.exists(dst):
                            dst = f"{base_dst}_{j}{ext}"
                            j += 1
                        os.replace(src, dst)

        return None
    except Exception as e:
        return str(e)

# ---------------- Interface utilisateur ----------------

st.title("Extraction multimédia (vidéo, audio, images)")
st.markdown("**[www.codeandcortex.fr](http://www.codeandcortex.fr)**")

vider_cache()

st.markdown(
    "Par défaut, l’extraction porte sur **toute la vidéo**. Vous pouvez activer un intervalle personnalisé si besoin. "
    "Si la vidéo est restreinte (403), exportez vos cookies avec l’extension Firefox : "
    "[cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)."
)

# Etats initiaux et verrou d’exécution
st.session_state.setdefault("busy", False)
st.session_state.setdefault("debut_secs", 0)
st.session_state.setdefault("fin_secs", 10)
st.session_state.setdefault("local_temp_path", None)
st.session_state.setdefault("local_name_base", None)
st.session_state.setdefault("video_base", None)
st.session_state.setdefault("base_court", None)
st.session_state.setdefault("apercu_local_bytes", None)
st.session_state.setdefault("upload_signature", None)

# Source
url = st.text_input("URL YouTube")
cookies_file = st.file_uploader("Fichier cookies.txt (optionnel)", type=["txt"])
fichier_local = st.file_uploader("Ou importer un fichier vidéo (.mp4)", type=["mp4"])

mode_verbose = st.checkbox("Mode diagnostic yt-dl", value=False)
qualite = st.radio("Qualité de la vidéo de base", ["Compressée (1280p, CRF 28)", "HD (max qualité dispo)"], index=0)

# Ressources à produire
st.subheader("Ressources à produire")
st.markdown("<style>div[data-testid='stHorizontalBlock'] label { white-space: nowrap; }</style>", unsafe_allow_html=True)
c1, c2, c3, c4, c5, c6 = st.columns([1,1,1,1,1,1])
with c1: opt_mp4 = st.checkbox("MP4", key="opt_mp4")
with c2: opt_mp3 = st.checkbox("MP3", key="opt_mp3")
with c3: opt_wav = st.checkbox("WAV", key="opt_wav")
with c4: opt_img1 = st.checkbox("Img 1 FPS", key="opt_img1")
with c5: opt_img25 = st.checkbox("Img 25 FPS", key="opt_img25")
with c6: opt_timelapse = st.checkbox("Timelapse", key="opt_timelapse")

# Timelapse options
if opt_timelapse:
    col_t1, col_t2 = st.columns([1,1])
    with col_t1:
        fps_timelapse = st.selectbox("FPS timelapse", [4, 6, 8, 10, 12, 14, 16], index=2, key="fps_timelapse")
    with col_t2:
        opt_flow = st.checkbox("Flux optique (overlay)", value=False, key="opt_flow")
else:
    fps_timelapse = 12
    opt_flow = False

# Étendue
st.subheader("Étendue")
etendue = st.radio("Choisir l’étendue", ["Toute la vidéo", "Intervalle personnalisé"], index=0)
if etendue == "Interval

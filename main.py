# pip install streamlit yt_dlp

# ---------------- Imports ----------------
import os
os.environ["STREAMLIT_SERVER_FILE_WATCHER_TYPE"] = "none"  # éviter inotify sur Streamlit Cloud

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
SEUIL_APERCU_OCTETS = 160 * 1024 * 1024  # seuil d’aperçu pour éviter de charger des gros fichiers
LONGUEUR_TITRE_MAX = 24
LONGUEUR_PREFIX_ID = 8
REPERTOIRE_SORTIE = os.path.abspath("fichiers")  # unique dossier de sortie

# ---------------- Utilitaires ----------------

def vider_cache():
    # Vide explicitement le cache Streamlit
    st.cache_data.clear()

def nettoyer_titre(titre):
    # Nettoyage robuste et raccourcissement strict
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
    # Préfixe court et stable: <id8>_<TitreCourt>
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
    # Renommage atomique sans collision
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

# ---------------- Téléchargement / préparation vidéo ----------------

def telecharger_preparer_video(url, cookies_path, verbose, qualite, utiliser_intervalle, debut, fin):
    """
    Si utiliser_intervalle = True, on ne télécharge que l'intervalle [debut, fin)
    via yt-dlp 'download_sections' et on force les keyframes à la coupe.
    Sinon, on télécharge la vidéo complète.
    Puis on prépare une vidéo de travail MP4 selon la qualité.
    """
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
        'extractor_args': {'youtube': {'player_client': ['android', 'ios', 'mweb', 'web']}}
    }

    # Téléchargement de SECTIONS si demandé
    if utiliser_intervalle:
        # équivaut à --download-sections "*start-end"
        base_opts['download_sections'] = [ {'section': f"*{debut}-{fin}"} ]
        base_opts['force_keyframes_at_cuts'] = True

    if cookies_path:
        base_opts['cookiefile'] = cookies_path

    if not ffmpeg_disponible():
        st.warning("ffmpeg n’est pas détecté. Ajoute 'ffmpeg' dans packages.txt.")

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

    # Préparer la vidéo de travail selon la qualité
    cible = os.path.join(REPERTOIRE_SORTIE, f"{base_court}_video.mp4")
    if qualite == "Compressée (1280p, CRF 28)":
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
        # HD (max) : remux en mp4 si possible, sinon transcodage léger
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

    # Nettoyage
    try:
        if os.path.exists(chemin_source_propre):
            os.remove(chemin_source_propre)
    except Exception:
        pass

    return cible, base_court, info, None

# ---------------- Extraction des ressources ----------------

def extraire_ressources(video_path, debut, fin, base_court, options, utiliser_intervalle):
    try:
        def cmd_segment(sortie):
            if utiliser_intervalle:
                return [
                    "ffmpeg", "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path,
                    "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                    "-c:a", "aac", "-b:a", "96k", sortie
                ]
            else:
                return [
                    "ffmpeg", "-y", "-i", video_path,
                    "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                    "-c:a", "aac", "-b:a", "96k", sortie
                ]

        def cmd_audio(sortie, codec_args):
            if utiliser_intervalle:
                return ["ffmpeg", "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path] + codec_args + [sortie]
            else:
                return ["ffmpeg", "-y", "-i", video_path] + codec_args + [sortie]

        def cmd_images(output_pattern, fps):
            vf = f"fps={fps},scale=1920:1080"
            if utiliser_intervalle:
                return ["ffmpeg", "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path, "-vf", vf, "-q:v", "1", output_pattern]
            else:
                return ["ffmpeg", "-y", "-i", video_path, "-vf", vf, "-q:v", "1", output_pattern]

        if options.get("mp4"):
            nom = f"{base_court}_seg.mp4" if utiliser_intervalle else f"{base_court}_full.mp4"
            subprocess.run(cmd_segment(os.path.join(REPERTOIRE_SORTIE, nom)), check=True)

        if options.get("mp3"):
            nom = f"{base_court}_seg.mp3" if utiliser_intervalle else f"{base_court}_full.mp3"
            subprocess.run(cmd_audio(os.path.join(REPERTOIRE_SORTIE, nom), ["-vn", "-acodec", "libmp3lame", "-q:a", "5"]), check=True)

        if options.get("wav"):
            nom = f"{base_court}_seg.wav" if utiliser_intervalle else f"{base_court}_full.wav"
            subprocess.run(cmd_audio(os.path.join(REPERTOIRE_SORTIE, nom), ["-vn", "-acodec", "adpcm_ima_wav"]), check=True)

        if options.get("img1") or options.get("img25"):
            for fps in [1, 25]:
                if (fps == 1 and options.get("img1")) or (fps == 25 and options.get("img25")):
                    dossier = f"img{fps}_{base_court}" if utiliser_intervalle else f"img{fps}_full_{base_court}"
                    rep = os.path.join(REPERTOIRE_SORTIE, dossier)
                    os.makedirs(rep, exist_ok=True)
                    output_pattern = os.path.join(rep, "i_%04d.jpg")
                    subprocess.run(cmd_images(output_pattern, fps), check=True)

        return None
    except Exception as e:
        return str(e)

# ---------------- Interface utilisateur ----------------

st.title("Extraction multimédia (vidéo, audio, images)")
st.markdown("**[www.codeandcortex.fr](http://www.codeandcortex.fr)**")

vider_cache()
os.makedirs(REPERTOIRE_SORTIE, exist_ok=True)

# Note sous le titre (lien cookies cliquable)
st.markdown(
    "Par défaut, l’extraction porte sur **toute la vidéo**. Vous pouvez activer un intervalle personnalisé si besoin. "
    "Si la vidéo est restreinte (403), exportez vos cookies avec l’extension Firefox : "
    "[cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)."
)

# URL + cookies
url = st.text_input("URL YouTube")
cookies_file = st.file_uploader("Fichier cookies.txt (optionnel)", type=["txt"])

# Upload local (aperçu immédiat)
fichier_local = st.file_uploader("Ou importer un fichier vidéo (.mp4)", type=["mp4"])

# Mode diagnostic + qualité
mode_verbose = st.checkbox("Mode diagnostic yt-dl")
qualite = st.radio("Qualité de la vidéo de base", ["Compressée (1280p, CRF 28)", "HD (max qualité dispo)"], index=0)

# Ressources à produire alignées
st.subheader("Ressources à produire")
st.markdown("""
    <style>
    div[data-testid="stHorizontalBlock"] label { white-space: nowrap; }
    </style>
""", unsafe_allow_html=True)
col_r1, col_r2, col_r3, col_r4, col_r5 = st.columns([1,1,1,1,1])
with col_r1:
    opt_mp4 = st.checkbox("MP4", key="opt_mp4")
with col_r2:
    opt_mp3 = st.checkbox("MP3", key="opt_mp3")
with col_r3:
    opt_wav = st.checkbox("WAV", key="opt_wav")
with col_r4:
    opt_img1 = st.checkbox("Img 1 FPS", key="opt_img1")
with col_r5:
    opt_img25 = st.checkbox("Img 25 FPS", key="opt_img25")

# Étendue et intervalle
st.subheader("Étendue")
etendue = st.radio("Choisir l’étendue", ["Toute la vidéo", "Intervalle personnalisé"], index=0)
if etendue == "Intervalle personnalisé":
    col1, col2 = st.columns(2)
    debut = col1.number_input("Début (s)", min_value=0, value=0)
    fin = col2.number_input("Fin (s)", min_value=1, value=10)
    utiliser_intervalle = True
    if fin <= debut:
        st.warning("La fin doit être strictement supérieure au début.")
else:
    debut, fin = 0, 0
    utiliser_intervalle = False

# Aperçu unique controlé par placeholder
afficher_apercu = st.checkbox("Afficher l’aperçu vidéo", value=True)
apercu_placeholder = st.empty()

# Aperçu immédiat si fichier local avant traitement
if afficher_apercu and fichier_local is not None:
    try:
        pos = fichier_local.tell()
        data_local = fichier_local.read()
        fichier_local.seek(pos)  # remettre le curseur
        if len(data_local) <= SEUIL_APERCU_OCTETS:
            with apercu_placeholder:
                st.video(data_local, format="video/mp4")
        else:
            apercu_placeholder.info("Fichier local volumineux : aperçu désactivé (télécharge pour prévisualiser).")
    except Exception:
        pass

# Bouton unique « Lancer le traitement »
if st.button("Lancer le traitement"):
    cookies_path = None
    if cookies_file:
        cookies_path = os.path.join(REPERTOIRE_SORTIE, "cookies.txt")
        with open(cookies_path, "wb") as f:
            f.write(cookies_file.read())

    # Cas URL
    if url:
        video_base, base_court, info, err = telecharger_preparer_video(
            url, cookies_path, mode_verbose, qualite, utiliser_intervalle, debut, fin
        )
        if err:
            st.error(f"Erreur : {err}")
        else:
            st.session_state['video_base'] = video_base
            st.session_state['base_court'] = base_court
            st.success(f"Vidéo prête : {os.path.basename(video_base)}")

    # Cas fichier local
    elif fichier_local:
        titre_net = nettoyer_titre(os.path.splitext(fichier_local.name)[0])
        base_court = generer_nom_base("local", titre_net)
        chemin_temp = os.path.join(REPERTOIRE_SORTIE, f"{base_court}_src.mp4")
        with open(chemin_temp, "wb") as f:
            f.write(fichier_local.read())
        cible = os.path.join(REPERTOIRE_SORTIE, f"{base_court}_video.mp4")
        if qualite == "Compressée (1280p, CRF 28)":
            try:
                if utiliser_intervalle:
                    subprocess.run([
                        "ffmpeg", "-y", "-ss", str(debut), "-to", str(fin), "-i", chemin_temp,
                        "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                        "-c:a", "aac", "-b:a", "96k", cible
                    ], check=True)
                else:
                    subprocess.run([
                        "ffmpeg", "-y", "-i", chemin_temp,
                        "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                        "-c:a", "aac", "-b:a", "96k", cible
                    ], check=True)
            except Exception as e:
                st.error(f"Echec de la compression locale : {e}")
                cible = None
        else:
            try:
                if utiliser_intervalle:
                    subprocess.run([
                        "ffmpeg", "-y", "-ss", str(debut), "-to", str(fin), "-i", chemin_temp,
                        "-c", "copy", "-movflags", "+faststart", cible
                    ], check=True)
                else:
                    subprocess.run(["ffmpeg", "-y", "-i", chemin_temp, "-c", "copy", "-movflags", "+faststart", cible], check=True)
            except Exception:
                try:
                    args = [
                        "ffmpeg", "-y",
                        "-i", chemin_temp,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                        "-c:a", "aac", "-b:a", "192k", cible
                    ]
                    if utiliser_intervalle:
                        args = ["ffmpeg", "-y", "-ss", str(debut), "-to", str(fin)] + args[2:]
                    subprocess.run(args, check=True)
                except Exception as e:
                    st.error(f"Echec du remux/transcodage local : {e}")
                    cible = None
        try:
            if os.path.exists(chemin_temp):
                os.remove(chemin_temp)
        except Exception:
            pass
        if cible:
            st.session_state['video_base'] = cible
            st.session_state['base_court'] = base_court
            st.success(f"Vidéo prête : {os.path.basename(cible)}")

    else:
        st.warning("Veuillez fournir une URL YouTube ou un fichier local.")

    # Extraction des ressources choisies
    if 'video_base' in st.session_state and os.path.exists(st.session_state['video_base']):
        # Si intervalle désactivé, on fixe fin = durée réelle (info)
        if not utiliser_intervalle:
            d = duree_video_seconds(st.session_state['video_base'])
            if d:
                fin = d
        options = {
            "mp4": opt_mp4,
            "mp3": opt_mp3,
            "wav": opt_wav,
            "img1": opt_img1,
            "img25": opt_img25
        }
        if any(options.values()):
            err2 = extraire_ressources(
                st.session_state['video_base'],
                debut, fin,
                st.session_state['base_court'],
                options,
                utiliser_intervalle
            )
            if err2:
                st.error(f"Erreur pendant l'extraction : {err2}")
            else:
                st.success("Ressources générées.")

# Aperçu unique après traitement (pour URL ou local converti)
apercu_placeholder2 = st.empty()
if 'video_base' in st.session_state and os.path.exists(st.session_state['video_base']):
    st.markdown("---")
    if afficher_apercu:
        size = taille_fichier(st.session_state['video_base']) or 0
        if size <= SEUIL_APERCU_OCTETS:
            with apercu_placeholder2:
                with open(st.session_state['video_base'], "rb") as f:
                    st.video(f.read(), format="video/mp4")

    # Téléchargement unique : ZIP de tout le run (y compris vidéo de base)
    prefix = st.session_state['base_court']
    fichiers_run = collecter_sorties_run(prefix)
    if st.session_state['video_base'] not in fichiers_run:
        fichiers_run.append(st.session_state['video_base'])
    buffer_zip, nom_zip = zipper_fichiers(fichiers_run, f"resultats_{prefix}")
    st.download_button("Télécharger", data=buffer_zip, file_name=nom_zip, mime="application/zip")

# Note explicite si URL sans traitement (pas d’aperçu possible avant d’avoir téléchargé)
if url and 'video_base' not in st.session_state:
    st.info("Aperçu indisponible pour une URL tant que le traitement n’a pas été lancé.")

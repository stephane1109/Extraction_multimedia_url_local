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
# Longueurs cibles très courtes pour les noms
LONGUEUR_TITRE_MAX = 24    # longueur max du titre "nettoyé" dans le nom
LONGUEUR_PREFIX_ID = 8     # longueur de l'ID vidéo utilisé dans le nom

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
    # Raccourcissement strict
    return titre[:LONGUEUR_TITRE_MAX]

def generer_nom_base(video_id, titre):
    # Construit un préfixe très court et stable: <id8>_<titreCourt>
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

def zipper_dossier(dossier, nom_zip_sans_ext="ressources"):
    # Crée un ZIP en mémoire avec tout le contenu d'un dossier
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(dossier):
            for f in files:
                abs_path = os.path.join(root, f)
                rel_path = os.path.relpath(abs_path, start=dossier)
                zf.write(abs_path, arcname=rel_path)
    buffer.seek(0)
    return buffer, f"{nom_zip_sans_ext}.zip"

def afficher_video_securisee(video_path):
    # Affichage SANS JAMAIS appeler st.video(path). On passe des bytes à st.video, ou on propose un téléchargement.
    if not os.path.exists(video_path):
        st.error(f"Fichier vidéo introuvable : {video_path}")
        return
    size = taille_fichier(video_path)
    if size is None:
        st.error("Impossible de déterminer la taille du fichier vidéo.")
        return
    if size > SEUIL_APERCU_OCTETS:
        st.info("La vidéo est volumineuse pour un aperçu direct. Utilisez le bouton « Télécharger » ci-dessous.")
        return
    try:
        with open(video_path, "rb") as f:
            data = f.read()
        st.video(data, format="video/mp4")
    except Exception as e:
        st.warning(f"Aperçu vidéo indisponible. Utilisez le bouton « Télécharger ». Détail : {e}")

def preparer_telechargement_unique(video_path, interval_dir, nom_zip="ressources_intervalle"):
    # Prépare la charge utile d’un SEUL bouton « Télécharger » :
    # - si des ressources existent dans ressources_intervalle : on propose un ZIP
    # - sinon on propose la vidéo compressée
    fichiers_intervalle = lister_fichiers([
        os.path.join(interval_dir, "*.mp4"),
        os.path.join(interval_dir, "*.mp3"),
        os.path.join(interval_dir, "*.wav"),
        os.path.join(interval_dir, "images_*", "*.jpg")
    ])
    if fichiers_intervalle:
        buffer_zip, nom_zip_f = zipper_dossier(interval_dir, nom_zip)
        return buffer_zip, nom_zip_f, "application/zip"
    # sinon, proposer la vidéo compressée
    if video_path and os.path.exists(video_path):
        try:
            with open(video_path, "rb") as f:
                data = f.read()
            return data, os.path.basename(video_path), "video/mp4"
        except Exception:
            pass
    # défaut: ZIP vide pour éviter de ne rien proposer
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        pass
    buffer.seek(0)
    return buffer, "vide.zip", "application/zip"

# ---------------- Téléchargement + compression ----------------

def telecharger_video(url, repertoire, cookies_path=None, verbose=False):
    # Téléchargement robuste via yt-dlp + compression MP4
    st.write("Téléchargement de la vidéo compressée en cours...")

    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0"
    http_headers = {
        'User-Agent': user_agent,
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Referer': 'https://www.youtube.com/'
    }

    formats_fallbacks = [
        "bv*[ext=mp4][height<=1080]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "bv*+ba/b",
        "b"
    ]

    base_opts = {
        'paths': {'home': repertoire},
        # Sortie initiale par ID pour éviter tout caractère exotique; on renomme ensuite court
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
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'mweb', 'web']
            }
        }
    }

    if cookies_path:
        base_opts['cookiefile'] = cookies_path

    if not ffmpeg_disponible():
        st.warning("ffmpeg n’est pas détecté. Sur Streamlit Cloud, ajoute un fichier packages.txt contenant la ligne: ffmpeg")

    derniere_erreur = None
    info = None
    fichier_final = None

    for fmt in formats_fallbacks:
        ydl_opts = base_opts.copy()
        ydl_opts['format'] = fmt
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                _ = ydl.prepare_filename(info)  # chemin attendu basé sur l’ID
            candidats = lister_fichiers([os.path.join(repertoire, f"*.{ext}") for ext in ['mp4', 'mkv', 'webm', 'm4a', 'mp3']])
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
            msg += " — HTTP 403 détecté. Fournis un cookies.txt (format Netscape) exporté de ton navigateur."
        return None, None, None, msg

    # Raccourci de nom: base très courte = <id8>_<titreCourt>
    video_id = (info.get('id') if info else "vid") or "vid"
    titre_brut = (info.get('title') if info else os.path.splitext(os.path.basename(fichier_final))[0]) or "video"
    base_court = generer_nom_base(video_id, titre_brut)

    # Déplacement du fichier source vers un nom propre intermédiaire
    ext_src = os.path.splitext(fichier_final)[1]
    src_base = os.path.join(repertoire, f"{base_court}_src")
    chemin_source_propre = renommer_sans_collision(fichier_final, src_base, ext=ext_src)

    # Cible finale compressée très courte
    compressed_base = os.path.join(repertoire, f"{base_court}_cmp")
    compressed_target = compressed_base + ".mp4"
    if os.path.exists(compressed_target):
        idx = 1
        while os.path.exists(f"{compressed_base}_{idx}.mp4"):
            idx += 1
        compressed_target = f"{compressed_base}_{idx}.mp4"

    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", chemin_source_propre,
            "-vf", "scale=1280:-2",
            "-c:v", "libx264",
            "-preset", "slow",
            "-crf", "28",
            "-c:a", "aac", "-b:a", "96k",
            compressed_target
        ], check=True)
    except Exception as e:
        return None, None, None, f"Echec de la compression avec ffmpeg : {e}"
    finally:
        try:
            if os.path.exists(chemin_source_propre):
                os.remove(chemin_source_propre)
        except Exception:
            pass

    # On renvoie aussi un titre court à réutiliser pour nommer les extraits
    return compressed_target, base_court, info, None

# ---------------- Extraction des ressources ----------------

def extraire_ressources(video_path, repertoire, debut, fin, base_court, options):
    # Extraction de segments et d’images selon les options choisies, avec noms très courts
    try:
        if options.get("mp4"):
            extrait_video_path = os.path.join(repertoire, f"{base_court}_seg.mp4")
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path,
                "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                "-c:a", "aac", "-b:a", "96k",
                extrait_video_path
            ], check=True)

        if options.get("mp3"):
            extrait_mp3_path = os.path.join(repertoire, f"{base_court}_seg.mp3")
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path,
                "-vn", "-acodec", "libmp3lame", "-q:a", "5", extrait_mp3_path
            ], check=True)

        if options.get("wav"):
            extrait_wav_path = os.path.join(repertoire, f"{base_court}_seg.wav")
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path,
                "-vn", "-acodec", "adpcm_ima_wav", extrait_wav_path
            ], check=True)

        if options.get("img1") or options.get("img25"):
            for fps in [1, 25]:
                if (fps == 1 and options.get("img1")) or (fps == 25 and options.get("img25")):
                    images_repertoire = os.path.join(repertoire, f"img{fps}_{base_court}")
                    os.makedirs(images_repertoire, exist_ok=True)
                    output_pattern = os.path.join(images_repertoire, "i_%04d.jpg")
                    subprocess.run([
                        "ffmpeg", "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path,
                        "-vf", f"fps={fps},scale=1920:1080", "-q:v", "1", output_pattern
                    ], check=True)

        return None

    except Exception as e:
        return str(e)

# ---------------- Interface utilisateur ----------------

st.title("Extraction multimédia compressée (mp4 - mp3 - wav - images)")
st.markdown("**[www.codeandcortex.fr](http://www.codeandcortex.fr)**")

vider_cache()

st.markdown("""
Entrez une URL YouTube ou importez un fichier mp4. La vidéo est compressée (1280px, CRF 28, AAC 96kbps) et enregistrée dans **ressources_globale**. 
Vous pouvez ensuite définir un intervalle d'extraction et choisir les ressources à extraire dans **ressources_intervalle**.
""")

# URL puis cookies juste en dessous
url = st.text_input("Entrez l'URL de la vidéo YouTube :")
cookies_file = st.file_uploader("Uploader votre fichier cookies.txt (optionnel)", type=["txt"])
fichier_local = st.file_uploader("Ou importez un fichier vidéo (.mp4)", type=["mp4"])
mode_verbose = st.checkbox("Mode verbose yt-dlp (diagnostic)")

repertoire_globale = os.path.abspath("ressources_globale")
repertoire_intervalle = os.path.abspath("ressources_intervalle")
os.makedirs(repertoire_globale, exist_ok=True)
os.makedirs(repertoire_intervalle, exist_ok=True)

# Bouton principal de téléchargement
if st.button("Lancer le téléchargement"):
    if url:
        cookies_path = None
        if cookies_file:
            cookies_path = os.path.join(repertoire_globale, "cookies.txt")
            with open(cookies_path, "wb") as f:
                f.write(cookies_file.read())

        video_path, base_court, info, erreur = telecharger_video(url, repertoire_globale, cookies_path, verbose=mode_verbose)

        if erreur:
            st.error(f"Erreur : {erreur}")
        else:
            st.session_state['video_path'] = video_path
            st.session_state['base_court'] = base_court
            st.success(f"Vidéo compressée enregistrée : {os.path.basename(video_path)}")
    elif fichier_local:
        # Compression locale avec nom très court
        titre_net = nettoyer_titre(os.path.splitext(fichier_local.name)[0])
        base_court = generer_nom_base("local", titre_net)
        original_path = os.path.join(repertoire_globale, f"{base_court}_src.mp4")
        compressed_path = os.path.join(repertoire_globale, f"{base_court}_cmp.mp4")

        with open(original_path, "wb") as f:
            f.write(fichier_local.read())

        try:
            subprocess.run([
                "ffmpeg", "-y", "-i", original_path,
                "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                "-c:a", "aac", "-b:a", "96k", compressed_path
            ], check=True)
            try:
                os.remove(original_path)
            except Exception:
                pass
            st.session_state['video_path'] = compressed_path
            st.session_state['base_court'] = base_court
            st.success(f"Vidéo locale compressée : {os.path.basename(compressed_path)}")
        except Exception as e:
            st.error(f"Echec de la compression locale : {e}")
    else:
        st.warning("Veuillez fournir une URL YouTube ou un fichier local.")

# Affichage, extraction et téléchargement unique si vidéo présente
if 'video_path' in st.session_state and os.path.exists(st.session_state['video_path']):
    st.markdown("---")
    afficher_video_securisee(st.session_state['video_path'])

    st.subheader("Paramètres d'extraction (ressources_intervalle)")

    col1, col2 = st.columns(2)
    debut = col1.number_input("Début (en secondes)", min_value=0, value=0)
    fin = col2.number_input("Fin (en secondes)", min_value=1, value=10)

    st.markdown("Choisissez les ressources à extraire")
    opt_mp4 = st.checkbox("Vidéo MP4")
    opt_mp3 = st.checkbox("Audio MP3")
    opt_wav = st.checkbox("Audio WAV")
    opt_img1 = st.checkbox("Images 1 FPS")
    opt_img25 = st.checkbox("Images 25 FPS")

    if st.button("Extraire les ressources"):
        options = {
            "mp4": opt_mp4,
            "mp3": opt_mp3,
            "wav": opt_wav,
            "img1": opt_img1,
            "img25": opt_img25
        }
        erreur = extraire_ressources(
            st.session_state['video_path'],
            repertoire_intervalle,
            debut,
            fin,
            st.session_state['base_court'],
            options
        )
        if erreur:
            st.error(f"Erreur pendant l'extraction : {erreur}")
        else:
            st.success("Ressources extraites dans le répertoire ressources_intervalle.")

    st.markdown("---")
    # Un SEUL bouton « Télécharger » :
    # - Si des ressources existent, il télécharge le ZIP des ressources_intervalle
    # - Sinon, il télécharge la vidéo compressée
    data_dl, nom_dl, mime_dl = preparer_telechargement_unique(
        st.session_state['video_path'],
        repertoire_intervalle,
        nom_zip="ressources_intervalle"
    )
    st.download_button("Télécharger", data=data_dl, file_name=nom_dl, mime=mime_dl)

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
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# ---------------- Constantes ----------------
# Seuil d’aperçu : au-delà, on évite de charger le MP4 en mémoire et on propose un téléchargement
SEUIL_APERCU_OCTETS = 160 * 1024 * 1024  # ~160 Mo

# ---------------- Fonctions utilitaires ----------------

def vider_cache():
    # Nettoyage explicite du cache Streamlit
    st.cache_data.clear()

def nettoyer_titre(titre):
    # Normalisation robuste du titre vers un nom de fichier ASCII sûr
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
    return titre[:80] if titre else "video"

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

def afficher_video_securisee(video_path):
    # Affichage sans jamais appeler st.video(path)
    if not os.path.exists(video_path):
        st.error(f"Fichier vidéo introuvable : {video_path}")
        return
    size = taille_fichier(video_path)
    if size is None:
        st.error("Impossible de déterminer la taille du fichier vidéo.")
        return
    if size > SEUIL_APERCU_OCTETS:
        st.info("La vidéo est volumineuse pour un aperçu direct. Téléchargez-la ci-dessous.")
        with open(video_path, "rb") as f:
            st.download_button("Télécharger la vidéo compressée (MP4)", data=f, file_name=os.path.basename(video_path), mime="video/mp4")
        return
    try:
        with open(video_path, "rb") as f:
            data = f.read()
        st.video(data, format="video/mp4")
    except Exception as e:
        st.warning(f"Aperçu vidéo indisponible. Vous pouvez la télécharger. Détail : {e}")
        try:
            with open(video_path, "rb") as f:
                st.download_button("Télécharger la vidéo compressée (MP4)", data=f, file_name=os.path.basename(video_path), mime="video/mp4")
        except Exception:
            st.error("Téléchargement impossible. Vérifiez l’existence et les droits sur le fichier.")

# ---------------- Téléchargement + compression ----------------

def telecharger_video(url, repertoire, cookies_path=None):
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
        'outtmpl': {'default': '%(id)s.%(ext)s'},  # Sortie initiale par ID pour éviter tout caractère exotique
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'merge_output_format': 'mp4',
        'retries': 10,
        'fragment_retries': 10,
        'continuedl': True,
        'concurrent_fragment_downloads': 1,
        'http_headers': http_headers,
        'geo_bypass': True,
        'nocheckcertificate': True,
        'restrictfilenames': True,
        'trim_file_name': 200,
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
            candidats = []
            for ext in ['mp4', 'mkv', 'webm', 'm4a', 'mp3']:
                candidats += glob.glob(os.path.join(repertoire, f"*.{ext}"))
            if not candidats:
                raise DownloadError("Téléchargement terminé mais aucun fichier détecté (download is empty).")
            candidats.sort(key=os.path.getmtime, reverse=True)
            fichier_final = candidats[0]
            break
        except Exception as e:
            derniere_erreur = e
            continue

    if fichier_final is None:
        msg = str(derniere_erreur) if derniere_erreur else "Echec inconnu."
        if "403" in msg or "Forbidden" in msg:
            msg += " — HTTP 403 détecté. Fournis un cookies.txt (format Netscape) exporté de ton navigateur."
        return None, None, msg

    titre_brut = (info.get('title') if info else os.path.splitext(os.path.basename(fichier_final))[0]) or "video"
    titre_net = nettoyer_titre(titre_brut)

    # Déplacement du fichier source vers un nom propre et compression MP4 finale
    ext_src = os.path.splitext(fichier_final)[1]
    src_base = os.path.join(repertoire, f"{titre_net}_source_tmp")
    chemin_source_propre = renommer_sans_collision(fichier_final, src_base, ext=ext_src)

    compressed_base = os.path.join(repertoire, f"{titre_net}_compressed")
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
        return None, None, f"Echec de la compression avec ffmpeg : {e}"
    finally:
        try:
            if os.path.exists(chemin_source_propre):
                os.remove(chemin_source_propre)
        except Exception:
            pass

    return compressed_target, titre_net, None

# ---------------- Extraction des ressources ----------------

def extraire_ressources(video_path, repertoire, debut, fin, video_title, options):
    # Extraction de segments et d’images selon les options choisies, avec noms sûrs
    try:
        titre_net = nettoyer_titre(video_title)

        if options.get("mp4"):
            extrait_video_path = os.path.join(repertoire, f"{titre_net}_extrait.mp4")
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path,
                "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
                "-c:a", "aac", "-b:a", "96k",
                extrait_video_path
            ], check=True)

        if options.get("mp3"):
            extrait_mp3_path = os.path.join(repertoire, f"{titre_net}_extrait.mp3")
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path,
                "-vn", "-acodec", "libmp3lame", "-q:a", "5", extrait_mp3_path
            ], check=True)

        if options.get("wav"):
            extrait_wav_path = os.path.join(repertoire, f"{titre_net}_extrait.wav")
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(debut), "-to", str(fin), "-i", video_path,
                "-vn", "-acodec", "adpcm_ima_wav", extrait_wav_path
            ], check=True)

        if options.get("img1") or options.get("img25"):
            for fps in [1, 25]:
                if (fps == 1 and options.get("img1")) or (fps == 25 and options.get("img25")):
                    images_repertoire = os.path.join(repertoire, f"images_{fps}fps_{titre_net}")
                    os.makedirs(images_repertoire, exist_ok=True)
                    output_pattern = os.path.join(images_repertoire, "image_%04d.jpg")
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
➡ Entrez une URL YouTube **ou** importez un fichier mp4.  
➡ La vidéo est compressée (1280px, CRF 28, AAC 96kbps) et enregistrée dans **ressources_globale**.  
➡ Vous pouvez ensuite définir un intervalle d'extraction et choisir les ressources à extraire dans **ressources_intervalle**.
""")

url = st.text_input("Entrez l'URL de la vidéo YouTube :")
fichier_local = st.file_uploader("Ou importez un fichier vidéo (.mp4)", type=["mp4"])
cookies_file = st.file_uploader("Uploader votre fichier cookies.txt (optionnel)", type=["txt"])

repertoire_globale = os.path.abspath("ressources_globale")
repertoire_intervalle = os.path.abspath("ressources_intervalle")
os.makedirs(repertoire_globale, exist_ok=True)
os.makedirs(repertoire_intervalle, exist_ok=True)

if st.button("Lancer le téléchargement"):
    if url:
        cookies_path = None
        if cookies_file:
            cookies_path = os.path.join(repertoire_globale, "cookies.txt")
            with open(cookies_path, "wb") as f:
                f.write(cookies_file.read())

        video_path, video_title, erreur = telecharger_video(url, repertoire_globale, cookies_path)

        if erreur:
            st.error(f"Erreur : {erreur}")
        else:
            st.session_state['video_path'] = video_path
            st.session_state['video_title'] = video_title
            st.success("Vidéo compressée téléchargée avec succès dans ressources_globale.")

    elif fichier_local:
        titre_net = nettoyer_titre(os.path.splitext(fichier_local.name)[0])
        original_path = os.path.join(repertoire_globale, f"{titre_net}_original.mp4")
        compressed_path = os.path.join(repertoire_globale, f"{titre_net}_compressed.mp4")

        with open(original_path, "wb") as f:
            f.write(fichier_local.read())

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
        st.session_state['video_title'] = titre_net
        st.success("Vidéo locale compressée avec succès dans ressources_globale.")
    else:
        st.warning("Veuillez fournir une URL YouTube ou un fichier local.")

# Extraction si vidéo présente
if 'video_path' in st.session_state and os.path.exists(st.session_state['video_path']):
    st.markdown("---")
    # Affichage vidéo sécurisé : jamais st.video(path)
    afficher_video_securisee(st.session_state['video_path'])

    st.subheader("Paramètres d'extraction (ressources_intervalle)")

    col1, col2 = st.columns(2)
    debut = col1.number_input("Début (en secondes)", min_value=0, value=0)
    fin = col2.number_input("Fin (en secondes)", min_value=1, value=10)

    st.markdown("**Choisissez les ressources à extraire :**")
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
            st.session_state['video_title'],
            options
        )
        if erreur:
            st.error(f"Erreur pendant l'extraction : {erreur}")
        else:
            st.success("Ressources extraites avec succès dans le répertoire ressources_intervalle.")

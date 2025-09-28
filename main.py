# pip install streamlit yt_dlp

# pip install streamlit yt_dlp

# ---------------- Imports ----------------
import os
# Désactivation du watcher de fichiers pour éviter "inotify instance limit reached" sur Streamlit Cloud
os.environ["STREAMLIT_SERVER_FILE_WATCHER_TYPE"] = "none"

import streamlit as st
import subprocess
import re
import glob
import unicodedata
import shutil
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# ---------------- Fonctions utilitaires ----------------

# Nettoyage du cache Streamlit
def vider_cache():
    # On vide explicitement le cache data
    st.cache_data.clear()

# Normalisation ASCII sûre du titre pour nom de fichier
def nettoyer_titre(titre):
    # 1) Normalisation Unicode puis translittération ASCII
    if titre is None:
        titre = "video"
    titre = titre.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    # Remplacer guillemets/apostrophes typographiques et caractères problématiques fréquents
    remplacement = {
        '«': '', '»': '', '“': '', '”': '', '’': '', '‘': '', '„': '',
        '"': '', "'": '', ':': '-', '/': '-', '\\': '-', '|': '-',
        '?': '', '*': '', '<': '', '>': '', '\u00A0': ' '
    }
    for k, v in remplacement.items():
        titre = titre.replace(k, v)
    # Décomposition et suppression des diacritiques
    titre = unicodedata.normalize('NFKD', titre)
    titre = ''.join(c for c in titre if not unicodedata.combining(c))
    # Garde lettres/chiffres/espaces/traits/underscores
    titre = re.sub(r'[^\w\s-]', '', titre, flags=re.UNICODE)
    # Réduction des espaces -> underscore
    titre = re.sub(r'\s+', '_', titre.strip())
    # Troncature raisonnable
    return titre[:80] if titre else "video"

# Vérification de la présence de ffmpeg dans l'environnement
def ffmpeg_disponible():
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False

# Renommer un fichier de manière atomique vers un nom cible
def renommer_sans_collision(src_path, dest_path_base, ext=".mp4"):
    # Si le nom existe déjà, on suffixe avec _1, _2, ...
    candidat = dest_path_base + ext
    i = 1
    while os.path.exists(candidat):
        candidat = f"{dest_path_base}_{i}{ext}"
        i += 1
    shutil.move(src_path, candidat)
    return candidat

# ---------------- Téléchargement + compression ----------------

def telecharger_video(url, repertoire, cookies_path=None):
    # Télécharge la vidéo avec des formats fallback et noms sûrs, puis compresse en MP4.
    st.write("Téléchargement de la vidéo compressée en cours...")

    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0"
    http_headers = {
        'User-Agent': user_agent,
        'Accept': '*/*',
        'Accept-Language': 'en-US,en;q=0.5',
        'Referer': 'https://www.youtube.com/'
    }

    # Fallbacks de formats (on reste raisonnable ≤1080p d’abord)
    formats_fallbacks = [
        "bv*[ext=mp4][height<=1080]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "bv*+ba/b",
        "b"
    ]

    # Pour éviter tout souci de caractères dans les fichiers intermédiaires, on sort d’abord sur l’ID.
    base_opts = {
        'paths': {'home': repertoire},
        'outtmpl': {'default': '%(id)s.%(ext)s'},
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
        'restrictfilenames': True,     # Noms sûrs ASCII
        'trim_file_name': 200,         # Limite la longueur
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
                # Chemin attendu (basé sur l’ID donc sûr)
                fichier_attendu = ydl.prepare_filename(info)
            # Vérifier la présence d’un fichier téléchargé
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

    # Calcul du titre propre pour la destination finale
    titre_brut = (info.get('title') if info else os.path.splitext(os.path.basename(fichier_final))[0]) or "video"
    titre_net = nettoyer_titre(titre_brut)

    # Compression en MP4 avec nom final propre
    chemin_compress_base = os.path.join(repertoire, f"{titre_net}_compressed")
    chemin_compress = renommer_sans_collision(fichier_final, os.path.join(repertoire, f"{titre_net}_source_tmp"), ext=os.path.splitext(fichier_final)[1])

    compressed_target = chemin_compress_base + ".mp4"
    # Si le nom existe déjà, on le décale
    if os.path.exists(compressed_target):
        idx = 1
        while os.path.exists(f"{chemin_compress_base}_{idx}.mp4"):
            idx += 1
        compressed_target = f"{chemin_compress_base}_{idx}.mp4"

    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", chemin_compress,
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
        # On peut supprimer le fichier intermédiaire renommé si ce n’est pas le même
        try:
            if os.path.exists(chemin_compress):
                os.remove(chemin_compress)
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
        # On applique la même normalisation au titre local
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

        # On peut supprimer l’original local (optionnel)
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
    st.video(st.session_state['video_path'])
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

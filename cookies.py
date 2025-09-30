# cookies_persist.py
# Gestion centralisée des cookies YouTube pour yt-dlp :
# - mémorisation persistante dans /tmp/appdata/fichiers/cookies.txt
# - réutilisation automatique si présent
# - remplacement forcé sur demande
# - fonctions utilitaires et petit bloc UI prêt à l’emploi

import os
from pathlib import Path
from datetime import datetime
import streamlit as st

# ---------------- Fonctions bas niveau (sans UI) ----------------

def chemin_cookies_persistant(repertoire_sortie: Path) -> Path:
    """
    Retourne le chemin canonique du cookies.txt mémorisé côté serveur.
    """
    return repertoire_sortie / "cookies.txt"

def cookies_disponibles(repertoire_sortie: Path) -> bool:
    """
    Indique si un cookies.txt mémorisé est disponible et non vide.
    """
    p = chemin_cookies_persistant(repertoire_sortie)
    return p.exists() and p.stat().st_size > 0

def info_cookies(repertoire_sortie: Path) -> str:
    """
    Renvoie une chaîne d'information sur l'état du cookies mémorisé.
    """
    p = chemin_cookies_persistant(repertoire_sortie)
    if not p.exists():
        return "Aucun cookies mémorisé."
    ts = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return f"cookies.txt présent ({p.stat().st_size} octets) — mis à jour le {ts}"

def memoriser_cookies_depuis_upload(fichier_streamlit, repertoire_sortie: Path, forcer: bool) -> tuple[Path | None, str]:
    """
    Mémorise un cookies.txt envoyé via un uploader Streamlit.
    - fichier_streamlit : objet retourné par st.file_uploader (ou None)
    - forcer            : True pour écraser l'existant
    Renvoie (chemin_effectif_ou_None, message_textuel).
    """
    if fichier_streamlit is None:
        # Aucun nouveau cookies fourni : ne rien changer
        if cookies_disponibles(repertoire_sortie):
            return chemin_cookies_persistant(repertoire_sortie), "Réutilisation du cookies.txt mémorisé."
        return None, "Aucun cookies fourni et aucun cookies mémorisé."

    dest = chemin_cookies_persistant(repertoire_sortie)
    if dest.exists() and not forcer:
        return dest, "Un cookies.txt est déjà mémorisé. Cochez « Forcer le remplacement » pour le remplacer."

    try:
        data = fichier_streamlit.read()
        dest.write_bytes(data)
        return dest, "cookies.txt mémorisé sur le serveur."
    except Exception as e:
        return None, f"Echec de la mémorisation du cookies.txt : {e}"

def chemin_cookies_a_utiliser(repertoire_sortie: Path, fichier_streamlit, forcer: bool) -> tuple[Path | None, str]:
    """
    Détermine le cookies.txt à utiliser :
    - si un nouveau est fourni et forcer=True, le remplace
    - sinon réutilise l’existant s’il est présent
    - sinon None
    Renvoie (chemin_ou_None, message_textuel).
    """
    if fichier_streamlit is not None:
        return memoriser_cookies_depuis_upload(fichier_streamlit, repertoire_sortie, forcer)
    if cookies_disponibles(repertoire_sortie):
        return chemin_cookies_persistant(repertoire_sortie), "Réutilisation du cookies.txt mémorisé."
    return None, "Aucun cookies disponible."

# ---------------- Bloc UI prêt à l’emploi ----------------

def afficher_section_cookies(repertoire_sortie: Path) -> Path | None:
    """
    Affiche un petit panneau de gestion des cookies :
    - uploader cookies.txt (optionnel)
    - case « Forcer le remplacement »
    - affiche l’état du cookies mémorisé
    Retourne le chemin du cookies.txt à utiliser (ou None).
    """
    st.markdown("#### Cookies YouTube (optionnel)")
    col1, col2 = st.columns([3, 2])
    with col1:
        cookies_file = st.file_uploader("Fichier cookies.txt", type=["txt"], key="cookies_file")
    with col2:
        forcer = st.checkbox("Forcer le remplacement", value=False, key="forcer_remplacement_cookies")

    # Informations sur l’état actuel
    st.caption(info_cookies(repertoire_sortie))

    # Calcul du cookies à utiliser
    cookies_path, message = chemin_cookies_a_utiliser(repertoire_sortie, cookies_file, forcer)

    # Feedback utilisateur (non intrusif)
    if "mémorisé" in message:
        st.success(message)
    elif "réutilisation" in message.lower():
        st.info(message)
    elif "aucun cookies" in message.lower():
        st.info(message)
    else:
        st.warning(message)

    return cookies_path

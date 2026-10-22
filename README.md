# JBS Studio — lecteur de scènes pour spectacles

Lecteur de scènes simple pour spectacles et événements scolaires : vidéo et audio sur un second écran, scènes numérotées, GO à la barre d'espace, API HTTP.

Python · PyQt5 · python-vlc · Windows / Linux

---

# Partie 1 — Utilisation (concepts)

## 1.1 Le principe

Un **show** est une liste ordonnée de **41 scènes numérotées de 0 à 40**. On prépare les scènes à l'avance, puis on les déclenche une à une pendant le spectacle (clavier, souris ou API HTTP).

Deux fenêtres :
- **Fenêtre de contrôle** : ce que voit le régisseur (aperçu, liste, réglages).
- **Fenêtre de sortie** : ce que voit le public, destinée au second écran. Si un seul écran est détecté, elle s'ouvre en mode fenêtré.

Une troisième fenêtre, optionnelle, reproduit le **Streamdeck (boitier)** (voir 1.5). Les fenêtres restent **toujours au premier plan**.

## 1.2 La scène

Chaque scène contient :

| Élément | Rôle |
|---|---|
| Média principal | Vidéo ou audio. C'est ce qui sort à l'écran public. |
| Audio secondaire | Optionnel (musique de fond, voix off). Joue **en même temps** que le média principal. |
| Nom | Étiquette optionnelle affichée dans la liste. |
| Mute (×2) | Indépendant pour chaque piste. |
| Volume (×2) | 0–100 %, indépendant pour chaque piste, modifiable en direct. |
| Enchaînement auto | À la fin du média principal, la scène suivante démarre seule. |

La **scène 0 est réservée** : elle ne peut recevoir aucun fichier. Elle sert de scène « noir / attente ».

Le numéro est attaché à la scène, mais l'**ordre de lecture est l'ordre d'affichage** dans la liste : réordonner une ligne change l'ordre dans lequel GO parcourt les scènes.

## 1.3 Naviguer

- **GO** (espace) joue la scène suivante, **Précédente** (retour arrière) la précédente.
- La navigation avance scène par scène **sans sauter les scènes vides** : sur une scène vide la lecture s'arrête (écran noir), mais la position avance.
- Clic simple = sélectionner. Double-clic ou Entrée = jouer la scène sélectionnée.
- La scène en cours est en **vert** ; la sélection est entourée de bleu.

## 1.4 Préparer un show

1. Sélectionner une scène.
2. Glisser un fichier sur sa ligne (média principal), ou sur l'une des deux zones de dépôt du panneau de détails (principal / secondaire). Plusieurs fichiers déposés d'un coup remplissent les lignes suivantes.
3. Régler nom, mutes, volumes, enchaînement.
4. Réordonner par glisser-déposer si nécessaire. Copier/coller pour dupliquer les réglages d'une scène.
5. Enregistrer (bouton Enregistrer ou Ctrl+S) dans un fichier `.json`.

## 1.5 Barre d'outils et Streamdeck (boitier)

- **Verrouillage** : verrouille le spectacle (blocage de l'édition ; détail ci-dessous). Rouge = verrouillé.
- **Enregistrer** : enregistre le show (= Ctrl+S ; demande un nom au premier enregistrement).
- **Sortie** : active / désactive l'écran de sortie (= menu « Ecran de sortie > Activer »). Vert = actif, gris = désactivé.
- **Plein écran** : plein écran de la sortie on/off (= touche F). Grisé si la sortie est désactivée.
- **Streamdeck (boitier)** : affiche / masque la fenêtre du boitier (voir plus bas).

Les fenêtres de contrôle, de sortie et du boitier restent **toujours au premier plan** : aucune autre fenêtre ne passe devant.

**Verrouillage** : tout ce qui modifie le contenu est bloqué (dépôts, réordonnancement, nom, assignation, copier/coller, enchaînement auto, nouveau/ouvrir). Les commandes de jeu restent actives (GO, mute, volumes, stop, API, Streamdeck). Le verrou n'est pas enregistré : l'app démarre déverrouillée.

**Streamdeck (boitier)**

Fenêtre optionnelle (bouton **Streamdeck (boitier)** ou menu du même nom) qui reproduit la **page 10 du Stream Deck XL** du pupitre de spectacle (8 colonnes × 4 lignes). Elle est redimensionnable : les touches et le texte s'adaptent.

| Zone | Touches | Action |
|---|---|---|
| Colonne de gauche | UP / PAGE / DOWN | Change de page de scènes (1–21, puis 22–40) ; la page suit la scène en cours |
| 3 premières lignes | 1 à 21 | Lance la scène par son **numéro** (comme `/scene/<n>`). Numéro seul affiché, jamais le nom. Vert = en cours, grisé = scène vide |
| Dernière ligne | Noir Silence | Stop (image noire, silence) |
| | Plein Écran | Plein écran de la sortie |
| | ◀ Préc. / GO | Scène précédente / suivante ; GO affiche le numéro en cours (orange si la scène est vide) |

Les touches **PF, Scène Off, Salle on/off, Salle 20 %** (lumières, pilotées par Home Assistant via Companion) sont une **option désactivée par défaut** : les quatre cases restent vides. Pour les activer, mettre `COMPANION_ENABLED = True` dans `media_scene_player.py` ou lancer avec `JBS_COMPANION=1`. Un menu « Adresse de Companion… » apparaît alors (défaut `10.37.4.10:8000`).

Le Streamdeck (boitier) reste utilisable quand le spectacle est verrouillé.

## 1.6 Raccourcis clavier

| Raccourci | Action |
|---|---|
| Espace | Scène suivante (GO) |
| Retour arrière | Scène précédente |
| Échap | Stop |
| ↑ / ↓ | Naviguer dans la liste |
| Entrée | Jouer la scène sélectionnée |
| Ctrl+↑ / Ctrl+↓ | Volume du média principal ±5 % |
| Maj+↑ / Maj+↓ | Volume de l'audio secondaire ±5 % |
| Ctrl+M | Mute du média principal |
| Ctrl+Maj+M | Mute de l'audio secondaire |
| Ctrl+S / Ctrl+N / Ctrl+O | Enregistrer / Nouveau / Ouvrir |
| Ctrl+Maj+C / Ctrl+Maj+V | Copier / coller une scène |
| F | Plein écran de la sortie |

## 1.7 Pilotage à distance (API HTTP)

Serveur intégré sur le port **8080**, accessible depuis le réseau local. **Aucune authentification** : toute personne du même réseau peut piloter le show.

| Route | Effet |
|---|---|
| `GET /scene/<n>` | Joue la scène numéro `n` |
| `GET /next` · `/previous` · `/stop` | GO / précédente / stop |
| `GET /status` | Statut JSON |
| `GET /mute/primary/on\|off` | Mute du média principal en cours |
| `GET /mute/secondary/on\|off` | Mute de l'audio secondaire en cours |
| `GET /volume/primary/<0-100>` | Volume du média principal en cours |
| `GET /volume/secondary/<0-100>` | Volume de l'audio secondaire en cours |

```
curl http://192.168.1.50:8080/scene/12
curl http://192.168.1.50:8080/status
```

Utilisable depuis un Stream Deck, Bitfocus Companion, Home Assistant, un pupitre tactile…

---

# Partie 2 — Installation

## Prérequis
- **Python ≥ 3.8** (PyQt5 n'a plus de wheels précompilés en dessous).
- **VLC Media Player** installé sur le système, de **même architecture que Python** (64 bits avec Python 64 bits). `python-vlc` n'est qu'une interface vers `libvlc`.

## Linux (Debian/Ubuntu)
```bash
sudo apt install vlc
python3 -m venv venv
source venv/bin/activate
pip install python-vlc PyQt5
python media_scene_player.py
```
Debian récent refuse `pip install` hors venv (PEP 668) : utilise le venv. Alternative : `sudo apt install python3-pyqt5` puis un venv créé avec `--system-site-packages`.

**Wayland** : l'app force Qt en mode X11 (XWayland) au lancement, car VLC s'intègre via un identifiant de fenêtre X11. Si le démarrage échoue avec une erreur de plugin `xcb` : `sudo apt install libxcb-cursor0 libxcb-xinerama0`.

## Windows
1. Installer Python ≥ 3.8 et VLC.
2. Dans un terminal :
   ```
   python -m venv venv
   venv\Scripts\activate
   pip install python-vlc PyQt5
   python media_scene_player.py
   ```

## Compilation en exécutable Windows (optionnel)
`build_exe.bat` (PyInstaller, mode `--onedir`) embarque `libvlc.dll`, `libvlccore.dll` et le dossier `plugins` de VLC, car `python-vlc` charge la bibliothèque dynamiquement et PyInstaller ne la détecte pas seul.
```
build_exe.bat
```
À lancer depuis le venv, sur une machine Windows (pas de compilation croisée). Le résultat est dans `dist\MediaScenePlayer\` : distribuer **tout le dossier**. Ajuste `VLC_DIR` en haut du script si VLC est installé ailleurs.

## Dépannage

| Symptôme | Piste |
|---|---|
| `No module named 'vlc'` | `python-vlc` absent de l'interpréteur utilisé (venv non activé) |
| `Failed building wheel for PyQt5-sip` | Python < 3.8 |
| `externally-managed-environment` | Utiliser un venv |
| libvlc introuvable | VLC absent, ou architecture différente de Python |
| Son avec de la réverb | Version trop ancienne : l'aperçu doit être lancé sans audio (corrigé) |
| Interface ou aperçu qui ne se met pas à jour (Linux) | Wayland : voir plus haut |
| Coupure au début d'une scène | Disque lent / codec lourd, voir « Cache » en partie 3 |
| `.exe` plante sans VLC | DLL et dossier `plugins` doivent être à côté de l'exécutable |

---

# Partie 3 — Programmation

## 3.1 Architecture

Un seul fichier, `media_scene_player.py`.

| Élément | Rôle |
|---|---|
| `ControlWindow(QMainWindow)` | Fenêtre de contrôle : UI, logique de lecture, fichiers de show, serveur API |
| `OutputWindow(QWidget)` | Fenêtre de sortie (fond noir, `video_frame` dans lequel VLC dessine) |
| `PlaylistWidget(QListWidget)` | Liste des scènes : réordonnancement interne (`InternalMove`) + dépôt de fichiers externes |
| `SceneItemDelegate` | Rendu : vert pour la scène en cours, cadre bleu pour la sélection |
| `DropZoneLabel(QLabel)` | Zone de dépôt de fichier (signal `fileDropped`) |
| `DeckWindow(QWidget)` | Fenêtre « Streamdeck (boitier) » : copie de la page 10 d'un Stream Deck XL |
| `Bridge(QObject)` | Pont thread-safe API HTTP → Qt (signaux) |
| `make_http_handler(bridge)` | Fabrique du `BaseHTTPRequestHandler` |

## 3.2 Modèle de données

Il n'y a pas de modèle séparé : **chaque scène est un `QListWidgetItem`**, et ses données sont stockées dans des rôles utilisateur (`Qt.UserRole + n`) :

| Rôle | Contenu |
|---|---|
| `NUMBER_ROLE` | Numéro (str, `"0"`..`"40"`) |
| `PATH_ROLE` / `AUDIO_PATH_ROLE` | Chemins des médias principal / secondaire |
| `NAME_ROLE` | Nom |
| `MUTE_PRIMARY_ROLE` / `MUTE_SECONDARY_ROLE` | bool |
| `VOLUME_PRIMARY_ROLE` / `VOLUME_SECONDARY_ROLE` | int 0–100 |
| `AUTO_NEXT_ROLE` | bool, enchaînement automatique |
| `IS_PLAYING_ROLE` | bool, lu par le delegate pour le fond vert |

L'ordre de lecture est l'ordre des lignes de la liste ; `current_index` est un **index de ligne**, pas un numéro de scène. `goto_scene_number(n)` (utilisé par l'API) fait la correspondance numéro → ligne.

Le texte affiché est produit par `scene_label(number, path, audio_path, name)`.

## 3.3 Lecture : trois lecteurs VLC

Une seule `vlc.Instance`, trois `MediaPlayer` :

| Lecteur | Rôle | Sortie |
|---|---|---|
| `preview_player` | Média principal, **sans audio** | `preview_frame` (fenêtre de contrôle) |
| `program_player` | Média principal | `output_window.video_frame` (public) |
| `program_audio_player` | Audio secondaire (`:no-video`) | Audio uniquement |

`play_scene(index)` :
1. Crée les `Media`, avec l'option `:file-caching=800`.
2. Aperçu avec `:no-audio` : appeler `audio_set_mute()` juste après `play()` est ignoré sur certaines machines (le son sortait deux fois, d'où une réverb).
3. Lance les lecteurs, programme des appels `_apply_audio_state` à +300 ms et +1000 ms pour (ré)appliquer mute/volume quand l'audio VLC est réellement démarré.
4. Met à jour `current_index`, la sélection, le surlignage, lance le préchargement de la scène suivante et le contrôle d'écran noir.

**Rattachement de la vidéo** (`attach_video_outputs`) selon l'OS : `set_hwnd` (Windows), `set_xwindow` (Linux/X11), `set_nsobject` (macOS, non testé). Les widgets de destination ont donc besoin d'un `winId()` natif.

**Enchaînement automatique** : `MediaPlayerEndReached` est émis depuis un thread VLC. Le callback ne fait qu'émettre le signal Qt `media_end_reached`, traité dans le thread principal par `_on_media_end_reached` → `go_next()` si `AUTO_NEXT_ROLE`.

**Écran noir** : VLC laisse la dernière image. `_schedule_blank_check` repeint les frames en noir quand la scène n'a pas de média principal, ou quand `has_vout() == 0` (média audio seul).

**Cache** : `file-caching` à 800 ms, plus `_prewarm_next_scene` qui lit en thread les 8 premiers Mo des fichiers de la scène N+1 pour les monter dans le cache disque du système. Ne sert que si la coupure vient d'un accès disque lent.

## 3.4 Interface

Une seule vue : barre d'outils (Verrouillage, Enregistrer, Sortie, Plein écran, Streamdeck), aperçu, progression, liste, transport, détails de la scène sélectionnée.

- `on_selection_changed(row)` synchronise le panneau de détails avec l'item, en bloquant les signaux (`blockSignals`) pour éviter les boucles d'écriture.
- **Verrou** : `set_show_locked` coupe le drag & drop de la liste, désactive les actions et raccourcis d'édition, puis rappelle `on_selection_changed` pour recalculer l'état des widgets. L'état est dans `self.show_locked`.
- **Bouton écran ↔ menu** : le bouton et l'action `toggle_output_action` sont reliés par `toggled`, et `set_output_enabled` affiche/cache la fenêtre de sortie (et active/désactive le bouton Plein écran).
- **Toujours au premier plan** : `Qt.WindowStaysOnTopHint` est posé sur `ControlWindow` et `OutputWindow` dans `__init__`, avant toute utilisation de `winId()`. Changer ce flag plus tard recréerait la fenêtre native et casserait le rattachement VLC, d'où l'absence d'interrupteur.
- `SceneItemDelegate.paint` force `State_Active` : sans cela, quand la fenêtre perd le focus, Qt utilise la palette inactive et la sélection devient grise ou invisible. La palette `Inactive` de la liste est aussi alignée sur l'`Active`.
- Plateforme : sous Linux, `main()` force `QT_QPA_PLATFORM=xcb` si la variable n'est pas définie (Wayland ne permet pas l'intégration VLC par identifiant X11).

### Streamdeck (boitier) : `DeckWindow`

- Fenêtre `Qt.Tool | WindowStaysOnTopHint`, grille 8 × 4 de `QPushButton` aux politiques `Expanding` ; `resizeEvent` règle la taille de police (`setPixelSize`) en fonction de la hauteur.
- Les scènes sont adressées par **numéro** (`_numbers()` : numéro → ligne), pas par position dans la liste, comme `/scene/<n>`. Page = 21 scènes, 2 pages pour 40 scènes.
- `refresh()` est appelé par `update_current_highlight` et le timer de progression (quand la fenêtre est visible) ; une **signature** de l'état évite de re-styler les boutons quand rien ne change. Le style passe par la propriété dynamique `ds` (`[ds=playing]` dans la feuille de style) suivie d'un `unpolish/polish`.
- Les actions locales appellent directement `ControlWindow` (`play_scene`, `go_next`, `go_previous`, `stop_playback`, `toggle_output_fullscreen`).
- **Option Companion** (`COMPANION_ENABLED`, désactivée par défaut) : les touches `PF / Scène Off / Salle on/off / Salle 20 %` envoient `POST http://<hote:port>/api/location/10/3/<colonne>/press` à Bitfocus Companion (≥ 3.0), dans un thread séparé (timeout 2 s) qui renvoie le résultat par signal Qt. Pas d'authentification ni de retour d'état. Désactivée : aucune touche, aucun menu, aucune connexion réseau. L'adresse est stockée dans `QSettings("JBS", "Studio")`.

## 3.5 API HTTP et threads

`http.server.HTTPServer` tourne dans un thread démon (port `API_PORT`, lié à `0.0.0.0`). Qt n'est pas thread-safe : le handler **ne touche jamais à l'UI**, il émet des signaux du `Bridge`, connectés à des slots de `ControlWindow` et exécutés dans le thread principal.

`/status` fait exception : `Bridge.get_status()` lit directement l'état depuis le thread HTTP (lecture seule). Aucun CORS, aucune authentification, aucun verrou de concurrence.

Ajouter une route :
1. Déclarer un signal sur `Bridge`.
2. Ajouter la branche dans `do_GET`.
3. Connecter le signal dans `start_api_server`.
4. Ajouter la route à la liste de la réponse 404 et à la documentation.

## 3.6 Format du show (`.json`)

```json
{
  "scenes": [
    {
      "number": "1",
      "name": "Ouverture",
      "path": "C:/videos/intro.mp4",
      "audio_path": "C:/musique/ambiance.mp3",
      "mute_primary": false,
      "mute_secondary": false,
      "volume_primary": 100,
      "volume_secondary": 60,
      "auto_next": false
    }
  ]
}
```
Les champs manquants prennent leur valeur par défaut au chargement (`_write_show` / `open_show`), ce qui garde la compatibilité avec les anciens shows. L'ordre du tableau est l'ordre de lecture. Les chemins sont **absolus** : un show déplacé sur une autre machine pointe vers des fichiers à réassigner.

## 3.7 Limites connues

- Pas d'authentification sur l'API (ni sur l'option Companion).
- Le Streamdeck (boitier) n'a pas de retour d'état pour les touches de lumière.
- La disposition du boitier est codée en dur (`DECK_BOTTOM`, 21 scènes par page).
- Un seul show en mémoire, pas d'annulation (undo).
- Le préchargement ne couvre que la scène N+1.
- Pas de gestion de la disparition d'un fichier au moment de la lecture.
- macOS : code prévu (`set_nsobject`) mais non testé.
- Tests : aucun test automatisé dans le dépôt. Les vérifications d'interface ont été faites hors écran (`QT_QPA_PLATFORM=offscreen`, module `vlc` simulé, `QTest`).

## 3.8 Pistes d'évolution

- Authentification simple (jeton) sur l'API.
- Chemins relatifs dans le fichier de show.
- Fondus entre scènes.
- Raccourcis personnalisables.

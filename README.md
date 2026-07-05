# media-mcp

Serveur MCP local (transport stdio) pour piloter un stack média self-hosted :
**Sonarr** + **Radarr**, et **qBittorrent via [qui](https://getqui.com)** (autobrr).

## Prérequis

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) installé

## Installation

```bash
# Cloner / se placer dans le répertoire du projet
cd media-mcp

# Installer les dépendances
uv sync

# Copier et remplir les variables d'environnement
cp .env.example .env
# Éditer .env avec vos URLs et clés API
```

## Lancement en développement

```bash
uv run python -m media_mcp
```

Le serveur démarre en mode stdio et attend des messages MCP sur stdin/stdout.

## Configuration Claude Desktop

Ajouter dans `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) ou `%APPDATA%\Claude\claude_desktop_config.json` (Windows) :

```json
{
  "mcpServers": {
    "media-mcp": {
      "command": "uv",
      "args": ["--directory", "/chemin/absolu/media-mcp", "run", "python", "-m", "media_mcp"],
      "env": {
        "SONARR_URL": "http://localhost:8989",
        "SONARR_API_KEY": "xxx",
        "RADARR_URL": "http://localhost:7878",
        "RADARR_API_KEY": "xxx",
        "QUI_URL": "https://qui.example.com",
        "QUI_API_KEY": "xxx",
        "QUI_INSTANCE": ""
      }
    }
  }
}
```

Remplacer `/chemin/absolu/media-mcp` par le chemin réel du projet.

## Variables d'environnement

| Variable | Description | Défaut |
|---|---|---|
| `SONARR_URL` | URL de base Sonarr | `http://localhost:8989` |
| `SONARR_API_KEY` | Clé API Sonarr | *(requis)* |
| `RADARR_URL` | URL de base Radarr | `http://localhost:7878` |
| `RADARR_API_KEY` | Clé API Radarr | *(requis)* |
| `QUI_URL` | URL de base de l'instance qui | *(requis pour qBit)* |
| `QUI_API_KEY` | Clé API qui (Settings > API Keys) | *(requis pour qBit)* |
| `QUI_INSTANCE` | Instance qBit ciblée (id ou nom) ; vide = auto si une seule | *(optionnel)* |

## Tools disponibles

### Sonarr
| Tool | Type | Description |
|---|---|---|
| `sonarr_system_status` | read | Statut et version de Sonarr |
| `sonarr_list_series` | read | Liste des séries suivies |
| `sonarr_lookup_series(term)` | read | Recherche une série (pour ajout) |
| `sonarr_quality_profiles` | read | Profils de qualité disponibles |
| `sonarr_root_folders` | read | Dossiers racine configurés |
| `sonarr_queue` | read | File de téléchargement en cours (avec l'ID de chaque élément) |
| `sonarr_disk_space` | read | Espace disque par volume, le plus plein en premier |
| `sonarr_health` | read | Avertissements de santé de l'instance |
| `sonarr_history(limit=20, event_type=None)` | read | Événements récents (grab/import/…) avec downloadId ; filtre `event_type` optionnel (voir ci-dessous) |
| `sonarr_delete_queue_item(queue_id, remove_from_client=True, blocklist=False, confirm=False)` | write | Retire un élément de la queue |
| `sonarr_upcoming(days=7)` | read | Épisodes à venir via calendrier |
| `sonarr_series_seasons(series_id)` | read | Détail saison par saison d'une série |
| `sonarr_add_series(tvdb_id, quality_profile_id, root_folder_path, confirm=False)` | write | Ajoute une série |
| `sonarr_set_season_monitoring(series_id, season_number, monitored)` | write | (Dé)monitore une saison précise |
| `sonarr_search_season(series_id, season_number, confirm=False)` | write | Lance la recherche d'une saison |
| `sonarr_delete_season(series_id, season_number, confirm=False)` | destructive | Supprime tous les fichiers d'une saison |
| `sonarr_delete_episode_file(episode_file_id, confirm=False)` | destructive | Supprime un fichier d'épisode |
| `sonarr_delete_series(series_id, delete_files=False, confirm=False)` | destructive | Supprime une série |

### Radarr
| Tool | Type | Description |
|---|---|---|
| `radarr_system_status` | read | Statut et version de Radarr |
| `radarr_list_movies` | read | Liste des films suivis |
| `radarr_lookup_movie(term)` | read | Recherche un film (pour ajout) |
| `radarr_quality_profiles` | read | Profils de qualité disponibles |
| `radarr_root_folders` | read | Dossiers racine configurés |
| `radarr_queue` | read | File de téléchargement en cours (avec l'ID de chaque élément) |
| `radarr_disk_space` | read | Espace disque par volume, le plus plein en premier |
| `radarr_health` | read | Avertissements de santé de l'instance |
| `radarr_history(limit=20, event_type=None)` | read | Événements récents (grab/import/…) avec downloadId ; filtre `event_type` optionnel (voir ci-dessous) |
| `radarr_delete_queue_item(queue_id, remove_from_client=True, blocklist=False, confirm=False)` | write | Retire un élément de la queue |
| `radarr_upcoming(days=7)` | read | Films à venir via calendrier |
| `radarr_add_movie(tmdb_id, quality_profile_id, root_folder_path, confirm=False)` | write | Ajoute un film |
| `radarr_set_movie_monitoring(movie_id, monitored)` | write | (Dé)monitore un film |
| `radarr_search_movie(movie_id, confirm=False)` | write | Lance la recherche d'un film |
| `radarr_delete_movie_file(movie_id, confirm=False)` | destructive | Supprime le fichier d'un film (garde le film suivi) |
| `radarr_delete_movie(movie_id, delete_files=False, confirm=False)` | destructive | Supprime un film |

### qBittorrent (via qui)

> **Accès uniquement via [qui](https://getqui.com)** (le gestionnaire web multi-instance
> d'autobrr), **jamais** via l'API qBittorrent directe. Auth par header `X-API-Key`.
> Les tools ciblent l'instance résolue depuis `QUI_INSTANCE` (id ou nom) ; si vide et qu'une
> seule instance existe, elle est choisie automatiquement ; si plusieurs, une erreur liste
> les instances disponibles.

| Tool | Type | Description |
|---|---|---|
| `qbit_list_instances` | read | Instances qBittorrent gérées par qui (id + nom) |
| `qbit_list_torrents(filter=None)` | read | Torrents de l'instance (nom, hash complet, état, %, taille, ratio, catégorie) ; `filter` = recherche libre (matche aussi le hash) |
| `qbit_get_torrent(hash)` | read | Détail d'un torrent par hash ou préfixe unique (pont avec le `downloadId` Sonarr/Radarr, insensible à la casse) |
| `qbit_pause(hash)` | control | Met un torrent en pause (réversible, pas de confirm) |
| `qbit_resume(hash)` | control | Reprend un torrent (réversible, pas de confirm) |
| `qbit_delete_torrent(hash, delete_files=False, confirm=False)` | destructive | Retire un torrent de qBittorrent, avec option suppression des fichiers |

Les tools prenant un `hash` acceptent le **hash complet (40 car., copiable depuis
`qbit_list_torrents`)** ou un **préfixe unique** ; un préfixe ambigu liste les candidats sans
agir.

Le **hash** qBittorrent est la clé de liaison : c'est la valeur renvoyée par le `downloadId`
de l'historique Sonarr/Radarr. La comparaison est insensible à la casse (qBit renvoie le
hash en minuscules, les *arr souvent en majuscules).

### Pattern dry-run / confirm

Toutes les actions à effet de bord (`add_*`, `delete_*`, `search_*`) acceptent un paramètre `confirm`:
- `confirm=False` (défaut) → aperçu sans exécution (dry-run)
- `confirm=True` → exécution réelle

> **Note hardlink** : les tools de suppression de fichiers (`sonarr_delete_season`,
> `sonarr_delete_episode_file`, `radarr_delete_movie_file`) retirent les fichiers côté
> Sonarr/Radarr uniquement. Si les fichiers sont en hardlink avec un client torrent,
> l'espace disque n'est **pas** libéré tant que le torrent n'est pas aussi supprimé côté
> client. L'aperçu dry-run le rappelle.

### Filtre `event_type` de `*_history`

L'API attend un entier pour son query param `eventType`, donc le filtrage est fait
**côté client** sur le champ texte `eventType` de chaque événement. `event_type` accepte :

| Alias | Correspond à (`eventType` canonique) |
|---|---|
| `grabbed` | `grabbed` |
| `imported` | `downloadFolderImported` |
| `failed` | `downloadFailed` |
| `deleted` | `episodeFileDeleted` (Sonarr) / `movieFileDeleted` (Radarr) |
| `renamed` | `episodeFileRenamed` (Sonarr) / `movieFileRenamed` (Radarr) |
| `ignored` | `downloadIgnored` |

La chaîne canonique exacte est aussi acceptée (ex. `event_type="downloadFolderImported"`).
Une valeur inconnue renvoie un message listant les valeurs valides, sans appel API.
Comme le filtrage est côté client sur une fenêtre élargie (une requête,
`pageSize = max(limit*5, 100)`), un résultat filtré partiel ajoute une note
`showing N of up to {limit} (searched the {window} most recent events)`.

## Développement

```bash
# Lint & format
uv run ruff check src tests
uv run ruff format src tests

# Tests
uv run pytest
```

## Architecture

```
src/media_mcp/
  config.py          # pydantic-settings — lit les variables d'env
  models.py          # modèles pydantic pour les réponses simplifiées
  server.py          # instancie FastMCP et enregistre tous les tools
  __main__.py        # entrypoint: python -m media_mcp
  clients/
    base.py          # ArrClient: httpx async, gestion des erreurs
    sonarr.py        # SonarrClient(ArrClient)
    radarr.py        # RadarrClient(ArrClient)
    qui.py           # QuiClient: httpx async, header X-API-Key (NE dérive PAS d'ArrClient)
  tools/
    sonarr_tools.py  # @mcp.tool pour Sonarr
    radarr_tools.py  # @mcp.tool pour Radarr
    qbit_tools.py    # @mcp.tool pour qBittorrent via qui
```

Ajouter un nouveau service (ex. Jellyseerr) : créer `clients/jellyseerr.py` et
`tools/jellyseerr_tools.py`, puis enregistrer dans `server.py`.

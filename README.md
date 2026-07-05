# media-mcp

Serveur MCP local (transport stdio) pour piloter un stack média self-hosted.
Première itération : **Sonarr** + **Radarr**.

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
        "RADARR_API_KEY": "xxx"
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

## Tools disponibles

### Sonarr
| Tool | Type | Description |
|---|---|---|
| `sonarr_system_status` | read | Statut et version de Sonarr |
| `sonarr_list_series` | read | Liste des séries suivies |
| `sonarr_lookup_series(term)` | read | Recherche une série (pour ajout) |
| `sonarr_quality_profiles` | read | Profils de qualité disponibles |
| `sonarr_root_folders` | read | Dossiers racine configurés |
| `sonarr_queue` | read | File de téléchargement en cours |
| `sonarr_upcoming(days=7)` | read | Épisodes à venir via calendrier |
| `sonarr_series_seasons(series_id)` | read | Détail saison par saison d'une série |
| `sonarr_add_series(tvdb_id, quality_profile_id, root_folder_path, confirm=False)` | write | Ajoute une série |
| `sonarr_set_season_monitoring(series_id, season_number, monitored)` | write | (Dé)monitore une saison précise |
| `sonarr_search_season(series_id, season_number, confirm=False)` | write | Lance la recherche d'une saison |
| `sonarr_delete_series(series_id, delete_files=False, confirm=False)` | destructive | Supprime une série |

### Radarr
| Tool | Type | Description |
|---|---|---|
| `radarr_system_status` | read | Statut et version de Radarr |
| `radarr_list_movies` | read | Liste des films suivis |
| `radarr_lookup_movie(term)` | read | Recherche un film (pour ajout) |
| `radarr_quality_profiles` | read | Profils de qualité disponibles |
| `radarr_root_folders` | read | Dossiers racine configurés |
| `radarr_queue` | read | File de téléchargement en cours |
| `radarr_add_movie(tmdb_id, quality_profile_id, root_folder_path, confirm=False)` | write | Ajoute un film |
| `radarr_delete_movie(movie_id, delete_files=False, confirm=False)` | destructive | Supprime un film |

### Pattern dry-run / confirm

Toutes les actions à effet de bord (`add_*`, `delete_*`) acceptent un paramètre `confirm`:
- `confirm=False` (défaut) → aperçu sans exécution (dry-run)
- `confirm=True` → exécution réelle

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
  tools/
    sonarr_tools.py  # @mcp.tool pour Sonarr
    radarr_tools.py  # @mcp.tool pour Radarr
```

Ajouter un nouveau service (ex. Jellyseerr) : créer `clients/jellyseerr.py` et
`tools/jellyseerr_tools.py`, puis enregistrer dans `server.py`.

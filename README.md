# media-mcp

Serveur MCP local (transport stdio) pour piloter un stack média self-hosted :
**Sonarr** + **Radarr**, **qBittorrent via [qui](https://getqui.com)** (autobrr),
**Prowlarr** (indexeurs) et **Jellyfin** (collections curatives / BoxSets).

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
        "QUI_INSTANCE": "",
        "PROWLARR_URL": "http://localhost:9696",
        "PROWLARR_API_KEY": "xxx"
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
| `PROWLARR_URL` | URL de base Prowlarr | *(requis pour Prowlarr)* |
| `PROWLARR_API_KEY` | Clé API Prowlarr | *(requis pour Prowlarr)* |
| `JELLYFIN_URL` | URL de base Jellyfin (ex. `http://192.168.1.20:8096`) | *(requis pour Jellyfin)* |
| `JELLYFIN_API_KEY` | Clé API Jellyfin (Dashboard > API Keys) | *(requis pour Jellyfin)* |

## Tools disponibles

### Sonarr
| Tool | Type | Description |
|---|---|---|
| `sonarr_system_status` | read | Statut et version de Sonarr |
| `sonarr_list_series` | read | Liste des séries suivies |
| `sonarr_lookup_series(term)` | read | Recherche une série (pour ajout) |
| `sonarr_quality_profiles` | read | Profils de qualité disponibles |
| `sonarr_root_folders` | read | Dossiers racine configurés |
| `sonarr_queue` | read | File de téléchargement + **diagnostic** des items bloqués (voir ci-dessous) |
| `sonarr_disk_space` | read | Espace disque par volume, le plus plein en premier |
| `sonarr_health` | read | Avertissements de santé de l'instance |
| `sonarr_history(limit=20, event_type=None)` | read | Événements récents (grab/import/…) avec downloadId ; filtre `event_type` optionnel (voir ci-dessous) |
| `sonarr_delete_queue_item(queue_id=None, download_id=None, remove_from_client=True, blocklist=False, confirm=False)` | write | Retire un item (par `queue_id`) ou **tous** ceux d'un même `download_id` (season pack) — exactement un des deux |
| `sonarr_upcoming(days=7)` | read | Épisodes à venir via calendrier |
| `sonarr_series_seasons(series_id)` | read | Détail saison par saison d'une série |
| `sonarr_season_episodes(series_id, season_number)` | read | Liste les épisodes d'une saison (E-num, titre, hasFile ✓/✗, monitored ✓/✗, id, fileId) |
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
| `radarr_queue` | read | File de téléchargement + **diagnostic** des items bloqués (voir Sonarr) |
| `radarr_disk_space` | read | Espace disque par volume, le plus plein en premier |
| `radarr_health` | read | Avertissements de santé de l'instance |
| `radarr_history(limit=20, event_type=None)` | read | Événements récents (grab/import/…) avec downloadId ; filtre `event_type` optionnel (voir ci-dessous) |
| `radarr_delete_queue_item(queue_id=None, download_id=None, remove_from_client=True, blocklist=False, confirm=False)` | write | Retire un item (par `queue_id`) ou **tous** ceux d'un même `download_id` — exactement un des deux |
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

### Prowlarr (indexeurs)

Gestionnaire d'indexeurs Servarr — **API en `/api/v1`** (et non v3), auth `X-Api-Key`.
Orienté diagnostic des indexeurs.

| Tool | Type | Description |
|---|---|---|
| `prowlarr_system_status` | read | Version de Prowlarr |
| `prowlarr_list_indexers` | read | Indexeurs configurés (id, nom, activé ✓/✗, protocole, privacy, catégories, tags), triés par nom |
| `prowlarr_indexer_status` | read | Indexeurs **en échec / désactivés temporairement** (+ `disabledTill`, dates d'échec) ; sinon « all indexers healthy » |
| `prowlarr_health` | read | Avertissements globaux Prowlarr (type/source/message) |
| `prowlarr_test_indexer(indexer_id)` | action | Teste la connectivité d'un indexeur → PASS/FAIL + message (pas de confirm) |
| `prowlarr_test_all_indexers` | action | Teste tous les indexeurs → résumé pass/fail, échecs mis en avant |
| `prowlarr_search(query, indexer_ids=None, categories=None, limit=20)` | read | Recherche cross-indexeurs (tout contenu) triée par seeders ; affiche `guid`+`indexerId` pour le grab |
| `prowlarr_grab(guid, indexer_id, confirm=False)` | acquisition | Envoie une release au download client de Prowlarr (dry-run/confirm) |

> `prowlarr_indexer_status` ne porte pas de message textuel de raison (l'API `/indexerstatus`
> n'expose que `indexerId` + horodatages) : il croise la liste des indexeurs pour le nom et
> affiche la date de reprise (`disabledTill`). Pour le « pourquoi » global, voir `prowlarr_health`.

#### Recherche & grab (contenu hors-*arr : ebooks, manga, logiciels…)

`prowlarr_search` interroge tous les indexeurs et renvoie, par release, la **référence de grab**
(`guid` + `indexerId`) à passer à `prowlarr_grab`. Les résultats sont triés par seeders
décroissant (le `limit` de Prowlarr n'étant pas un vrai plafond, la coupe est faite côté client).

`prowlarr_grab` envoie la release au **download client configuré dans Prowlarr** (dry-run par
défaut ; `confirm=True` pour exécuter). **Aucune catégorie n'est passée par le MCP** : le
classement final dans qBittorrent (ebook / logiciel / autre) est décidé par les **Mapped
Categories** du download client, **à configurer dans l'UI Prowlarr** (Settings → Download
Clients). S'il n'y a aucun download client, le grab renvoie un message clair (à ajouter d'abord
dans l'UI). La recherche/le grab avec catégorie explicite restent gérés côté Prowlarr, pas ici.

### Jellyfin (collections curatives / BoxSets)

Serveur média Jellyfin — **endpoints à la racine du serveur** (pas de préfixe `/api/vN`),
auth par header `Authorization: MediaBrowser Token="<clé>"`. Objectif : créer et gérer des
**collections curatives (BoxSets)** avec description, pilotables en langage naturel.

> Client **autonome** (ne dérive PAS d'`ArrClient`, comme `QuiClient`) : Jellyfin n'est pas
> une API *arr. Le `userId` requis par les endpoints d'items est résolu une fois (premier
> compte `Policy.IsAdministrator` via `GET /Users`) puis mis en cache pour la durée du process.

| Tool | Type | Description |
|---|---|---|
| `jellyfin_system_status` | read | Nom + version du serveur (valide la clé API) |
| `jellyfin_list_movies` | read | Films de la bibliothèque (short id, titre, année, tmdbId) |
| `jellyfin_list_collections` | read | Collections/BoxSets (short id, nom, nb d'items, description tronquée) |
| `jellyfin_collection_items(collection_ref)` | read | Contenu d'une collection (par nom ou id) |
| `jellyfin_create_collection(name, movies, overview=None, confirm=False)` | write | Crée une collection depuis une liste de films ; option description (verrouillée) |
| `jellyfin_add_to_collection(collection_ref, movies, confirm=False)` | write | Ajoute des films à une collection |
| `jellyfin_remove_from_collection(collection_ref, movies, confirm=False)` | write | Retire des films d'une collection (les films restent en bibliothèque) |
| `jellyfin_set_overview(item_ref, overview, lock=True, confirm=False)` | write | Écrit la description d'un item (collection ou film) ; `lock` la protège d'un refresh |
| `jellyfin_delete_collection(collection_ref, confirm=False)` | destructive | Supprime le conteneur collection (les films sont conservés) |

#### Résolution des films (`movies`) et références (`collection_ref` / `item_ref`)

Le paramètre `movies` accepte une **liste mixte** : `tmdbId` numériques, ids Jellyfin (ou
**préfixe unique** de 8 car.), ou **titres approximatifs** (casse/accents/articles/ponctuation
normalisés — « Le Solitaire » ≈ « solitaire »). La résolution est une **cascade** qui s'arrête
au premier niveau donnant un match unique :

1. `tmdbId` exact (via `ProviderIds.Tmdb`)
2. id Jellyfin, ou préfixe unique
3. `Name` Jellyfin normalisé
4. `OriginalTitle` Jellyfin normalisé
5. **repli Radarr** — Radarr connaît les titres localisés/alternatifs (`title`,
   `originalTitle`, `alternateTitles`) que Jellyfin n'indexe parfois que sous un titre anglais.
   Le titre demandé y est matché, son `tmdbId` récupéré, puis rebranché sur Jellyfin par
   `tmdbId`. Utilise le **client Radarr interne** (jamais un appel vers nos propres tools MCP) ;
   si Radarr n'est pas configuré ou est injoignable, le niveau 5 est **simplement sauté**
   (`not_found` propre, aucune exception).

La règle est identique à **chaque** niveau : un seul candidat → *matched* ; plusieurs →
*ambiguous* (candidats remontés, **jamais** un choix arbitraire) ; aucun → niveau suivant.

Chaque `movies` déclenche **au plus un** fetch bibliothèque Jellyfin + **au plus un** fetch
Radarr (ce dernier uniquement si une référence atteint le niveau 5, en lazy). Les dry-runs
(`confirm=False`) affichent exactement les films **matched / ambiguous / not found** avant toute
écriture, avec une **colonne indiquant le moyen de résolution** (`tmdb` / `id` / `title` /
`original-title` / `via-radarr`) ; un match `via-radarr` (le plus faillible) affiche en clair le
titre Radarr ET le titre Jellyfin retenus. Sur `confirm=True`, une création/modification
**refuse de procéder** si des références restent non résolues (pas de collection partielle en
silence). `ProviderIds.Tmdb` est le pont fiable avec le `tmdbId` Radarr (jamais de match sur le
titre en interne quand un tmdbId existe).

#### Pièges Jellyfin gérés

- **`POST /Items/{id}` = GET-modify-POST du BaseItemDto complet** (pas de PATCH). Un DTO
  partiel renvoie 400 et peut **corrompre l'item** jusqu'au prochain rescan (champs collection
  `null` passés à `.ToList()`). Avant tout envoi, les champs tableau (`Tags`, `Genres`, `Studios`,
  `People`, `LockedFields`, `GenreItems`, `TagItems`…) sont **normalisés en `[]`** (jamais `null`)
  et `ProviderIds` en `{}` (map, pas liste). Un test respx vérifie explicitement qu'aucun `null`
  ne part dans un champ tableau.
- **Verrouillage** : après écriture d'un `Overview`, `"Overview"` est ajouté à `LockedFields`
  (verrou au niveau champ) pour qu'un refresh de métadonnées n'écrase pas la description.
  Comportement par défaut, désactivable via `lock=False`.
- **Bibliothèque « Collections » absente** : `POST /Collections` peut renvoyer une 500
  (`Sequence contains no elements`) ; c'est traduit en message actionnable (créer une première
  collection depuis l'UI web) plutôt qu'une erreur brute.

> **Cadrage** : l'upload d'affiche (`jellyfin_set_collection_image`) n'est **pas** dans cette
> itération — la place est prévue dans l'architecture (`POST /Items/{id}/Images/Primary`,
> corps base64 + `Content-Type` réel), à ajouter ensuite.

### Tools coordonnés — purge « partout »

Suppriment, en un geste avec aperçu et `confirm`, **les fichiers bibliothèque (Sonarr/Radarr)
ET le(s) torrent(s) correspondants côté qBittorrent-via-qui, cross-seeds inclus**.

| Tool | Type | Description |
|---|---|---|
| `sonarr_purge_season(series_id, season_number, delete_torrent_files=True, include_loose_matches=True, confirm=False)` | destructive | Purge une saison partout (fichiers Sonarr + torrents + cross-seeds) |
| `radarr_purge_movie(movie_id, delete_torrent_files=True, include_loose_matches=True, confirm=False)` | destructive | Purge un film partout (fichier Radarr + torrents + cross-seeds) |

**Flux** :
1. Lister les fichiers concernés côté *arr (saison / film) → nombre + taille.
2. Extraire les `downloadId` depuis l'historique *arr (`/history/series`, `/history/movie`) →
   ensemble des **hash d'origine** (dédupliqués ; un season pack partage un seul `downloadId`).
3. Côté qui, pour chaque origine : résoudre le torrent, puis
   `local-matches?strict=true` → **cross-seeds (siblings)**.
4. Ensemble à supprimer = origines présentes ∪ siblings, dédupliqué par hash.
   `include_loose_matches=False` exclut les siblings `match_type ∈ {name, release}`
   (garde les matches `content_path`) et indique combien ont été exclus.
5. **Dry-run** (`confirm=False`) : aperçu **exhaustif des deux côtés**, rien supprimé.
   **`confirm=True`** : suppression des fichiers *arr **puis** un **seul** `bulk-action delete`
   (avec `deleteFiles` selon `delete_torrent_files`) sur tous les hash ; rapport combiné.

**Cas limites gérés** (sans planter) : aucun `downloadId` (historique purgé → suppression
biblio seule, torrents à gérer à la main) ; origine absente de qBit (ignorée, signalée) ;
saison/film sans fichier (torrents traités quand même) ; cross-seed indispo (repli sur les
origines seules).

> **Honnêteté sur l'espace disque** : les tailles bibliothèque et torrents ne sont **jamais
> additionnées** — hardlinkées, ce sont généralement les **mêmes octets**. L'aperçu les montre
> séparément et rappelle que, comme on supprime les **deux** côtés (+ cross-seeds), l'espace de
> ce contenu sera cette fois **réellement** libéré (≈ la plus grande des deux tailles, pas la somme).

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

### Diagnostic & regroupement de `*_queue`

`sonarr_queue` / `radarr_queue` surfacent, pour chaque item, **pourquoi il est bloqué** :
`trackedDownloadStatus` / `trackedDownloadState` (ex. `warning` / `importBlocked`), le texte
des `statusMessages` et l'`errorMessage` éventuel. Les messages par item sont bornés
(`(+N more)`) pour rester lisibles ; un champ absent/`null` est géré sans erreur.

Les items partageant le **même `downloadId`** (un season pack = un torrent, N lignes) sont
**regroupés** en une entrée `[×N]` affichant le `downloadId` (le pont vers qBittorrent) et la
ligne `ids: …` (les queue IDs individuels du groupe, tronquée si trop longue). Les items sans
`downloadId` restent individuels et conservent taille/ETA.

`*_delete_queue_item` accepte **exactement un** de `queue_id` (un item) ou `download_id`
(**tous** les items du download, retirés en un seul `DELETE /queue/bulk`) ; en dry-run il liste
le nombre d'items, leur(s) titre(s) et les IDs ciblés avant toute suppression.

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
  coordinated.py     # service d'orchestration purge (arr + qui), logique lourde
  jellyfin_resolve.py   # résolution en cascade (tmdbId/id/Name/OriginalTitle + repli Radarr injecté)
  server.py          # instancie FastMCP et enregistre tous les tools
  __main__.py        # entrypoint: python -m media_mcp
  clients/
    base.py          # ArrClient: httpx async, gestion des erreurs
    sonarr.py        # SonarrClient(ArrClient)
    radarr.py        # RadarrClient(ArrClient)
    prowlarr.py      # ProwlarrClient(ArrClient) — /api/v1
    qui.py           # QuiClient: httpx async, header X-API-Key (NE dérive PAS d'ArrClient)
    jellyfin.py      # JellyfinClient: root path, MediaBrowser Token (NE dérive PAS d'ArrClient)
  tools/
    sonarr_tools.py  # @mcp.tool pour Sonarr
    radarr_tools.py  # @mcp.tool pour Radarr
    qbit_tools.py    # @mcp.tool pour qBittorrent via qui
    prowlarr_tools.py     # @mcp.tool pour Prowlarr (indexeurs)
    coordinated_tools.py  # @mcp.tool purge saison/film "partout" (arr + qui)
    jellyfin_tools.py     # @mcp.tool pour Jellyfin (collections curatives / BoxSets)
```

Ajouter un nouveau service (ex. Jellyseerr) : créer `clients/jellyseerr.py` et
`tools/jellyseerr_tools.py`, puis enregistrer dans `server.py`.

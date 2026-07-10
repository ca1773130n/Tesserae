# Chunks de session quotidiens — `.tesserae/session_chunks.db`

<!-- translations:start -->
<p align="center"><a href="../session-chunks.md">English</a> · <a href="session-chunks.ko.md">한국어</a> · <a href="session-chunks.zh.md">中文</a> · <a href="session-chunks.ja.md">日本語</a> · <a href="session-chunks.ru.md">Русский</a> · <a href="session-chunks.es.md">Español</a> · <a href="session-chunks.fr.md">Français</a> · <a href="session-chunks.de.md">Deutsch</a></p>
<!-- translations:end -->
Les requêtes de session fenêtrées — `tesserae summary`, `tesserae decisions` et
les actions d’activité du planificateur `ask` — re-parsaient auparavant chaque
transcription Claude Code / Codex de la fenêtre à chaque appel. Le magasin de
chunks quotidiens persiste chaque tour normalisé **une seule fois**, rangé par
étiquette de jour KST, de sorte qu’un jour passé entièrement couvert est servi
depuis SQLite au lieu d’un rescannage brut. Mesuré sur un vrai corpus de
plusieurs milliers de sessions, cela rend les résumés fenêtrés **~20x plus
rapides**.

Le magasin est un unique fichier SQLite, `.tesserae/session_chunks.db` (WAL,
connexion éphémère par opération) : une table `turns` indexée par jour, une
table `day_coverage` enregistrant quelles paires `(day, harness)` sont
complètes, et une table `meta` avec la version du schéma.

## Ce qui l’écrit

1. **En direct — le tailer du moteur.** Pendant que `tesserae engine` tourne, le
   tailer de sessions ajoute les tours au magasin à mesure qu’il les suit, à
   chaque sondage, et met à jour (upsert) la couverture des jours affectés
   (`source: "tailer"`). Le chemin d’écriture est append-only, idempotent face
   aux tours re-livrés, et ne lève jamais d’exception dans la boucle du daemon.
   Il n’y a délibérément **aucun écrivain via hook SessionEnd** — les écrivains
   SessionEnd en arrière-plan s’accumulent (un mode de défaillance documenté).
2. **Backfill.** Deux points d’entrée parcourent les transcriptions existantes
   et remplissent l’historique (`source: "backfill"`) :
   - `tesserae refresh` exécute automatiquement un backfill au sein de son
     étape d’import de sessions, si bien que le premier refresh après une mise
     à niveau peuple le magasin sans action supplémentaire.
   - `tesserae sessions chunk-backfill [--since YYYY-MM-DD]` l’exécute
     explicitement ; `--since` borne jusqu’où remonter (par défaut :
     l’historique complet).

   Le backfill prend un flock **non bloquant** sur
   `.tesserae/session_chunks.lock` avec une sémantique skip-si-détenu — un
   backfill concurrent (ou un moteur qui le détient déjà) fait que le second
   appelant passe proprement son tour au lieu de faire la queue. Les upserts du
   backfill sont clés sur `(session_path, ts, role, hash(text))`, si bien que
   les lignes du tailer et celles du backfill ne se dupliquent jamais. Un
   chevauchement d’une journée sur les backfills incrémentaux répare les tours
   arrivés après que la couverture d’un jour a été revendiquée pour la première
   fois.

## Ce qui le lit

Le chemin rapide vit au point d’étranglement unique du scan
(`activity_summary.iter_project_transcripts` / `scan_messages`), si bien que
tout ce qui est en aval en hérite de manière transparente :

- `tesserae summary` (y compris sa collecte de décisions embarquée)
- `tesserae decisions`
- `tesserae ask` — les actions `activity_summary` / `decisions` du planificateur
- MCP `activity_summary` et `query_decisions`
- la vue des sessions en direct

## Règle de couverture : aujourd’hui est toujours scanné à cru

Une fenêtre n’est servie depuis les chunks que lorsque **toutes** les
conditions suivantes tiennent :

1. c’est exactement une journée unique alignée sur KST ;
2. ce jour est **strictement antérieur à aujourd’hui** — aujourd’hui est encore
   en cours d’écriture, donc il prend toujours le scan brut de la
   transcription ;
3. une ligne `day_coverage` existe pour **chaque** harness demandé ce jour-là.

Tout le reste retombe sur le scan brut pour cette fenêtre.

## La garantie de repli sur scan brut

Le magasin de chunks est un accélérateur, jamais une source de vérité :

- Toute erreur de base, un fichier manquant/corrompu, ou un désaccord de
  `schema_version` ne produit **rien** depuis le chemin des chunks — le scan
  brut de transcription de l’appelant procède exactement comme avant. Un
  désaccord de schéma supprime et reconstruit le magasin à vide ; la couverture
  disparaît avec lui, donc le repli reste correct.
- Les jours sans couverture (par exemple, le moteur ne tournait pas et aucun
  backfill n’a eu lieu) prennent silencieusement le chemin lent. C’est correct,
  mais l’accélération disparaît — `tesserae doctor` rapporte les trous de
  couverture dans la fenêtre récente et pointe vers
  `tesserae sessions chunk-backfill` (voir [doctor.md](doctor.fr.md)).
- **Invariant de parité :** pour un jour entièrement couvert, les tours servis
  par les chunks sont égaux à ce que le scan brut aurait produit (mêmes
  horodatage, rôle, nom, texte, clé de session et harness).

## Notes opérationnelles

- Gardez `tesserae engine` en marche et les jours passés restent couverts en
  direct ; sinon un `tesserae refresh` occasionnel (ou un `chunk-backfill`
  explicite) comble les trous.
- Le magasin est par projet, vit sous `.tesserae/`, et peut toujours être
  supprimé sans danger — le prochain backfill le reconstruit, et les lecteurs
  retombent sur les scans bruts entre-temps.

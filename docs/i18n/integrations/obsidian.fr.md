# Obsidian — ouvrir le wiki compilé comme un vrai vault

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian.md">English</a> · <a href="obsidian.ko.md">한국어</a> · <a href="obsidian.zh.md">中文</a> · <a href="obsidian.ja.md">日本語</a> · <a href="obsidian.ru.md">Русский</a> · <a href="obsidian.es.md">Español</a> · <a href="obsidian.de.md">Deutsch</a></p>
<!-- translations:end -->

L'export Obsidian de Tesserae transforme votre graphe typé compilé en un vrai vault [Obsidian](https://obsidian.md) avec un parti pris assumé. Pas un simple dossier de markdown — un vault avec une configuration `.obsidian/`, des [callouts](https://help.obsidian.md/Editing+and+formatting/Callouts) sensibles au type, du frontmatter interrogeable via [Dataview](https://blacksmithgu.github.io/obsidian-dataview/), un tableau de bord de vault, et un index des références `wiki://` inter-vaults.

## Prérequis

Compilez d'abord le projet :

```bash
cd /path/to/your-project
tesserae init
tesserae compile
```

La compilation produit `.tesserae/graph.json` (la source de vérité) et une projection markdown brute dans `.tesserae/markdown_projection/`. L'export Obsidian est construit par-dessus cette projection mais ajoute sur chaque page des enrichissements natifs Obsidian.

## 1) Exporter le vault

```bash
tesserae vault export --vault ~/Documents/tesserae-vault
```

Le dossier est créé s'il n'existe pas. Une nouvelle exécution l'écrase de manière idempotente — la projection markdown est déterministe pour un même graphe.

Ce qui atterrit sur disque :

```text
tesserae-vault/
  .obsidian/                  # Obsidian config (app.json, graph.json, plugins)
  README.md                   # Vault entry point
  index.md                    # All nodes grouped by section
  _bridges.md                 # Cross-vault wiki:// references, grouped by alias
  _meta/
    dashboard.md              # Dataview overview tables
  papers/                     # Paper / Repository / SourceDocument pages
  concepts/                   # Concept / Topic / Field / Method / Algorithm pages
  claims/                     # Claim / OpenQuestion / Evidence pages
  raw/                        # Optional raw-source attachments (created lazily)
```

## 2) Ouvrir le dossier dans Obsidian

`File → Open vault... → Open folder as vault → ~/Documents/tesserae-vault`.

Obsidian détectera `.obsidian/`, le reconnaîtra comme un vrai vault, et le chargera. La liste des plugins communautaires inclut Dataview, donc Obsidian proposera de l'activer (recommandé — sans lui, les blocs dataview s'affichent comme de simples blocs de code).

`Settings → Community plugins → Browse → "Dataview" → Install → Enable`.

## 3) Visite guidée du vault

### Points d'entrée

- `README.md` — ce qu'est ce vault et comment le rafraîchir
- `index.md` — chaque nœud par section (papers, concepts, claims) avec des wikilinks
- `_meta/dashboard.md` — vue d'ensemble dataview : pages récentes, papers, concepts/claims

### Enrichissements par page

Chaque page de nœud est désormais livrée avec :

**Callouts sensibles au type.** Un callout sémantique en haut de chaque page rend le type du nœud visible en un coup d'œil :

```markdown
> [!quote] Paper
> The paper triggered a wave of follow-on work: SuGaR aligns Gaussians...

> [!warning] Limitation
> No current method can achieve real-time display rates at 1080p...

> [!question] Open question
> How does dynamic-scene reconstruction scale...
```

Correspondance (extraits) : `Paper → quote`, `Repository → info`, `Contribution → success`, `Performance → info`, `Limitation → warning`, `Causal → important`, `OpenQuestion → question`, `Evidence → example`.

**Arêtes interrogeables via Dataview.** Le frontmatter porte désormais les arêtes typées sous forme de maps imbriquées :

```yaml
edges_out:
  uses: [gaussian-splatting, volumetric-rendering]
  part_of: [3d-4d-vision-and-reconstruction]
  supports_claim: [performance-claim-..., comparison-...]
edges_in:
  mentioned_in: [project-pulse, topic-visual-slam]
```

Vous pouvez écrire des requêtes comme :

````markdown
```dataview
LIST FROM "papers" WHERE contains(edges_out.uses, "nerf")
```

```dataview
TABLE edges_out.supports_claim AS "Claims"
FROM "papers"
WHERE length(edges_out.supports_claim) > 3
SORT length(edges_out.supports_claim) DESC
LIMIT 10
```
````

**Ponts inter-vaults.** Toute URI `wiki://<alias>/<kind>/<slug>` mentionnée dans la description ou les métadonnées d'un nœud est exposée à la fois en tant que champ de frontmatter :

```yaml
cross_vault: [wiki://research/concepts/rlhf, wiki://notes/papers/arxiv-2510-12323]
```

et sous la forme d'une section de corps `Cross-vault references`. L'index `_bridges.md` au niveau du vault agrège chaque référence sortante regroupée par alias de destination, vous permettant d'auditer les liens inter-vaults depuis une seule page.

**Bloc Related (dataview).** Chaque page se termine par une requête qui montre les pages renvoyant vers elle, peuplée automatiquement :

````markdown
```dataview
LIST
FROM "papers" OR "concepts" OR "claims"
WHERE contains(file.outlinks, this.file.link) AND file.name != this.file.name
SORT file.name
LIMIT 25
```
````

### Tableau de bord du vault

`_meta/dashboard.md` embarque des blocs dataview pour les vues agrégées les plus utiles : pages récemment mises à jour, tous les papers avec des colonnes de métadonnées, tous les concepts et claims triés par type. Modifiez-le librement — c'est un point de départ, pas un contrat figé.

### Vue graphe du vault

La vue graphe intégrée d'Obsidian (`Ctrl/Cmd+G`) fonctionne déjà sur les wikilinks émis dans les sections `## Outgoing` / `## Incoming`. Le `.obsidian/graph.json` pré-livré colore les chemins `papers/`, `concepts/`, `claims/` pour faciliter l'orientation. Vous pouvez superposer des vues filtrées par dataview pour obtenir des découpes plus riches.

## Workflows inter-vaults

Enregistrez plusieurs vaults Tesserae afin que les URI `wiki://` soient résolues à travers eux :

```bash
tesserae register-project /path/to/research --name research
tesserae register-project /path/to/notes    --name notes
```

Ré-exportez chaque vault après l'enregistrement. Le `_bridges.md` de chaque export affichera désormais des références résolvables entre vaults, regroupées par alias.

Obsidian lui-même ne suit pas nativement les URI `wiki://` — elles s'affichent en texte brut — mais `_bridges.md` plus la section `Cross-vault references` par page vous offrent un index manuel en attendant qu'un plugin Obsidian dédié arrive.

## Workflow de rafraîchissement

Pour intégrer de nouvelles sources ou des corrections depuis vos fichiers source :

```bash
# Éditez les fichiers source dans les répertoires de sources du projet, puis :
tesserae compile
```

`compile` reprojette désormais le vault automatiquement — vous n'avez plus à exécuter une étape d'export séparée. (`tesserae vault export --vault <chemin>` existe toujours pour une reprojection ponctuelle sans recompilation complète.) Obsidian recharge à chaud les fichiers modifiés sur disque.

Si vous avez ajouté dans le vault des notes markdown qui ne sont pas projetées depuis le graphe (par ex. vos propres annotations personnelles), elles survivent — le projecteur n'écrase que les fichiers qu'il possède sous `papers/`, `concepts/`, `claims/`, ainsi que `index.md`, `_bridges.md`, `_meta/dashboard.md` et `README.md`. Les pages écrites à la main (sans la clé de frontmatter `node_id:`) et le bloc de notes utilisateur dédié (`<!-- user-notes:start -->` … `<!-- user-notes:end -->`) de chaque page projetée sont préservés d'une recompilation à l'autre.

### Les modifications dans Obsidian remontent vers le graphe (synchronisation bidirectionnelle)

Depuis la v0.5.0, le vault **n'est plus un export à sens unique**. C'est désormais une *projection bidirectionnelle* : le graphe typé reste la source de vérité, mais `project compile` relit maintenant vos modifications depuis le vault et les superpose sur le graphe **avant** de reprojeter. Modifiez le `title`, les `aliases`, le callout de description ou tout scalaire de frontmatter hors-système d'un nœud dans Obsidian, recompilez, et le changement survit — et se propage au site statique, à MCP et à toutes les autres projections.

```bash
tesserae compile
# [tesserae] vault overlay: applying 3 field override(s) from obsidian_vault/
```

Ce que la superposition récolte (les champs où *le vault gagne*) :

- `title` → `name` du nœud
- `aliases` → alias du nœud
- le callout de description du corps (ou le premier paragraphe) → `description` du nœud
- chaque scalaire de frontmatter non réservé → `metadata.<key>` (les clés réservées/système `node_id`, `title`, `type`, `aliases`, `source_path`, `edges_out`, `edges_in`, `cross_vault` ne sont jamais traitées comme des overrides utilisateur)

Chaque passage de la superposition écrit un rapport `.tesserae/diverged-fields.md` (`## Field overrides — N across M node(s)`) pour que vous puissiez auditer exactement ce qui a été remonté. Les wikilinks que vous ajoutez dans le bloc de notes utilisateur deviennent des arêtes `user_link`. Passez `tesserae compile` (with `compile_options.no_vault_pull = true` in `.tesserae/config.json`) pour contourner la superposition sur un passage — utile en cas de récupération, ou quand vous voulez intentionnellement que le markdown source l'emporte.

La première compilation après l'activation de cette fonctionnalité obtient un « laissez-passer » : sans ligne de base `vault_snapshot.json` pour l'instant, rien n'est récolté ; le snapshot écrit à la fin devient la ligne de base pour le diff de la compilation suivante.

Pour un workflow live dédié, `tesserae vault sync` réapplique la superposition et reprojette sans recompilation complète :

```bash
# Prévisualisez ce qu'une compilation remonterait, sans muter le graphe.
tesserae vault sync --dry-run

# Surveillez le vault et faites l'aller-retour des modifications en direct (Ctrl-C pour arrêter).
tesserae vault sync --watch

# Après un renommage/suppression de nœuds, supprimez les pages projetées orphelines.
tesserae vault sync --prune-orphans
```

Voir [obsidian-sync.md](obsidian-sync.md) pour la matrice complète de propriété par champ et la justification de conception.

## Quand l'utiliser vs. le site statique

Le site HTML compilé (`tesserae export site` → `.tesserae/site/`) est un export à sens unique en lecture seule, fait pour le partage — déployez-le sur GitHub Pages, S3, ou n'importe quel hébergeur statique. Le vault Obsidian est fait pour **lire, interroger et éditer** localement avec Dataview et la vue graphe d'Obsidian : c'est la seule projection dont les modifications remontent vers le graphe (voir la section synchronisation bidirectionnelle ci-dessus). Les deux projettent depuis le même graphe, ils ne dérivent donc jamais — et les corrections que vous faites dans Obsidian se propagent au site à la compilation suivante.

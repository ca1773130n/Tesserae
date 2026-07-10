# Obsidian — ouvrir le wiki compilé comme un vrai vault

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian.md">English</a> · <a href="obsidian.ko.md">한국어</a> · <a href="obsidian.zh.md">中文</a> · <a href="obsidian.ja.md">日本語</a> · <a href="obsidian.ru.md">Русский</a> · <a href="obsidian.es.md">Español</a> · <a href="obsidian.de.md">Deutsch</a></p>
<!-- translations:end -->

L’export Obsidian de Tesserae transforme votre graphe typé compilé en un vrai vault [Obsidian](https://obsidian.md) avec des partis pris. Pas un répertoire de markdown — un vault avec une config `.obsidian/`, des [callouts](https://help.obsidian.md/Editing+and+formatting/Callouts) conscients des types, un frontmatter interrogeable par [Dataview](https://blacksmithgu.github.io/obsidian-dataview/), un tableau de bord de vault et un index des références inter-vaults `wiki://`.

## Prérequis

Compilez d’abord le projet :

```bash
cd /path/to/your-project
tesserae init
tesserae compile
```

La compilation produit `.tesserae/graph.json` (la source de vérité) et une projection markdown simple à `.tesserae/markdown_projection/`. L’export Obsidian est construit par-dessus cette projection mais superpose des enrichissements natifs Obsidian sur chaque page.

## 1) Exporter le vault

```bash
tesserae vault export --output ~/Documents/tesserae-vault
```

Le répertoire est créé s’il n’existe pas. Relancer écrase de façon idempotente — la projection markdown est déterministe pour un même graphe.

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

## 2) Ouvrir le répertoire dans Obsidian

`File → Open vault... → Open folder as vault → ~/Documents/tesserae-vault`.

Obsidian détectera `.obsidian/`, le reconnaîtra comme un vrai vault et le chargera. La liste des plugins communautaires inclut Dataview, donc Obsidian proposera de l’activer (recommandé — sans lui les blocs dataview se rendent comme des blocs de code).

`Settings → Community plugins → Browse → "Dataview" → Install → Enable`.

## 3) Visiter le vault

### Points d’entrée

- `README.md` — ce qu’est ce vault et comment le rafraîchir
- `index.md` — chaque nœud par section (papers, concepts, claims) avec des wikilinks
- `_meta/dashboard.md` — aperçu dataview : pages récentes, papers, concepts/claims

### Enrichissements par page

Chaque page de nœud est désormais livrée avec :

**Des callouts conscients des types.** Un callout sémantique en haut de chaque page rend le type du nœud visible d’un coup d’œil :

```markdown
> [!quote] Paper
> The paper triggered a wave of follow-on work: SuGaR aligns Gaussians...

> [!warning] Limitation
> No current method can achieve real-time display rates at 1080p...

> [!question] Open question
> How does dynamic-scene reconstruction scale...
```

Correspondance (points saillants) : `Paper → quote`, `Repository → info`, `Contribution → success`, `Performance → info`, `Limitation → warning`, `Causal → important`, `OpenQuestion → question`, `Evidence → example`.

**Des arêtes interrogeables par Dataview.** Le frontmatter porte désormais les arêtes typées comme des maps imbriquées :

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

**Des ponts inter-vaults.** Tout URI `wiki://<alias>/<kind>/<slug>` mentionné dans la description ou les métadonnées d’un nœud est exposé à la fois comme un champ de frontmatter :

```yaml
cross_vault: [wiki://research/concepts/rlhf, wiki://notes/papers/arxiv-2510-12323]
```

et comme une section de corps `Cross-vault references`. L’index `_bridges.md` au niveau du vault agrège chaque référence sortante groupée par alias de destination, si bien que vous pouvez auditer les liens inter-vaults depuis une seule page.

**Un bloc Related (dataview).** Chaque page se termine par une requête qui montre les pages pointant en retour, peuplée automatiquement :

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

`_meta/dashboard.md` est livré avec des blocs dataview pour les vues agrégées les plus utiles : pages récemment mises à jour, tous les papers avec colonnes de métadonnées, tous les concepts et claims triés par type. Éditez-le librement — c’est un point de départ, pas un contrat figé.

### Vue graphe du vault

La vue graphe intégrée d’Obsidian (`Ctrl/Cmd+G`) fonctionne déjà contre les wikilinks émis dans les sections `## Outgoing` / `## Incoming`. Le `.obsidian/graph.json` pré-livré code par couleur les chemins `papers/`, `concepts/`, `claims/` pour l’orientation. Vous pouvez superposer des vues filtrées par dataview pour des tranches plus riches.

## Workflows inter-vaults

Enregistrez plusieurs vaults Tesserae pour que les URI `wiki://` se résolvent entre eux :

```bash
tesserae projects register /path/to/research --name research
tesserae projects register /path/to/notes    --name notes
```

Ré-exportez chaque vault après enregistrement. `_bridges.md` dans chaque export montrera désormais les références résolubles entre vaults groupées par alias.

Obsidian lui-même ne suit pas nativement les URI `wiki://` — ils se rendent comme du texte inline — mais `_bridges.md` plus la section `Cross-vault references` par page vous donnent un index manuel en attendant qu’un plugin Obsidian dédié arrive.

## Workflow de rafraîchissement

Pour incorporer de nouvelles sources ou des corrections depuis vos fichiers sources :

```bash
# Edit source files under your project's source dirs, then:
tesserae compile
```

`compile` re-projette le vault automatiquement — vous n’avez plus à lancer une étape d’export séparée. (`tesserae vault export --output <path>` existe toujours pour une re-projection ponctuelle sans recompilation complète.) Obsidian recharge à chaud les fichiers modifiés sur disque.

Si vous avez ajouté à l’intérieur du vault des notes markdown qui ne sont pas projetées depuis le graphe (p. ex. vos annotations personnelles), elles survivent — le projecteur n’écrase que les fichiers qu’il possède sous `papers/`, `concepts/`, `claims/`, plus `index.md`, `_bridges.md`, `_meta/dashboard.md` et `README.md`. Les pages écrites à la main (sans frontmatter `node_id:`) et le bloc dédié de notes utilisateur (`<!-- user-notes:start -->` … `<!-- user-notes:end -->`) sur chaque page projetée sont préservés à travers les recompilations.

### Les éditions dans Obsidian refluent (sync bidirectionnelle)

Depuis la v0.5.0, le vault n’est **plus un export à sens unique**. C’est une *projection bidirectionnelle* : le graphe typé reste la source de vérité, mais `project compile` relit désormais vos éditions Obsidian depuis le vault et les superpose sur le graphe **avant** de re-projeter. Éditez le `title`, les `aliases`, le callout de description ou tout scalaire de frontmatter non système d’un nœud dans Obsidian, recompilez, et le changement survit — et se propage au site statique, à MCP et à chaque autre projection.

```bash
tesserae compile
# [tesserae] vault overlay: applying 3 field override(s) from obsidian_vault/
```

Ce que l’overlay récolte (les champs *vault-wins*) :

- `title` → `name` du nœud
- `aliases` → alias du nœud
- le callout de description du corps (ou premier paragraphe) → `description` du nœud
- chaque scalaire de frontmatter non réservé → `metadata.<key>` (les clés réservées/système `node_id`, `title`, `type`, `aliases`, `source_path`, `edges_out`, `edges_in`, `cross_vault` ne sont jamais traitées comme des overrides utilisateur)

Chaque exécution de l’overlay écrit un rapport `.tesserae/diverged-fields.md` (`## Field overrides — N across M node(s)`) pour que vous puissiez auditer exactement ce qui a été rapatrié. Les wikilinks que vous ajoutez dans le bloc de notes utilisateur deviennent des arêtes `user_link`. Passez `tesserae compile` (avec `compile_options.no_vault_pull = true` dans `.tesserae/config.json`) pour contourner l’overlay pour une exécution — utile pour la récupération, ou quand vous voulez intentionnellement que le markdown source gagne.

La première compilation après activation de cette fonctionnalité a droit à un « laissez-passer » : sans base de référence `vault_snapshot.json` encore, rien n’est récolté ; le snapshot écrit à la fin devient la base de référence pour le diff de la compilation suivante.

Pour un workflow en direct dédié, `tesserae vault sync` ré-applique l’overlay et re-projette sans recompilation complète :

```bash
# Preview what a compile would pull back, without mutating the graph.
tesserae vault sync --dry-run

# Watch the vault and round-trip edits live (Ctrl-C to stop).
tesserae vault sync --watch

# After renaming/removing nodes, delete projected pages left orphaned.
tesserae vault sync --prune-orphans
```

Voir [obsidian-sync.md](obsidian-sync.fr.md) pour la matrice complète de propriété par champ et la justification de conception.

## Quand utiliser ceci plutôt que le site statique

Le site HTML compilé (`tesserae export site` → `.tesserae/site/`) est un export à sens unique, en lecture seule, pour le partage — poussez-le vers GitHub Pages, S3, n’importe quel hébergeur statique. Le vault Obsidian est fait pour **lire, interroger et éditer** localement avec Dataview et la vue graphe d’Obsidian : c’est la seule projection dont les éditions refluent dans le graphe (voir la section sync bidirectionnelle ci-dessus). Les deux projettent depuis le même graphe, donc ils ne dérivent jamais — et les corrections que vous faites dans Obsidian se propagent au site à la compilation suivante.

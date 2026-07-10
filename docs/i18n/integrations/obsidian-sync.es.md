# Sincronización bidireccional con Obsidian — diseño propuesto

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian-sync.md">English</a> · <a href="obsidian-sync.ko.md">한국어</a> · <a href="obsidian-sync.zh.md">中文</a> · <a href="obsidian-sync.ja.md">日本語</a> · <a href="obsidian-sync.ru.md">Русский</a> · <a href="obsidian-sync.es.md">Español</a> · <a href="obsidian-sync.fr.md">Français</a> · <a href="obsidian-sync.de.md">Deutsch</a></p>
<!-- translations:end -->

> **Estado: Entregado (Tier 1, v0.5.0).** El lector de overlay, las zonas de anexo de user-notes, el modo watch y la poda de huérfanos descritos abajo están vivos tras `tesserae vault sync`. Esta página sirve a la vez de justificación del diseño y de guía de usuario. La federación multi-vault (Tier 3) sigue fuera de alcance.

El [export a Obsidian](obsidian.es.md) solía ser estrictamente unidireccional: el grafo tipado en `.tesserae/graph.json` se proyecta al vault, y `project compile` sobrescribe los archivos proyectados. `obsidian-sync` añade la dirección opuesta — edita una descripción en Obsidian, y sobrevive a la recompilación.

Este documento detalla cómo funciona eso sin volver incoherente el modelo de datos.

## Cambio estratégico, dicho con claridad

El README actual desautoriza la edición en vivo:

> Tesserae elige compilar-desde-la-fuente en lugar de la edición en vivo. Si quieres editar notas en una UI, usa Logseq u Obsidian.

La sincronización bidireccional **cambia ese contrato** para un subconjunto de campos. Vale la pena ser deliberado al respecto. El objetivo no es "Obsidian se convierte en el editor" — es "las ediciones del usuario en Obsidian no se destruyen silenciosamente al recompilar".

## La idea central: overlays, no merges

En lugar de intentar fusionar dos copias divergentes del mismo nodo, trata el vault como una **capa de diff** sobre la proyección:

```text
source markdown  ──extract──▶  base_graph
                                    +
                              vault_overrides     ◀── computed from vault
                                    ↓
                              final_graph  ──project──▶  vault (.md files)
```

`vault_overrides.json` vive en `.tesserae/` y es **computado**, no escrito a mano. En cada compilación, Tesserae recorre el vault, compara cada página proyectada contra lo que escribió la proyección anterior, y registra cada cambio introducido por el usuario como una entrada de overlay. El grafo final es `base_graph` con los overlays aplicados. La siguiente proyección escribe el resultado de vuelta a disco.

Estable en round-trip. Recompilar el mismo vault sin cambios del lado de las fuentes no produce diffs.

## Propiedad por campo

Cada campo de un nodo tiene un propietario. La propiedad decide qué pasa cuando fuente y vault discrepan.

| Campo | Lo posee la fuente | El vault puede anularlo | Notas |
|---|---|---|---|
| `id`, `type` | sí | no | Controlado por el esquema; propiedad del extractor |
| `name` | inicial | sí | El usuario a menudo conoce el nombre canónico mejor que el extractor |
| `aliases` | inicial | sí | Append-only desde el vault; las entradas del vault siempre se preservan |
| `description` | inicial | **sí** | La edición más común en Obsidian |
| `source_path` | sí | no | Procedencia; no puede editarse para eliminarla |
| `metadata` (claves declaradas) | inicial | sí | P. ej. `arxiv_id`, `github_repo` — el usuario puede corregir |
| `metadata.user.*` | n/a | sí | Namespace reservado para claves solo-de-usuario; el extractor nunca escribe |
| Aristas salientes (tipadas) | sí | no | Las aristas viven en la ontología, no en el vault |
| Nuevos wikilinks que el usuario escribe | n/a | sí | Expuestos como `edge_type=user_link`, escritos al grafo |
| Bloque de cuerpo `<!-- user-notes -->` | nunca se escribe | siempre se preserva | Zona append-only que el proyector nunca toca |

## Casos de conflicto y defaults

| Caso | Default | Por qué |
|---|---|---|
| El `description` del vault difiere del `description` re-extraído de la fuente | **Gana el vault**, se registra en `.tesserae/lint-report.md` bajo "diverged fields" | Respeto a la edición del usuario: el usuario claramente quiso la edición. El rastro de auditoría permite revisar más tarde. |
| Archivo fuente borrado, la página proyectada sigue en el vault | Retirar el nodo del grafo, listarlo en `.tesserae/orphans.md` | La fuente es autoritativa para la existencia; el log de huérfanos te deja decidir si restaurar o aceptar |
| El usuario escribió un wikilink a un slug que no existe | Crear un nodo tombstone (tipo `Stub`), exponerlo en el informe de lint | No descartar la intención del usuario; marcarla para limpieza |
| El usuario añadió una clave de frontmatter que el esquema no conoce | Preservar como `metadata.user.<key>`, nunca sobrescribir | Compatible hacia adelante sin contaminar el grafo tipado |
| Dos vaults en máquinas distintas editan el mismo nodo, ambos sincronizados vía Obsidian Sync | **Fuera de alcance para v1.** Gana el último escritor a nivel de filesystem. | La verdadera federación multi-vault es Tier 3; diferir hasta un caso de uso real |

## Zona de anexo user-notes

Cada página proyectada recibe una zona delimitada que el proyector nunca toca:

```markdown
> [!quote] Paper
> Headline contribution and method sketch projected from the graph...

<!-- user-notes:start -->

Your notes here. Anything between the markers survives recompile forever.
Wikilinks here become `user_link` edges in the graph on the next pull.

<!-- user-notes:end -->

## Outgoing
- ...
```

Dos efectos prácticos:
1. Los usuarios pueden anotar cualquier página (p. ej. "ver capítulo 4 de mis notas") sin perderlo al reconstruir.
2. El pase de pull escanea el bloque de user-notes en busca de wikilinks y los expone como aristas `user_link` tipadas en la ontología, dándoles alcanzabilidad en el grafo sin contaminar los tipos de arista formales.

## Transporte remoto — no-objetivo explícito

Tesserae **no** construye un servidor de sync, capa de auth, daemon de resolución de conflictos ni vault alojado. "Bidireccional" aquí significa "la compilación lee desde el vault" — cómo llega el vault a la máquina que compila es problema del usuario, resuelto por herramientas que ya existen:

| Stack | Coste | Notas |
|---|---|---|
| Obsidian Sync | De pago, $4-8/mes | Cifrado E2E, oficial, sencillísimo |
| iCloud / Dropbox / OneDrive | Incluido con el SO | Funciona pero la UX de conflictos es hostil |
| Syncthing | Gratis, auto-alojado | Lo mejor para multi-dispositivo en solitario |
| Git (vault commiteado) | Gratis | La UX de conflictos es la mejor para usuarios técnicos |
| LiveSync (plugin CouchDB) | Gratis, requiere servidor | Multi-dispositivo en tiempo real |

Los cinco son compatibles con el modelo de overlay porque Tesserae ve el vault como archivos-en-disco, no como un flujo de mutaciones.

## Superficie CLI

`tesserae vault sync` aplica las ediciones del vault sobre el grafo tipado y re-proyecta:

```bash
# Apply the overlay once: pull user edits, re-project to the vault.
tesserae vault sync

# Inspect what would change first. Writes .tesserae/diverged-fields.md and
# does NOT apply or re-project.
tesserae vault sync --dry-run

# Point at a specific vault for this call (resolution order:
# --vault > config.obsidian.vault_path > .tesserae/obsidian_vault/).
tesserae vault sync --vault ~/Documents/tesserae-vault

# Make that vault path the default for future commands.
tesserae vault sync --vault ~/Documents/tesserae-vault --persist-vault

# Long-running watch: re-apply the overlay every time the vault changes.
# Ctrl-C to stop; tune the poll cadence with --interval (default 1.5s).
tesserae vault sync --watch --interval 1.5

# Delete projected pages whose source node no longer exists (the projector
# only overwrites, never deletes). Pages with user-notes are kept unless you
# also pass --force-prune-with-notes.
tesserae vault sync --prune-orphans
tesserae vault sync --prune-orphans --force-prune-with-notes
```

El slash command `/tesserae:obsidian-sync` envuelve esto, y `tesserae refresh`
(más la macro `/tesserae:refresh`) ejecuta el overlay como último paso de su
cadena import → compile → sync.

## Estado de entrega

| Tier | Alcance | Estado |
|---|---|---|
| **1a** | Lector de overlay: recorrer el vault, construir `vault_overrides.json`, aplicar en el sync. Las divergencias aterrizan en `.tesserae/diverged-fields.md`. | Entregado |
| **1b** | Zonas de anexo user-notes: el proyector nunca toca los bloques `<!-- user-notes:start --> ... <!-- user-notes:end -->`. | Entregado |
| **2** | Modo watch: `obsidian-sync --watch` de larga vida re-ejecuta el overlay en un bucle de sondeo conforme el vault cambia. | Entregado |
| **3** | Federación multi-vault: el grafo guarda procedencia por vault, soporta ediciones concurrentes entre vaults sincronizados. | Diferido hasta un caso de uso real |

## No-objetivos (explícitamente)

- Un servidor de sync / auth / backend alojado.
- Edición colaborativa en tiempo real dentro de Obsidian (usa LiveSync si necesitas esto).
- Reescribir el extractor para hacer round-trip de cada campo — el markdown fuente sigue siendo canónico para todo lo que está fuera de la tabla de overrides.
- Sync del sitio HTML estático (`build-site` sigue siendo solo-proyección).

## Decisiones resueltas

Estas eran las cuestiones abiertas en tiempo de diseño; la implementación entregada de los Tiers 1–2 las zanjó así:

1. **Forma del informe de lint.** Los campos divergentes se exponen como un archivo dedicado `.tesserae/diverged-fields.md` (escrito por `--dry-run` y en cada apply) para que pueda diffearse en git, en lugar de como una sección de `lint-report.md`.
2. **Tipo del nodo tombstone.** ¿Añadir `Stub` como tipo real del esquema, o apoyarse en `OpenQuestion` con un discriminador `_kind: stub`? Propuesto: tipo real, llamado `Stub`, oculto de los índices públicos.
3. **Default de pull-on-compile.** ¿ON por defecto u OFF por defecto? Propuesto: ON cuando existe un vault en la ruta configurada, con un prompt de confirmación único la primera vez que se activa para que los usuarios opten deliberadamente.
4. **¿Qué cuenta como "la proyección anterior" para el diff?** ¿Snapshot guardado en `.tesserae/vault_snapshot.json`, o re-proyectar al vuelo en cada compilación? Propuesto: snapshot, escrito al final de cada compilación. Más barato y evita que el no-determinismo del extractor se filtre en el overlay.
5. **Proyección de vault multi-idioma.** La proyección de hoy es mono-idioma (el de la fuente). ¿Deberían los overlays ser conscientes del locale (p. ej. una edición de `description` en un vault coreano aplica solo a la proyección coreana)? Propuesto: fuera de alcance para v1; el vault es mono-idioma coincidiendo con el idioma primario del proyecto.

## Cómo aparece esto en `obsidian.md`

La guía de cara al usuario se mantiene enfocada en "puedes leer y consultar el vault", y luego enlaza aquí para la historia del round-trip con un resumen de una línea: "Edita campos en Obsidian, sobreviven a la recompilación. Ver [obsidian-sync.md](obsidian-sync.es.md) para el modelo completo."

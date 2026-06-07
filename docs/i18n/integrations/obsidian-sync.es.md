# Sincronización bidireccional con Obsidian — diseño propuesto

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian-sync.md">English</a> · <a href="obsidian-sync.ko.md">한국어</a> · <a href="obsidian-sync.zh.md">中文</a> · <a href="obsidian-sync.ja.md">日本語</a> · <a href="obsidian-sync.ru.md">Русский</a> · <a href="obsidian-sync.fr.md">Français</a> · <a href="obsidian-sync.de.md">Deutsch</a></p>
<!-- translations:end -->

> **Estado: Entregado (Tier 1, v0.5.0).** El lector de overlay, las zonas de agregación de user-notes, el modo watch y la limpieza de huérfanos descritos abajo están vivos tras `tesserae vault sync`. Esta página sirve a la vez de justificación de diseño y de guía de usuario. La federación multi-vault (Tier 3) sigue fuera de alcance.

La [exportación a Obsidian](obsidian.md) solía ser estrictamente unidireccional: el grafo tipado en `.tesserae/graph.json` se proyecta al vault, y `project compile` sobrescribe los archivos proyectados. `obsidian-sync` añade la dirección opuesta — edita una descripción en Obsidian y sobrevive a la recompilación.

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

`vault_overrides.json` vive en `.tesserae/` y es **calculado**, no escrito a mano. En cada compilación, Tesserae recorre el vault, compara cada página proyectada con lo que la proyección anterior escribió, y registra cada cambio introducido por el usuario como una entrada del overlay. El grafo final es `base_graph` con los overlays aplicados. La siguiente proyección escribe el resultado de vuelta a disco.

Estable en el viaje de ida y vuelta. Recompilar el mismo vault sin cambios en el lado de la fuente no produce diffs.

## Propiedad por campo

Cada campo de un nodo tiene un propietario. La propiedad decide qué pasa cuando la fuente y el vault no coinciden.

| Campo | Propiedad de la fuente | El vault puede sobrescribir | Notas |
|---|---|---|---|
| `id`, `type` | sí | no | Controlado por el esquema; propiedad del extractor |
| `name` | inicial | sí | El usuario suele conocer el nombre canónico mejor que el extractor |
| `aliases` | inicial | sí | Solo agregación desde el vault; las entradas del vault siempre se preservan |
| `description` | inicial | **sí** | La edición más común en Obsidian |
| `source_path` | sí | no | Procedencia; no se puede borrar editando |
| `metadata` (claves declaradas) | inicial | sí | P. ej. `arxiv_id`, `github_repo` — el usuario puede corregir |
| `metadata.user.*` | n/a | sí | Espacio de nombres reservado para claves solo del usuario; el extractor nunca escribe ahí |
| Aristas salientes (tipadas) | sí | no | Las aristas viven en la ontología, no en el vault |
| Nuevos wikilinks que el usuario escribe | n/a | sí | Aparecen como `edge_type=user_link`, se escriben al grafo |
| Bloque `<!-- user-notes -->` del cuerpo | nunca escrito | siempre preservado | Zona solo de agregación que el proyector nunca toca |

## Casos de conflicto y comportamientos por defecto

| Caso | Por defecto | Por qué |
|---|---|---|
| El `description` del vault difiere del `description` re-extraído de la fuente | **Gana el vault**, se registra en `.tesserae/lint-report.md` bajo "diverged fields" | Respeto a la edición del usuario: la edición fue claramente intencional. El rastro de auditoría permite revisarlo después. |
| Archivo fuente eliminado, la página proyectada sigue en el vault | Eliminar el nodo del grafo, listarlo en `.tesserae/orphans.md` | La fuente es autoritativa sobre la existencia; el log de huérfanos te deja decidir si restaurar o aceptar |
| El usuario escribió un wikilink a un slug que no existe | Crear un nodo lápida (tipo `Stub`), sacarlo a la luz en el lint report | No descartar la intención del usuario; marcarla para limpieza |
| El usuario añadió una clave de frontmatter que el esquema no conoce | Preservar como `metadata.user.<key>`, nunca sobrescribir | Compatible hacia adelante sin contaminar el grafo tipado |
| Dos vaults en máquinas distintas editan el mismo nodo, ambos sincronizados vía Obsidian Sync | **Fuera de alcance para v1.** Gana el último escritor a nivel de sistema de archivos. | La verdadera federación multi-vault es Tier 3; se aplaza hasta que haya un caso de uso real |

## Zona de agregación de user-notes

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
2. La pasada de pull escanea el bloque user-notes en busca de wikilinks y los expone como aristas `user_link` tipadas en la ontología, dándoles alcance en el grafo sin contaminar los tipos formales de aristas.

## Transporte remoto — no-objetivo explícito

Tesserae **no** construye un servidor de sincronización, una capa de autenticación, un daemon de resolución de conflictos ni un vault alojado. "Bidireccional" aquí significa "la compilación lee del vault" — qué hace que el vault llegue a la máquina que ejecuta la compilación es problema del usuario, resuelto por herramientas que ya existen:

| Stack | Coste | Notas |
|---|---|---|
| Obsidian Sync | De pago, 4-8 $/mes | Cifrado E2E, oficial, muy sencillo |
| iCloud / Dropbox / OneDrive | Incluido con el SO | Funciona pero la UX de conflictos es hostil |
| Syncthing | Gratis, autoalojado | Lo mejor para solo entre dispositivos |
| Git (vault versionado) | Gratis | La UX de conflictos es la mejor para usuarios técnicos |
| LiveSync (plugin CouchDB) | Gratis, requiere servidor | Multi-dispositivo en tiempo real |

Los cinco son compatibles con el modelo de overlay porque Tesserae ve el vault como archivos-en-disco, no como un flujo de mutaciones.

## Superficie de CLI

`tesserae vault sync` aplica las ediciones del vault sobre el grafo tipado y reproyecta:

```bash
# Aplicar el overlay una vez: trae ediciones de usuario, reproyecta al vault.
tesserae vault sync

# Inspecciona primero qué cambiaría. Escribe .tesserae/diverged-fields.md y
# NO aplica ni reproyecta.
tesserae vault sync --dry-run

# Apunta a un vault concreto para esta llamada (orden de resolución:
# --vault > config.obsidian.vault_path > .tesserae/obsidian_vault/).
tesserae vault sync --vault ~/Documents/tesserae-vault

# Hacer que esa ruta de vault sea el valor por defecto para futuros comandos.
tesserae vault sync --vault ~/Documents/tesserae-vault --persist-vault

# Watch de larga duración: reaplica el overlay cada vez que cambia el vault.
# Ctrl-C para detener; ajusta la cadencia con --poll-interval (por defecto 1.5 s).
tesserae vault sync --watch --poll-interval 1.5

# Elimina páginas proyectadas cuyo nodo fuente ya no existe (el proyector
# solo sobrescribe, nunca borra). Las páginas con user-notes se conservan
# salvo que pases también --force-prune-with-notes.
tesserae vault sync --prune-orphans
tesserae vault sync --prune-orphans --force-prune-with-notes
```

El comando slash `/tesserae:obsidian-sync` lo envuelve, y `tesserae refresh`
(más la macro `/tesserae:refresh`) ejecuta el overlay como último paso de su
cadena import → compile → sync.

## Estado de entrega

| Tier | Alcance | Estado |
|---|---|---|
| **1a** | Lector de overlay: recorre el vault, construye `vault_overrides.json`, lo aplica en la sincronización. Las divergencias van a `.tesserae/diverged-fields.md`. | Entregado |
| **1b** | Zonas de agregación de user-notes: el proyector nunca toca los bloques `<!-- user-notes:start --> ... <!-- user-notes:end -->`. | Entregado |
| **2** | Modo watch: `obsidian-sync --watch` de larga duración reejecuta el overlay en un bucle de sondeo a medida que cambia el vault. | Entregado |
| **3** | Federación multi-vault: el grafo guarda procedencia por vault, soporta ediciones concurrentes entre vaults sincronizados. | Aplazado hasta que haya un caso de uso real |

## No-objetivos (explícitamente)

- Un servidor de sincronización / auth / backend alojado.
- Edición colaborativa en tiempo real dentro de Obsidian (usa LiveSync si lo necesitas).
- Reescribir el extractor para hacer round-trip de cada campo — el markdown fuente sigue siendo canónico para todo lo que esté fuera de la tabla de overrides.
- Sincronización del sitio HTML estático (`build-site` sigue siendo solo proyección).

## Decisiones resueltas

Estas eran las preguntas abiertas en el momento del diseño; la implementación entregada de Tier 1–2 las resolvió así:

1. **Forma del lint report.** Los campos divergentes aparecen como un archivo dedicado `.tesserae/diverged-fields.md` (escrito por `--dry-run` y en cada aplicación) para poder diffearlo en git, en lugar de como una sección de `lint-report.md`.
2. **Tipo de nodo lápida.** ¿Añadir `Stub` como un tipo real del esquema, o aprovechar `OpenQuestion` con un discriminador `_kind: stub`? Propuesta: tipo real, llamado `Stub`, oculto de los índices públicos.
3. **Pull-on-compile por defecto.** ¿Por defecto activado o desactivado? Propuesta: activado cuando existe un vault en la ruta configurada, con un prompt de confirmación único la primera vez que se activa para que los usuarios opten deliberadamente.
4. **¿Qué cuenta como "la proyección anterior" para el diff?** ¿Snapshot guardado en `.tesserae/vault_snapshot.json`, o re-proyectar al vuelo en cada compilación? Propuesta: snapshot, escrito al final de cada compilación. Más barato y evita que el no-determinismo del extractor se filtre al overlay.
5. **Proyección de vault multilingüe.** La proyección actual es monolingüe (la de la fuente). ¿Deberían los overlays ser conscientes del idioma (p. ej. que una edición a `description` en un vault coreano se aplique solo a la proyección coreana)? Propuesta: fuera de alcance para v1; el vault es monolingüe coincidiendo con el idioma principal del proyecto.

## Cómo aparece esto en `obsidian.md`

La guía de cara al usuario sigue centrada en "puedes leer y consultar el vault", y luego enlaza aquí para la historia del ida y vuelta con un resumen de una línea: "Edita campos en Obsidian, sobreviven a la recompilación. Ver [obsidian-sync.md](obsidian-sync.md) para el modelo completo."

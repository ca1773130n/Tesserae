# Chunks de sesión diarios — `.tesserae/session_chunks.db`

<!-- translations:start -->
<p align="center"><a href="../session-chunks.md">English</a> · <a href="session-chunks.ko.md">한국어</a> · <a href="session-chunks.zh.md">中文</a> · <a href="session-chunks.ja.md">日本語</a> · <a href="session-chunks.ru.md">Русский</a> · <a href="session-chunks.es.md">Español</a> · <a href="session-chunks.fr.md">Français</a> · <a href="session-chunks.de.md">Deutsch</a></p>
<!-- translations:end -->
Las consultas de sesión por ventana — `tesserae summary`, `tesserae decisions` y las
acciones de actividad del planificador de `ask` — solían re-parsear cada transcript de Claude
Code / Codex dentro de la ventana en cada llamada. El almacén de chunks diarios persiste cada
turno normalizado **una sola vez**, agrupado por etiqueta de día KST, de modo que un día pasado
completamente cubierto se sirve desde SQLite en lugar de un rescaneo en bruto. Medido sobre un
corpus real de varios miles de sesiones, esto hace los resúmenes por ventana **~20x más rápidos**.

El almacén es un único archivo SQLite, `.tesserae/session_chunks.db` (WAL,
conexión de vida corta por operación): una tabla `turns` indexada por día, una
tabla `day_coverage` que registra qué pares `(day, harness)` están completos, y
una tabla `meta` con la versión del esquema.

## Qué lo escribe

1. **En vivo — el tailer del engine.** Mientras `tesserae engine` corre, el tailer
   de sesiones anexa turnos al almacén conforme los va siguiendo, por cada poll, y hace upsert
   de la cobertura para los días afectados (`source: "tailer"`). La ruta de escritura es
   append-only, idempotente ante turnos re-entregados, y nunca lanza excepciones dentro
   del bucle del daemon. Deliberadamente **no hay un escritor en el hook SessionEnd** —
   los escritores de SessionEnd en segundo plano se acumulan (un modo de fallo registrado).
2. **Backfill.** Dos puntos de entrada recorren los transcripts existentes y rellenan el historial
   (`source: "backfill"`):
   - `tesserae refresh` ejecuta un backfill automáticamente como parte de su
     paso de importación de sesiones, así que el primer refresh tras actualizar puebla el
     almacén sin acción adicional.
   - `tesserae sessions chunk-backfill [--since YYYY-MM-DD]` lo ejecuta
     explícitamente; `--since` acota hasta dónde retroceder (por defecto: el historial
     completo).

   El backfill toma un flock **no bloqueante** sobre
   `.tesserae/session_chunks.lock` con semántica skip-if-held — un backfill
   concurrente (o un engine que ya lo retiene) hace que el segundo llamador se salte
   limpiamente en lugar de encolarse. Los upserts del backfill se indexan por
   `(session_path, ts, role, hash(text))`, así que las filas del tailer y las del backfill
   nunca se duplican entre sí. Un solapamiento de un día en los backfills incrementales
   sana los turnos que aterrizaron después de que la cobertura de un día se reclamara por primera vez.

## Qué lo lee

La ruta rápida vive en el único punto de estrangulamiento del escaneo
(`activity_summary.iter_project_transcripts` / `scan_messages`), así que todo lo
que está aguas abajo la hereda de forma transparente:

- `tesserae summary` (incluida su recolección embebida de decisiones)
- `tesserae decisions`
- `tesserae ask` — las acciones `activity_summary` / `decisions` del planificador
- MCP `activity_summary` y `query_decisions`
- la vista de sesiones en vivo

## Regla de cobertura: hoy siempre se escanea en bruto

Una ventana se sirve desde chunks solo cuando se cumplen **todas** las condiciones siguientes:

1. es exactamente un único día alineado a KST;
2. ese día es **estrictamente anterior a hoy** — hoy todavía se está escribiendo, así que
   siempre toma el escaneo del transcript en bruto;
3. existe una fila de `day_coverage` para **cada** harness solicitado en ese día.

Cualquier otro caso recae en el escaneo en bruto para esa ventana.

## La garantía del fallback a escaneo en bruto

El almacén de chunks es un acelerador, nunca una fuente de verdad:

- Cualquier error de DB, un archivo ausente/corrupto, o una discrepancia de `schema_version`
  produce **nada** desde la ruta de chunks — el escaneo del transcript en bruto del llamador
  procede exactamente como antes. Una discrepancia de esquema descarta y reconstruye el almacén
  vacío; la cobertura desaparece con él, así que el fallback sigue siendo correcto.
- Los días sin cobertura (por ejemplo, el engine no estaba corriendo y no ha habido
  backfill) toman silenciosamente la ruta lenta. Correcto, pero la aceleración
  desaparece — `tesserae doctor` reporta los huecos de cobertura en la ventana reciente
  y apunta a `tesserae sessions chunk-backfill` (ver
  [doctor.md](doctor.es.md)).
- **Invariante de paridad:** para un día completamente cubierto, los turnos servidos desde chunks son iguales
  a lo que habría producido el escaneo en bruto (mismo timestamp, role, name, text,
  clave de sesión y harness).

## Notas operativas

- Mantén `tesserae engine` corriendo y los días pasados quedan cubiertos en vivo; si no, un
  `tesserae refresh` ocasional (o un `chunk-backfill` explícito) cierra los
  huecos.
- El almacén es por proyecto, vive bajo `.tesserae/`, y siempre puede
  borrarse con seguridad — el siguiente backfill lo reconstruye, y los lectores recaen en escaneos
  en bruto mientras tanto.

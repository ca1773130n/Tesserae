# Consolidación automática — el ciclo de sueño del motor

<!-- translations:start -->
<p align="center"><a href="../engine-consolidation.md">English</a> · <a href="engine-consolidation.ko.md">한국어</a> · <a href="engine-consolidation.zh.md">中文</a> · <a href="engine-consolidation.ja.md">日本語</a> · <a href="engine-consolidation.ru.md">Русский</a> · <a href="engine-consolidation.fr.md">Français</a> · <a href="engine-consolidation.de.md">Deutsch</a></p>
<!-- translations:end -->

El cerebro consolida la memoria durante el descanso. Mientras duermes, la experiencia bruta del día se reorganiza, se comprime y se integra — lo reciente, lo ruidoso se pliega en una estructura duradera. El motor de Tesserae hace lo mismo. Cuando un proyecto se **inactiva**, el demonio siempre encendido deja de esperar la siguiente edición y pasa el tiempo reorganizando lo que ya sabe: ejecuta un paso de destilación que reorganiza, comprime y olvida de manera segura la memoria de cada agente.

Hasta ahora, ese paso solo se ejecutaba cuando lo solicitabas — `tesserae refresh` bajo presión de memoria, o un `tesserae distill` explícito. El motor recompilaba en cada archivo y evento de sesión, pero nunca se consolidaba automáticamente. El **ciclo de sueño** cierra esa brecha: deja `tesserae engine` en funcionamiento y la consolidación ocurre durante el descanso, sin ningún comando que recordar.

Como todo en el sistema de [memoria en capas](agent-memory.md), esto es **no-op a menos que optes** — el demonio consolida en inactividad, pero la destilación que hay debajo solo funciona cuando se establece `TESSERAE_AGENT_DISTILL`.

## Cuándo se dispara

Un hilo de consolidación dedicado se despierta en un **intervalo de verificación** fijo (30 segundos por defecto) y evalúa dos desencadenantes independientes contra un reloj de actividad monótono:

- **Disparador de inactividad.** El proyecto no ha visto un evento de disparo ni una ejecución de canalización durante al menos `--consolidate-idle` segundos (por defecto **300s = 5 min**). Este es el caso de "consolidación durante el descanso" — el motor notó que dejaste de trabajar y utilizó la pausa. Un **piso** desde la última consolidación previene cambios bruscos, por lo que un proyecto ocupado que acaba de quedarse tranquilo no se consolida en un disparo sensible. - **Disparador de techo.** Han transcurrido al menos `--consolidate-every` segundos desde la última consolidación, **independientemente de la actividad** (por defecto **21600s = 6h**). Esto garantiza que un proyecto continuamente ocupado aún se consolide periódicamente en lugar de nunca tener un momento tranquilo. Configurarlo a `0` desactiva el techo — entonces la inactividad es el único disparador.

Cada edición, turno de sesión o recompilación aumenta el reloj de actividad, por lo que la ventana de inactividad solo transcurre durante el descanso genuino. Ambos relojes son **monótonos**, nunca reloj de pared, y nunca se persisten en ningún artefacto — el tiempo de consolidación nunca puede perturbar el gráfico byte-determinista.

## Qué se ejecuta

Cada disparo carga el gráfico compilado desde `.tesserae/graph.json` (si el archivo está ausente, el paso se omite) y llama al mismo punto de entrada `maybe_distill_on_refresh` que usa `tesserae refresh`. Esa función está **triple compuerta** internamente y nunca se eleva por falla por agente:

1. **Puerta de inclusión** — `TESSERAE_AGENT_DISTILL=1` (o `{"agent_distill": {"enabled": true}}` en `config.json`). Desactivado por defecto; todo el ciclo es un no-op seguro hasta que lo configures.
2. **Marca de agua por agente** — un agente cuyas conclusiones no han cambiado desde su última destilación se omite.
3. **Presión de memoria por agente** — solo los agentes cuyas conclusiones no destiladas ya no caben en la mitad de una lectura de contexto se consolidan (disparador estilo MemGPT).

Entonces, incluso cuando la consolidación se *dispara* en un cronograma, solo *funciona* para los agentes que se incluyeron y realmente acumularon suficiente memoria nueva para justificarlo. Consulta [Memoria de agente en capas](agent-memory.md) para lo que produce la destilación.

## Seguridad y determinismo

- **Se ejecuta bajo la puerta de compilación.** La consolidación adquiere el mismo bloqueo que una recompilación, por lo que se serializa con compilaciones y **nunca se superpone a una**. Una compilación pendiente espera una consolidación en vuelo y viceversa — el gráfico nunca se lee durante la escritura.
- **Nunca se eleva al bucle del demonio.** Todo el paso se envuelve; cualquier error se registra y el hilo mantiene el bucle. Una consolidación fallida nunca derriba el motor.
- **No-op cuando la puerta está apagada.** Con `TESSERAE_AGENT_DISTILL` sin establecer, el paso no carga nada costoso y regresa inmediatamente, por lo que dejar el ciclo de sueño encendido cuesta esencialmente nada.
- **Artefactos deterministas, sin cambios.** Los artefactos destilados permanecen deterministas dadas sus entradas; el ciclo de sueño solo cambia *cuándo* se ejecuta la destilación, nunca *qué* produce. El tiempo de inactividad nunca se filtra en `graph.json` o ninguna capa destilada.
- **Apagado limpio.** El hilo de consolidación observa el evento de parada del demonio y sale adecuadamente en `Ctrl-C` / apagado. Es solo una característica de modo de larga duración: `tesserae engine ... --once` nunca la inicia.

## Banderas CLI

| Bandera | Defecto | Efecto |
|---|---|---|
| `--consolidate` / `--no-consolidate` | on | Habilita o deshabilita el ciclo de sueño completamente. Habilitado por defecto (no-op si la puerta de destilación no está configurada). |
| `--consolidate-idle SECONDS` | `300` | Ventana de descanso: consolida después de esta cantidad de segundos sin actividad. |
| `--consolidate-every SECONDS` | `21600` | Techo: consolida al menos tan a menudo independientemente de la actividad. `0` desactiva el techo. |
| `--consolidate-check SECONDS` | `30` | Con qué frecuencia el hilo de consolidación se despierta para reevaluar los disparadores. |

## Comportamiento de flota(`--all`)

`tesserae engine --all` mantiene fresco cada proyecto registrado en un proceso. Cada unidad de proyecto obtiene su propio hilo de consolidación con los mismos controles, y todas las unidades comparten una puerta de compilación de todo el parque — entonces una consolidación en un proyecto se serializa contra compilaciones en todo el parque, nunca superponiéndose a ninguna.

## Ejemplo trabajado

Activa la destilación, luego ejecuta el motor con un ciclo de sueño más rápido para una demostración — consolida después de 60s inactivo, y al menos cada 30 min sin importar qué:

```bash
export TESSERAE_AGENT_DISTILL=1
tesserae engine \
  --consolidate-idle 60 \
  --consolidate-every 1800 \
  --consolidate-check 15
```

Trabaja en tu editor y agentes como de costumbre; el motor observa, debounces y recompila cada cambio. Detente durante un minuto y se dispara el disparador de inactividad: el hilo de consolidación adquiere la puerta de compilación y destila cualquier agente bajo presión de memoria — reorganizando, comprimiendo y olvidando de manera segura — luego vuelve a dormir. Continúa trabajando más allá de la marca de media hora sin nunca pausar y el techo también se dispara, por lo que un proyecto despiadado aún se consolida.

Para mantener el motor funcionando pero dejar la consolidación para ejecuciones manuales de `tesserae distill`, pasa `--no-consolidate`. Para que se ejecute en inactividad pero nunca en un cronograma fijo, pasa `--consolidate-every 0`.

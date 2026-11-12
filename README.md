# engdis_mcp

Servidor MCP de uso personal para English Discoveries (`ed.engdis.com/ucb`) y sus Unit Tests (`ets.toeicolpc.com`). Sirve para cualquier curso y unidad de la plataforma. Se probó con las prácticas de las unidades 1 a 6 del TOEIC Course 2 y con las unidades 1 a 3 del TOEIC Bridge.

Su objetivo es gastar menos tokens:

- Controla su propio navegador con Playwright, sin depender de la extensión de Chrome.
- Devuelve texto compacto en vez de capturas.
- Hace en una sola llamada los bucles que antes costaban decenas de clics.

## Requisitos

- macOS: el speaking usa `say` para generar la voz.
- `ffmpeg`: `brew install ffmpeg`.
- Python 3.10 o superior.
- Google Chrome instalado. Para usar el Chromium de Playwright en su lugar, pon `ENGDIS_CHANNEL=""` y ejecuta `playwright install chromium`.

## Instalación

```bash
cd ~/Downloads/proyecto_ingles/engdis_mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

La primera transcripción descarga el modelo `small.en` de whisper, que pesa unos 500 MB.

## Conectarlo

Es un servidor MCP estándar, así que sirve con cualquier cliente que hable MCP. Hay dos formas de arrancarlo:

- **Local (stdio):** el cliente lanza el proceso. Vale para Claude Code, Claude Desktop, Gemini CLI, Codex, Cursor, VS Code y similares.
- **HTTP (`--http`):** para clientes que solo aceptan una URL, como ChatGPT.

En los ejemplos, `PY` es `~/Downloads/proyecto_ingles/engdis_mcp/.venv/bin/python` y `SRV` es `~/Downloads/proyecto_ingles/engdis_mcp/server.py`; ponlos con la ruta completa.

### Claude Code

```bash
claude mcp add engdis -- PY SRV
```

### Claude Desktop

En `claude_desktop_config.json`:

```json
{ "mcpServers": { "engdis": { "command": "PY", "args": ["SRV"] } } }
```

### Gemini CLI

En `~/.gemini/settings.json` (el `timeout` va en milisegundos):

```json
{ "mcpServers": { "engdis": { "command": "PY", "args": ["SRV"], "timeout": 120000 } } }
```

### Codex (CLI de OpenAI)

```bash
codex mcp add engdis -- PY SRV
```

Si alguna herramienta se corta, sube el límite en `~/.codex/config.toml`, dentro de `[mcp_servers.engdis]`: `tool_timeout_sec = 120`.

### ChatGPT (web o escritorio)

ChatGPT no lanza procesos locales: necesita una URL pública por HTTPS.

1. Arranca el servidor en modo HTTP:

   ```bash
   PY SRV --http 8765
   ```

   Imprime una URL como `http://127.0.0.1:8765/<token>/mcp`. El token se crea una vez y se guarda en `datos/token.txt`.
2. Publícalo con un túnel, por ejemplo con Cloudflare (`brew install cloudflared`):

   ```bash
   cloudflared tunnel --url http://127.0.0.1:8765
   ```

   Te da una dirección `https://….trycloudflare.com`.
3. En ChatGPT, activa el modo de desarrollador (Settings → Apps & Connectors → Advanced) y crea un conector con la URL `https://….trycloudflare.com/<token>/mcp`, sin autenticación.

   Esos menús pueden cambiar de nombre y el modo de desarrollador depende del plan.

**Seguridad:** la ruta con el token es la única protección, y quien la tenga controla tu navegador con tu sesión de engdis. No compartas la URL, cierra el túnel al terminar y, si la URL se filtra, borra `datos/token.txt` para generar otro token.

### Para cualquier cliente

- Las herramientas largas (`practica_resolver`, `explorar`, `prueba_leer`) se pausan a los 45 s para no pasar los límites de tiempo de los clientes. Devuelven «pausa por tiempo» y basta con volver a llamarlas, porque siguen desde el ítem actual. El límite se cambia con `max_segundos`.
- En los quizzes de dibujos, `prueba_leer` devuelve imágenes, así que el modelo debe aceptar imágenes en los resultados de herramientas.
- Las instrucciones de uso viajan en el campo `instructions` del servidor. Si un cliente no se las pasa al modelo, la herramienta `guia()` devuelve el mismo texto (pídele al agente que la llame primero).

## Primer uso

1. Pídele al agente `abrir()`. Se abrirá una ventana de Chrome con un perfil propio, guardado en `datos/perfil_navegador`.
2. Inicia sesión tú mismo en esa ventana. La sesión queda guardada para las siguientes veces.

## Herramientas

| Herramienta | Qué hace |
|---|---|
| `guia()` | Devuelve las instrucciones de uso (para clientes que no muestran `instructions`) |
| `abrir(url)` | Lanza el navegador y navega; avisa si falta iniciar sesión |
| `cursos(curso)` | Lista los cursos de My Progress y el avance de las unidades del curso abierto; con `curso` (parte del nombre) abre antes ese curso |
| `ir_leccion(unidad, leccion, curso)` | Abre la lección; sin `curso` usa el curso activo, con `curso` (parte del nombre) entra por My Progress |
| `unit_test_abrir(unidad, curso)` | Abre la ventana del Unit Test desde My Progress |
| `ir_paso(paso)` | Cambia de paso por número (`"2"`) o parte del nombre (`practice`, `vocabulary`, `quiz`) |
| `ir_item(n)` | Salta al ítem n del paso (0 = introducción) |
| `estado()` | Texto útil del ítem (sin el menú lateral), posición, ítems pendientes, audios, desplegables y respuestas marcadas |
| `clic(x, y \| texto)` | Clic por coordenadas (ventana de 1512x806) o por texto exacto |
| `escribir(texto, x, y \| campo, limpiar)` | Escribe con el teclado real (pulsación a pulsación) en el campo de texto visible nº `campo` o tras un clic en (x, y) |
| `aviso(boton)` | Pulsa `Continue` o `Stay in this step` en el aviso emergente "Reminder!" (está en un iframe y `clic(texto=...)` no lo alcanza) |
| `captura(x0, y0, x1, y1)` | JPEG de una región; solo si hace falta ver una imagen |
| `js(codigo)` | Ejecuta JavaScript en la página (vía de escape) |
| `usar_pestana(i)` | Cambia la pestaña activa (las ventanas emergentes se toman solas) |
| `transcribir(url, inicio, fin, vad)` | Transcribe un audio con faster-whisper; rellena solo los huecos que whisper se salta y guarda caché |
| `escuchar()` | Transcribe todos los audios de la página en una llamada |
| `explorar()` | Paso Explore: pulsa el play del reproductor de la plataforma, espera a que el ítem quede marcado como visto y avanza (si no hay ese reproductor, reproduce el medio completo) |
| `practica_resolver()` | Paso Practice: por ítem, ojo → leer → reiniciar → poner las respuestas → Check → siguiente. Cubre desplegables, arrastres (motor nuevo, motor antiguo y tablas), opción múltiple (también con imágenes como opciones), seleccionar palabras del texto ("Select Text"), texto, el juego de memoria, las respuestas habladas ("What's the best response" con micrófono), las casillas de selección múltiple ("Which facts are true") y los ejercicios de ordenar. Los de escritura libre los anota para `practica_escribir`. Si un ítem es de un tipo desconocido lo anota y sigue (`saltar_desconocidos`) |
| `practica_escribir(texto)` | Ítem de escritura libre ("Type your answer in the box and click Done"): escribe el texto en el editor (TinyMCE en iframe o textarea) con un clic real de ratón en el recuadro, comprueba que el texto entró y pulsa Done. El texto lo redacta el agente según la consigna |
| `practica_boton(boton)` | Pulsa `reiniciar`, `ver`, `check` o `siguiente` |
| `practica_opciones()` / `practica_elegir([...])` | Lista las opciones de los desplegables o las elige |
| `arrastrar(texto, hueco)` | Arrastra una ficha a un hueco (para tests de lección) |
| `prueba_iniciar()` / `prueba_enviar()` | Inicia y envía el test o quiz de lección (`prueba_enviar` va antes al último ítem, que es donde está Submit) |
| `prueba_leer()` | Recorre todo el quiz y devuelve por ítem el texto completo (si el pasaje se repite, solo la pregunta y las opciones), las opciones de cada desplegable (los abre uno a uno), la transcripción (si sale casi vacía o mucho más corta que el video, la repite sin VAD) y, si las opciones son dibujos, una captura |
| `prueba_responder({...})` | Marca las respuestas del test: `{"1": "B"}` (letra o texto; con respuestas preparadas antes usa siempre el texto, ver «Opciones barajadas»), `{"2": ["were", "replaced"]}` (arrastre, fichas en orden de huecos) o `{"1.3": "A"}` (ítem con varias preguntas) |
| `speaking_conversacion(personaje)` | Hace sola una conversación de speaking (paso Interact): elige el personaje (`derecha`/`izquierda`), pulsa Start, dice cada línea con TTS y reintenta si sale "Try again". También resuelve las conversaciones ramificadas (Interact 2) |
| `leccion_completa(unidad, leccion, curso, tests)` | Pasada completa de una lección: Explore, Practice, speaking y (con `tests="leer"`) deja el test leído en `datos/quiz/<curso>_U<u>L<l>.txt`; al final revisa los pendientes reales |
| `barrido_iniciar(lecciones, curso, tests)` / `barrido_estado(desde)` / `barrido_parar()` | `leccion_completa` para varias lecciones (`["2:1", "2:2"]`) en segundo plano, sin límite de tiempo del cliente; el estado devuelve el log |
| `pendientes(lecciones, curso)` | Revisa sin tocar nada los ítems pendientes de cada paso de esas lecciones |
| `speaking_frase(frase)` | Genera la frase con TTS y la inyecta como micrófono en el próximo "Speak now" |
| `speaking_apagar()` | Detiene la inyección |
| `test_iniciar()` | Pulsa Start en la bienvenida del Unit Test |
| `test_responder({"1": "B"})` | Marca respuestas y devuelve lo que quedó marcado |
| `test_siguiente()` | Pulsa Next y devuelve el estado de la nueva pantalla |
| `test_pasaje()` | Lee el texto de lectura y, si es imagen, lo devuelve en capturas por tramos |
| `test_enviar()` | Pulsa Submit y Yes y devuelve la nota |

El servidor le pasa al agente un resumen del flujo de trabajo en `instructions`, así que no hace falta explicárselo en cada sesión.

## Probarlo sin agente

`probar.py` arranca el servidor por stdio y llama herramientas en orden; las imágenes que recibe las guarda en `datos/`:

```bash
.venv/bin/python probar.py listar
.venv/bin/python probar.py login            # abre el navegador y espera a que inicies sesión
.venv/bin/python probar.py ir_leccion '{"unidad":2,"leccion":5}' ir_paso '{"paso":"practice"}' ir_item '{"n":1}' practica_resolver '{}'
```

## Lo aprendido de la plataforma

- **"See Answer" no cuenta como progreso.** Hay que poner las respuestas y pulsar Check; `practica_resolver` ya lo hace así.
- **Después de Check las respuestas se bloquean.** Para corregir, primero pulsa `reiniciar`.
- **Explore:** el progreso lo registra el reproductor de la plataforma (`.layout__mediaPlayPause`, clase `CTrackerPlayBtnD`) cuando se pulsa su play con un clic real: el ítem queda hecho al empezar a sonar, sin esperar al final. Con `play()` por JS el audio suena completo pero el ítem **no** cuenta (así fallaban los Explore de audio/video del TOEIC Course 2, U7-U8). `explorar()` ya pulsa ese botón; solo si no existe usa el método antiguo.
- **`items_pendientes` justo después de terminar un paso puede estar desfasado:** la marca `--done` a veces se actualiza al salir del ítem. Antes de repetir nada, vuelve a mirarlo entrando de nuevo al paso.
- **Tests de lectura (TOEIC Course 2):** el pasaje va antes de la pregunta y puede pasar de 1200 caracteres; `prueba_leer` lee el texto completo y, si el pasaje es el mismo del ítem anterior, devuelve solo lo nuevo.
- **Tests de un solo ítem con varias preguntas** (p. ej. "Read the announcement and answer the questions"): los radios se llaman `question-N_answer-M`. Lo más seguro es marcarlos con `js()` filtrando por `name^="question-N_"` y comprobar `checked`. Si el texto es una imagen alta, la captura de `prueba_leer` puede cortarla: descarga la imagen (`img.src` en static.engdis.com) y léela entera.
- **Tests de arrastre:** no hay ojo. Lista las fichas por ítem con `js()` (`ed-la-dndzone` → `.dnditem`; en el motor antiguo, `.draggable` y `.droptarget`) y usa `arrastrar(texto, hueco)` hueco por hueco; devuelve cómo quedaron.
- **Botón verde de los tests:** dice Next en todos los ítems menos el último, donde es Submit.
- **Tests repetibles:** en el TOEIC Course 2 la portada del test vuelve a mostrar "Start Test" aunque ya esté hecho; para saber si cuenta, mira My Progress (`cursos(curso=...)`).
- **Respuesta hablada en Practice** ("What's the best response to the statement?" con micrófono y Start): la opción correcta ya viene marcada en el DOM con la clase `c` (`div.lessonMultipleAnswer.c`, texto en `.multiTextInline`). `practica_resolver` la dice con TTS, pulsa Start y espera a que el ítem quede hecho.
- **Casillas "Which facts are true...?"** (`div.lessonMultipleAnswer` sin input): también traen la clase `c` en las correctas, pero no tienen micrófono. La seleccionada añade la clase `check`; se marcan con clics reales sobre el texto y luego Check. Para distinguirlas de la respuesta hablada, se mira si hay botón Start o texto de reconocimiento de voz.
- **Ordenar** ("put these headlines into the right order"): cada `ed-la-dndzone` tiene una sola ficha y no hay banco (antes el detector de banco las tomaba todas por banco). El ojo muestra el orden; tras reiniciar, se arrastra cada ficha a su posición, comprobando después de cada movimiento.
- **Escritura libre** ("Type your answer in the box and click Done"): editor TinyMCE dentro de un iframe (`td.mceIframeContainer iframe`). No tiene respuesta en el ojo: el agente redacta según la consigna y usa `practica_escribir`.
- **Elegir personaje en speaking:** las flechas son `a.chooseCaracterArrow` (la foto es un fondo CSS, no un `<img>`).
- **Conversación ramificada (Interact 2):** dice "Click 'Start' to begin the conversation." y "Take part in a branching conversation". No se elige personaje. En cada turno salen la pregunta del otro, el botón Start/Try again y 2-3 respuestas (`div.recordingPanelSentence`, sin clase `c`). Cualquiera vale: al decirla sale "Good! Let's move on..." y sigue la rama. `speaking_conversacion` dice la primera opción sin el guion inicial.
- **Lecturas en Explore:** el ítem queda visto al abrirlo; `explorar` ya no reproduce la narración si el ítem está hecho, y si la reproduce, la corta en cuanto se marca.
- **Opciones barajadas en los tests:** el orden de las opciones cambia cada vez que se abre un test. Una letra leída en una apertura anterior (o en `datos/quiz/`) apunta a otra opción, y así salió un 0%. Lee el test en la misma apertura (`prueba_iniciar` → `prueba_leer`) y responde por texto. Si sale menos de 100%, `Review` marca la correcta con la clase `.correct`, y los tests de lección se pueden repetir desde "Start Test".
- **Desplegables en los tests:** las opciones de `ed-select-options` no están en el DOM hasta abrir el desplegable; `prueba_leer` las abre con `practica_opciones`.
- **Audios de video en los tests:** con VAD, whisper a veces descarta casi todo el audio de un video y devuelve "Thank you." o "Thanks for watching!". `prueba_leer` lo repite sin VAD; a mano, `transcribir(url, vad=False)` con la URL del `<source>`.
- **Escritura libre con botones Save/Edit text:** es el mismo TinyMCE (`mce_N_ifr`), pero el clic del localizador sobre el iframe no siempre deja el foco dentro y las teclas se pierden; `practica_escribir` hace un clic de ratón por coordenadas y comprueba el texto antes de pulsar Done.
- **My Progress tarda en pintar las unidades** justo después de abrir el navegador: `ir_leccion(..., curso=)` espera hasta 10 s antes de responder "No existe la unidad N (hay 0)".
- **Pasada limpia:** usa `barrido_iniciar([...], curso)` y consulta `barrido_estado()`; cada lección termina con una línea `pendientes:` hecha volviendo a entrar en cada paso, así que lo que diga ahí es lo que de verdad falta. Los tests quedan leídos en `datos/quiz/` para responderlos con `prueba_responder` + `prueba_enviar`.
- **Speaking:** tras elegir personaje (flecha junto a la foto) y pulsar Start, el ejercicio pasa por "Prepare to speak..." y "Speak now.". La línea que toca decir es la del personaje elegido; carga cada línea con `speaking_frase` antes de su turno. Si sale "Try again", carga la línea correcta y pulsa `clic(texto="Try again")`. El ítem queda hecho con la primera línea reconocida.
- **Menú de pasos:** al abrirlo deja una capa gris (`utils__siteOverlay`) que tapa los botones; un clic sobre ella la cierra. El servidor la cierra solo.
- **Arrastres:**
  - Con dragula (motor nuevo) hacen falta movimientos de mouse espaciados; un movimiento de golpe solo selecciona texto.
  - Si hay texto seleccionado en la página, Chrome arrastra el texto y no la ficha.
  - En el motor antiguo, el ojo coloca la ficha encima del hueco con posición absoluta; se detecta por geometría.
  - Los paneles con `mCustomScrollbar` no responden a `scrollIntoView`; se desplazan con la rueda del mouse (tanto el del hueco como el del banco).
  - El banco se marca distinto según la plantilla (`q-index=bank`, `dndBank`, `wordsBankWrapper` o nada). Cuando no hay marca, se identifica reiniciando el ítem: las zonas que quedan con fichas son el banco.
  - En las columnas de clasificación el orden de las fichas no importa.
- **Barra de práctica:** los botones (Clear, See Answer, Check Answer) cambian de sitio según el ítem; se buscan por su etiqueta.
- **Juego de memoria:** las tarjetas solo se voltean con clics reales del mouse; el componente guarda el par de cada tarjeta, pero no actualiza sus banderas, así que el fin se comprueba en el paginador.
- **Opciones con imágenes:** no tienen radio; se marcan con un clic en la imagen (`prMCQ__answers--checked`), y el ojo añade `prMCQ__answers--correct`.
- **Select Text:** las palabras marcables son `.learning__selectTxt_st`; el ojo usa `--seeAnswer` y la selección `--selected`.
- **Cambiar de paso con actividades sin terminar** abre un aviso "Reminder!". Es una colorbox con un iframe (`#colorBoxIframe` → `Runtime/MsgPage.html`), así que su texto no aparece en `document.body` de la página ni lo encuentra `querySelector`. `ir_paso` pulsa Continue dentro de ese iframe, `practica_resolver` cierra con "Stay in this step" un aviso que haya quedado abierto (taparía los arrastres) y `aviso(boton)` lo hace a mano.
- **Campos de texto abiertos (`prOpenEnded`, "Complete the sentences by writing...")**: si se rellenan con `fill()` o por JS, Check los da por buenos (aparecen los ✔), pero el ítem no queda marcado como hecho. Hace falta teclear de verdad; `practica_resolver` ya teclea los textos y `escribir()` sirve para hacerlo a mano.
- **My Progress:** los enlaces de cada unidad son pasos (numerados 1, 2…); cada lección empieza donde vuelve el 1.
- **Títulos de curso en My Progress:** traen saltos de línea y espacios duros entre palabras ("TOEIC®\nOfficial…Course 2"); `ir_leccion(..., curso=)` normaliza los espacios antes de comparar, así que basta con `curso="Course 2"` o `curso="Bridge"`.
- **Quizzes sin "Start Test":** algunos pasos llamados Quiz (p. ej. Bridge U4 L5) son ítems de práctica con Check; se hacen con `practica_resolver()`. La portada de los quizzes normales tarda en pintarse: si `ir_paso` no muestra "Start Test", espera y vuelve a llamar a `estado()` antes de decidir.
- **Con red lenta** un ítem puede tardar en pintarse; `practica_resolver` espera hasta 15 s a que aparezca algo que responder.
- **Opción múltiple:** el ojo no marca el radio, añade la clase `prMCQ__answers--correct` a la opción correcta.
- **Unit Test:**
  - Se abre en una ventana emergente y el servidor cambia a ella automáticamente.
  - Los audios están en `Media/Units/NNNNN.mp3` o en `Media/Listening/<id>/<id>.mp3`.
  - Las lecturas a veces son imágenes; en ese caso usa `test_pasaje()`.
- **Abrir una lección recarga todo el documento**; el servidor reintenta las lecturas que coinciden con la recarga.
- **La URL de `learningArea` no se puede abrir directamente** (da 404); hay que entrar desde el inicio con `ir_leccion`.

## Variables de entorno

| Variable | Por defecto | Para qué |
|---|---|---|
| `ENGDIS_DATA` | `./datos` | Perfil del navegador y caché de audios |
| `ENGDIS_CHANNEL` | `chrome` | `""` para usar el Chromium de Playwright |
| `ENGDIS_WHISPER` | `small.en` | Modelo de whisper (`base.en` es más rápido, `medium.en` más preciso) |

Las coordenadas de los botones de práctica y del test están pensadas para una ventana de 1512x806. Si la plataforma cambia de diseño, ajústalas en las constantes del principio de `server.py`.

#!/usr/bin/env python3
# Servidor MCP personal para la práctica de English Discoveries (ed.engdis.com) y los
# Unit Tests de ets.toeicolpc.com. Maneja su propio navegador con Playwright y devuelve
# texto compacto en lugar de capturas, para gastar menos tokens.
import asyncio
import base64
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

try:  # SDK de MCP 2.x
    from mcp.server.mcpserver import Image, MCPServer as FastMCP
except ImportError:  # SDK de MCP 1.x
    from mcp.server.fastmcp import FastMCP, Image
from playwright.async_api import async_playwright

BASE = Path(__file__).resolve().parent
DATOS = Path(os.environ.get("ENGDIS_DATA", BASE / "datos"))
PERFIL = DATOS / "perfil_navegador"
AUDIO = DATOS / "audio"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
CANAL = os.environ.get("ENGDIS_CHANNEL", "chrome")  # "chrome" usa el Chrome instalado; "" usa el Chromium de Playwright
MODELO_WHISPER = os.environ.get("ENGDIS_WHISPER", "small.en")
INICIO = "https://ed.engdis.com/ucb#/home"
HOSTS_AUDIO = ("https://static.engdis.com/", "https://ets.toeicolpc.com/")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# las coordenadas de los botones dependen de este tamaño de ventana
VIEWPORT = {"width": 1512, "height": 806}
# barra inferior de la práctica (y=783): reiniciar, ver respuesta (ojo), Check
PRACTICA_Y = 783
PRACTICA_X = {"reiniciar": 1177, "ver": 1263, "check": 1345}
# test de lección (paso 3)
PRUEBA_START = (755, 434)
PRUEBA_SUBMIT = (1435, 783)
# botones del Unit Test (ets.toeicolpc.com)
TEST_START = (755, 512)
TEST_NEXT = (1205, 653)
TEST_SUBMIT = (755, 713)
TEST_YES = (658, 432)
TEST_PASAJE = (543, 400)  # un punto dentro del panel izquierdo (lectura o imagen)

INSTRUCCIONES = """\
English Discoveries (cualquier curso y unidad de ed.engdis.com) y sus Unit Tests. Ahorra tokens: usa estado() (texto) y no captura()
salvo para fotos o algo que el texto no explique.
Navegación: abrir() (si pide login, el usuario lo hace a mano en la ventana) → cursos() →
  ir_leccion(u, l, curso="parte del nombre"; vacío = curso activo) →
  ir_paso(número o parte del nombre; los pasos cambian según el curso) → ir_item(n).
  estado() incluye los pasos de la lección e items_pendientes del paso.
Lección completa:
- Pasos de presentación (Explore, "Focusing on..."): ir_item(1) y explorar(); pulsa el play real de la
  plataforma (con play() por JS no cuenta) y avanza en cuanto el ítem queda visto.
- Pasos de práctica (Practice, "Practicing..."): ir_item(1) y practica_resolver(); resuelve desplegables, arrastres, opciones y texto
  (ojo → reiniciar → poner → Check). "See Answer" solo NO cuenta. Si se detiene en un ítem
  desconocido, resuélvelo con estado/clic/arrastrar + practica_boton("check") y sigue.
- Test de lección (Test, "Quiz"): prueba_iniciar() → prueba_leer() (todos los ítems con transcripción y,
  si las opciones son dibujos, captura) → prueba_responder({"1":"B"}) → prueba_enviar(). No hay ojo.
  Si un ítem no es de opciones: estado()/practica_elegir/clic(texto=...)/arrastrar.
Avisos "Reminder!" (iframe): aviso("Continue" | "Stay in this step"). Campos de texto: escribir(texto, campo=n)
  teclea de verdad (rellenar por JS no marca el ítem como hecho).
- Speaking (lección 2): speaking_frase("línea exacta") antes de cada "Speak now"; si sale
  "Try again", carga la frase y reintenta.
Si una herramienta larga devuelve "pausa por tiempo", vuelve a llamarla: sigue desde el ítem actual.
Pasada completa (recomendado): barrido_iniciar(["2:1","2:2"], curso) → barrido_estado() hasta "BARRIDO TERMINADO";
  hace Explore, Practice y speaking (speaking_conversacion), deja los tests leídos en datos/quiz/ y termina cada
  lección con "pendientes:". Después responde cada test: ir_leccion → ir_paso("test") → prueba_iniciar →
  prueba_responder({"1":"B", "2":["ficha1","ficha2"], "3.1":"A"}) → prueba_enviar.
Audios: escuchar() transcribe todos los de la página; transcribir(url, inicio, fin) para uno.
Unit Test: unit_test_abrir(u, curso) (se abre en otra ventana y el servidor cambia solo) →
  test_iniciar(); por pantalla estado() → escuchar() / test_pasaje() → test_responder({"1":"B"}) →
  test_siguiente(); al final test_enviar(). Enviar es definitivo: revisa antes.
Tests de arrastre: lista fichas con js() y arrastrar(texto, hueco). Tests de un ítem con varias preguntas:
  radios question-N_answer-M (marca por js). Avance de otro curso: cursos(curso="Course 2").
"""

mcp = FastMCP("engdis", instructions=INSTRUCCIONES)


@mcp.tool()
async def guia() -> str:
    """Llámala primero: explica el flujo de trabajo y qué herramienta usar en cada paso de English Discoveries."""
    return INSTRUCCIONES

_pw = None
_ctx = None
_page = None
_modelo = None


def _nueva_pagina(p):
    # el Unit Test se abre en ventana emergente; se pasa a trabajar en ella
    global _page
    _page = p


async def _pagina():
    global _pw, _ctx, _page
    if _ctx is None:
        PERFIL.mkdir(parents=True, exist_ok=True)
        _pw = await async_playwright().start()
        _ctx = await _pw.chromium.launch_persistent_context(
            str(PERFIL),
            headless=False,
            channel=CANAL or None,
            viewport=VIEWPORT,
            args=[
                "--autoplay-policy=no-user-gesture-required",
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
            ],
        )
        _ctx.on("page", _nueva_pagina)
        _page = _ctx.pages[0] if _ctx.pages else await _ctx.new_page()
    if _page is None or _page.is_closed():
        vivas = [p for p in _ctx.pages if not p.is_closed()]
        _page = vivas[-1] if vivas else await _ctx.new_page()
    return _page


async def _esperar_carga(page, minimo=800):
    # la plataforma es una SPA que sigue pidiendo datos tras el evento load
    await page.wait_for_timeout(minimo)
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass


async def _eval(page, codigo, arg=None, intentos=3):
    # evalúa JS tolerando recargas de página (abrir una lección recarga todo el documento)
    for i in range(intentos):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
            await page.wait_for_function("() => document.body", timeout=15000)
            return await page.evaluate(codigo, arg)
        except Exception as e:
            if i == intentos - 1 or not re.search(r"context was destroyed|navigation|of null|Timeout", str(e)):
                raise
            await _esperar_carga(page, 1500)


def _limpiar_leccion(texto: str) -> str:
    # en learningArea el texto empieza con el menú (lecciones y pasos); el contenido empieza en la
    # última línea "Step N: ..." (la primera es el título del desplegable de pasos)
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    if "Unit Objectives" in lineas:
        cab = next((l for l in lineas if l.startswith("Lesson ")), "")
        pasos = [i for i, l in enumerate(lineas) if re.match(r"Step \d+: ", l)]
        if pasos:
            cuerpo = lineas[pasos[-1]:]
            i = 1
            while i < len(cuerpo) and re.match(r"\d+\. \S", cuerpo[i]) and len(cuerpo[i]) < 60:
                i += 1  # el desplegable de pasos abierto repite la lista
            lineas = ([cab] if cab else []) + cuerpo[:1] + cuerpo[i:]
    salida = []
    for l in lineas:  # líneas repetidas seguidas (la consigna aparece dos veces)
        if not salida or salida[-1] != l:
            salida.append(l)
    # el paginador de ítems ("1 2 3 ... 9") se resume en una línea
    texto = "\n".join(salida)
    return re.sub(r"(?:^|\n)((?:\d+\n){3,})(?=Back\n|Next\n)",
                  lambda m: f"\nítems: 1-{m.group(1).split()[-1]}\n", texto)


def _compactar(texto: str, maximo: int) -> str:
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n\s*\n+", "\n", texto).strip()
    return texto if len(texto) <= maximo else texto[:maximo] + " …"


async def _jpeg(page, clip=None, calidad=60) -> Image:
    datos = await page.screenshot(type="jpeg", quality=calidad, clip=clip)
    return Image(data=datos, format="jpeg")


# ---------------------------------------------------------------- navegación

@mcp.tool()
async def abrir(url: str = INICIO) -> str:
    """Lanza el navegador (si no está abierto) y va a la URL. La sesión se guarda en el perfil local."""
    page = await _pagina()
    await page.goto(url)
    await _esperar_carga(page)
    if "#/login" in page.url:
        return MSG_LOGIN
    return f"{page.url} | {await page.title()}"


# el menú desplegable de pasos o lecciones deja una capa gris que tapa los botones
JS_CERRAR_CAPA = """() => {
  const o = [...document.querySelectorAll('.utils__siteOverlay')].find(e => getComputedStyle(e).display !== 'none');
  if (o) o.click();
  return !!o;
}"""


MSG_LOGIN = ("Sesión no iniciada: pide al usuario que inicie sesión en la ventana del navegador "
             "y luego llama otra vez a abrir().")

JS_MI_PROGRESO = """async (curso) => {
  const LOGIN = '""" + MSG_LOGIN + """';
  // abre My Progress y, si se indica, el curso cuyo título contiene `curso`
  const s = ms => new Promise(r => setTimeout(r, ms));
  if (!location.hash.includes('MyProgress')) {
    let mp;
    for (let i = 0; i < 30 && !mp; i++) {  // en frío la página de inicio tarda en pintar el menú
      if (location.hash.includes('login')) return LOGIN;
      mp = [...document.querySelectorAll('*')].find(e => e.children.length === 0 && e.textContent.trim() === 'My Progress' && e.offsetParent);
      if (!mp) await s(500);
    }
    if (!mp) return 'No encuentro My Progress';
    mp.click(); await s(4000);
  }
  const actual = document.querySelector('.myProgress__mainCourseTitle, .myProgress__header h1, h1')?.textContent.trim() || '';
  const otros = [...document.querySelectorAll('.myProgress__courseTitle')];
  if (!curso) return {actual, otros: otros.map(t => t.textContent.trim().replace(/\\s+/g, ' '))};
  // los títulos traen saltos de línea y espacios duros entre palabras: se normalizan antes de comparar
  const norm = x => x.replace(/\\s+/g, ' ').trim().toLowerCase();
  let t = otros.find(t => norm(t.textContent).includes(norm(curso)));
  // el curso abierto no está en la lista de "otros"; su título tarda en pintarse
  for (let i = 0; i < 20 && !t && !norm(document.body.innerText).includes(norm(curso)); i++) {
    await s(500);
    t = [...document.querySelectorAll('.myProgress__courseTitle')].find(t => norm(t.textContent).includes(norm(curso)));
  }
  if (t) { t.click(); await s(4000); }
  else if (!norm(document.body.innerText).includes(norm(curso))) return 'No encuentro el curso ' + curso;
  return 'ok';
}"""


async def _a_inicio(page):
    if "#/home" not in page.url and "#/MyProgress" not in page.url:
        await page.goto(INICIO)
        await _esperar_carga(page)


@mcp.tool()
async def cursos(curso: str = "") -> dict:
    """Lista los cursos del usuario (los que aparecen en My Progress) con el avance de cada unidad del curso abierto.
    Con `curso` (parte del nombre, p. ej. "Course 2") abre antes ese curso en My Progress y da el avance de sus unidades."""
    page = await _pagina()
    await page.goto(INICIO)
    await _esperar_carga(page)
    if curso:
        ok = await _eval(page, JS_MI_PROGRESO, curso)
        if ok != "ok":
            return {"error": str(ok)}
    r = await _eval(page, JS_MI_PROGRESO, "")
    if isinstance(r, dict):
        texto = await _eval(page, "() => document.body.innerText")
        r["unidades_del_curso_abierto"] = re.findall(r"Unit (\d+)\n(.+)\n(\d+%)", texto)
    return r


@mcp.tool()
async def ir_leccion(unidad: int, leccion: int, curso: str = "") -> str:
    """Abre la lección N de la unidad U. Sin `curso` usa el curso activo de la página de inicio;
    con `curso` (parte del nombre, p. ej. "TOEIC Bridge") entra por My Progress a ese curso."""
    page = await _pagina()
    if curso:
        await page.goto(INICIO)
        await _esperar_carga(page)
        r = await _eval(page, JS_MI_PROGRESO, curso)
        if r != "ok" and str(r).startswith("No encuentro el curso"):
            # el curso abierto no sale en la lista de My Progress: si es el de la página de inicio, se usa ese
            await page.goto(INICIO)
            await _esperar_carga(page)
            norm = lambda x: re.sub(r"\s+", " ", x).strip().lower()
            for _ in range(20):
                if norm(curso) in norm(await _eval(page, "() => document.body.innerText")):
                    return await ir_leccion(unidad, leccion, "")
                await page.wait_for_timeout(500)
        if r != "ok":
            return str(r)
        codigo = """async ([u, l]) => {
          const s = ms => new Promise(r => setTimeout(r, ms));
          // la lista de unidades de My Progress tarda en pintarse (sobre todo recién abierto el navegador)
          let uds = [];
          for (let i = 0; i < 20 && !uds[u - 1]; i++) {
            uds = [...document.querySelectorAll('[ng-click^="myProgressUnitsCtrl.showLesson"]')].filter(e => e.offsetParent);
            if (!uds[u - 1]) await s(500);
          }
          if (!uds[u - 1]) return 'No existe la unidad ' + u + ' (hay ' + uds.length + ')';
          uds[u - 1].click(); await s(2000);
          // cada enlace visible es un paso ("1", "2"...); una lección nueva empieza cuando vuelve el "1"
          const ls = [];
          [...document.querySelectorAll('[ng-click^="myProgressUnitsCtrl.gotoLesson"]')].filter(e => e.offsetParent)
            .forEach(e => { if (e.textContent.trim() === '1' || !ls.length) ls.push(e); });
          if (!ls[l - 1]) return 'No encuentro la lección ' + l + ' (hay ' + ls.length + ')';
          ls[l - 1].click();
          return 'ok';
        }"""
    else:
        await _a_inicio(page)
        if "#/login" in page.url:
            return MSG_LOGIN
        if "#/home" not in page.url:
            await page.goto(INICIO)
            await _esperar_carga(page)
        codigo = """async ([u, l]) => {
          const s = ms => new Promise(r => setTimeout(r, ms));
          const card = document.querySelectorAll('.home__unitW')[u - 1];
          if (!card) return 'No existe la unidad ' + u;
          if (card.querySelector('[class*=lessonsLocked--locked]') && !card.classList.contains('home__unitW--notLocked'))
            return 'La unidad ' + u + ' está bloqueada';
          card.querySelector('.home__units_lessonsTabHandle')?.click(); await s(1500);
          const links = [...document.querySelectorAll('a.home__courseListItemLink')].filter(e => e.offsetParent);
          if (!links[l - 1]) return 'No encuentro la lección ' + l + ' (hay ' + links.length + ')';
          links[l - 1].click();
          return 'ok';
        }"""
    try:
        r = await page.evaluate(codigo, [unidad, leccion])
    except Exception as e:  # el clic recarga el documento y corta la evaluación
        if "context was destroyed" not in str(e):
            raise
        r = "ok"
    if r != "ok":
        return r
    await page.wait_for_url("**/learningArea**", timeout=20000)
    await _esperar_carga(page, 2500)
    return (await estado(400))["texto"]


@mcp.tool()
async def unit_test_abrir(unidad: int, curso: str = "") -> str:
    """Abre la ventana del Unit Test de la unidad (desde My Progress). Luego usa test_iniciar()."""
    page = await _pagina()
    await page.goto(INICIO)
    await _esperar_carga(page)
    r = await _eval(page, JS_MI_PROGRESO, curso)
    if r != "ok" and curso:
        return str(r)
    antes = len(_ctx.pages)
    r = await _eval(page, """async (u) => {
      const s = ms => new Promise(r => setTimeout(r, ms));
      const uds = [...document.querySelectorAll('[ng-click^="myProgressUnitsCtrl.showLesson"]')].filter(e => e.offsetParent);
      if (!uds[u - 1]) return 'No existe la unidad ' + u;
      uds[u - 1].click(); await s(2000);
      const t = [...document.querySelectorAll('[ng-click^="myProgressUnitsCtrl.openUnitTest"]')].filter(e => e.offsetParent)[0];
      if (!t) return 'Esta unidad no tiene Unit Test disponible';
      t.click();
      return 'ok';
    }""", unidad)
    if r != "ok":
        return r
    for _ in range(20):
        if len(_ctx.pages) > antes:
            break
        await asyncio.sleep(0.5)
    p = await _pagina()
    await _esperar_carga(p, 3000)
    return _compactar(await _eval(p, "() => document.body.innerText"), 500)


JS_PASOS = """() => [...document.querySelectorAll('.learning__dropDownListW')]
  .filter(w => !/--lessons/.test(w.className)).flatMap(w => [...w.querySelectorAll('.learning__dropDownList_itemName')])
  .map(e => e.textContent.trim().replace(/\\s+/g, ' '))"""


async def _cerrar_aviso(page, boton: str = "Continue") -> str | None:
    """Los avisos ("Reminder!" al cambiar de paso con ítems sin terminar) son una colorbox con un
    iframe (#colorBoxIframe → MsgPage.html): el texto no está en el documento principal y los
    querySelector de la página no lo ven. Se pulsa el botón dentro de ese iframe.
    Devuelve el texto del aviso si había uno, o None."""
    for _ in range(6):
        for fr in page.frames:
            if "MsgPage.html" not in fr.url:
                continue
            try:
                texto = (await fr.evaluate("() => document.body.innerText")).strip()
                if not texto:
                    continue
                await fr.get_by_text(boton, exact=True).first.click(timeout=3000)
                await page.wait_for_timeout(800)
                return texto
            except Exception:
                continue
        await page.wait_for_timeout(300)
    return None


@mcp.tool()
async def aviso(boton: str = "Continue") -> str:
    """Pulsa un botón del aviso emergente de la plataforma ("Reminder!": `Continue` o `Stay in this step`).
    Ese aviso está en un iframe y clic(texto=...) no lo alcanza."""
    page = await _pagina()
    t = await _cerrar_aviso(page, boton)
    return f"aviso cerrado con «{boton}»: {_compactar(t, 300)}" if t else "no hay aviso abierto"


@mcp.tool()
async def ir_paso(paso: str) -> str:
    """Dentro de una lección, cambia de paso por número ("2") o por parte del nombre
    ("practice", "vocabulary", "quiz"...). Los nombres de los pasos cambian según el curso."""
    page = await _pagina()
    pasos = await _eval(page, JS_PASOS)
    if paso.strip().isdigit():
        nombre = next((n for n in pasos if n.startswith(paso.strip() + ". ")), None)
    else:
        nombre = next((n for n in pasos if paso.lower() in n.lower()), None)
    if not nombre:
        return f"Paso desconocido; pasos de esta lección: {pasos}"
    r = await _eval(page, """async (nombre) => {
      const s = ms => new Promise(r => setTimeout(r, ms));
      const lista = () => [...document.querySelectorAll('.learning__dropDownListW')]
        .filter(w => !/--lessons/.test(w.className))
        .flatMap(w => [...w.querySelectorAll('.learning__dropDownList_itemName')])
        .filter(e => e.textContent.trim().replace(/\\s+/g, ' ') === nombre);
      if (!lista().some(e => e.offsetParent)) {  // abre el desplegable de pasos de la barra superior
        const cab = [...document.querySelectorAll('a,div,span,button')]
          .find(e => e.offsetParent && /^Step \\d+: /.test(e.textContent.trim()) && e.getBoundingClientRect().top < 60);
        cab?.click(); await s(600);
      }
      const vis = lista().find(e => e.offsetParent);
      if (!vis) return 'El paso no está visible';
      (vis.closest('a,li') || vis).click();
      return 'ok';
    }""", nombre)
    if r != "ok":
        return r
    await page.wait_for_timeout(800)
    # si el paso actual no está terminado sale "Reminder!" con Continue / Stay in this step
    await _cerrar_aviso(page, "Continue")
    await _esperar_carga(page, 2000)
    await _eval(page, JS_CERRAR_CAPA)
    return (await estado(500))["texto"]


@mcp.tool()
async def ir_item(n: int) -> str:
    """Dentro de un paso de la lección, salta al ítem n (0 = introducción) con el paginador inferior."""
    page = await _pagina()
    await _eval(page, JS_CERRAR_CAPA)
    r = await _eval(page, """async (n) => {
      const s = ms => new Promise(r => setTimeout(r, ms));
      const objetivo = () => n === 0 ? document.querySelector('.learning__tasksNav_pager_intro')
                                     : document.querySelectorAll('.learning__tasksNav_task')[n - 1];
      let e = objetivo();
      if (!e) return 'No existe el ítem ' + n;
      if (!e.offsetParent) {  // vista de ítem: el paginador está plegado en "N / M"
        document.querySelector('.learning__taskNavPagerCounter')?.click(); await s(600);
        e = objetivo();
      }
      if (!e || !e.offsetParent) return 'El paginador no se abrió';
      window.__mcpBancos = null;
      e.click();
      return 'ok';
    }""", n)
    if r != "ok":
        return r
    await _esperar_carga(page, 1500)
    await _eval(page, JS_CERRAR_CAPA)
    return (await estado(600))["texto"]


@mcp.tool()
async def estado(max_chars: int = 1500) -> dict:
    """Resumen compacto de la página activa: posición del ítem, texto útil, audios y desplegables."""
    page = await _pagina()
    info = await _eval(page, """() => {
      const t = document.body.innerText;
      const audios = [...document.querySelectorAll('audio,source,video')]
        .map(a => a.currentSrc || a.src).filter(s => s && !/soundTest/i.test(s));
      const dd = [...document.querySelectorAll('ed-select-options')]
        .map(d => d.querySelector('.DDLOptions__selected')?.textContent.trim());
      const radios = {};
      document.querySelectorAll('input[type=radio][name^=r]').forEach(r => {
        radios[r.name] ??= null; if (r.checked) radios[r.name] = r;
      });
      const marcadas = Object.fromEntries(Object.entries(radios).map(([n, r]) => {
        if (!r) return [n.slice(1), null];
        const i = [...document.querySelectorAll('input[name=' + n + ']')].indexOf(r);
        return [n.slice(1), 'ABCD'[i]];
      }));
      const pos = (t.match(/(\\d+) \\/ (\\d+)/) || [])[0] || null;
      const tareas = [...document.querySelectorAll('.learning__tasksNav_task')];
      const pendientes = tareas.map((e, i) => e.className.includes('--done') ? 0 : i + 1).filter(Boolean);
      return {url: location.href.split('?')[0], titulo: document.title, pos,
              audios: [...new Set(audios)], desplegables: dd, marcadas, texto: t,
              items_pendientes: tareas.length ? (pendientes.length ? pendientes : 'ninguno') : null};
    }""")
    texto = info.pop("texto")
    if "Choose the correct answer." in texto:  # Unit Test: solo el panel de preguntas
        texto = texto.split("Choose the correct answer.")[-1].split("Mark for Review")[0]
    texto = _limpiar_leccion(texto)
    info["texto"] = _compactar(texto, max_chars)
    if "learningArea" in info["url"]:
        info["pasos"] = await _eval(page, JS_PASOS)
    info["pestanas"] = len([p for p in _ctx.pages if not p.is_closed()])
    return {k: v for k, v in info.items() if v not in (None, [], {})}


@mcp.tool()
async def clic(x: int | None = None, y: int | None = None, texto: str | None = None) -> str:
    """Hace clic por coordenadas (ventana de 1512x806) o en el primer elemento visible con ese texto exacto."""
    page = await _pagina()
    if texto:
        await page.get_by_text(texto, exact=True).first.click(timeout=5000)
    elif x is not None and y is not None:
        await page.mouse.click(x, y)
    else:
        return "Indica x,y o texto"
    await page.wait_for_timeout(1200)
    return f"ok | {page.url.split('?')[0]}"


@mcp.tool()
async def escribir(texto: str, x: int | None = None, y: int | None = None, campo: int | None = None,
                   limpiar: bool = True) -> str:
    """Escribe con el teclado real (pulsaciones una a una). Primero hace clic en (x, y) o enfoca el
    campo de texto visible número `campo` (desde 0); sin ninguno escribe donde esté el foco.
    `limpiar` borra antes lo que hubiera. Útil cuando rellenar por JS no basta."""
    page = await _pagina()
    if campo is not None:
        loc = page.locator("input[type=text]:visible, textarea:visible").nth(campo)
        await loc.click(timeout=5000)
    elif x is not None and y is not None:
        await page.mouse.click(x, y)
    if limpiar:
        await page.keyboard.press("Meta+A")
        await page.keyboard.press("Backspace")
    await page.keyboard.type(texto, delay=40)
    await page.wait_for_timeout(300)
    valor = await _eval(page, "() => document.activeElement?.value ?? ''")
    return f"escrito; el campo vale «{valor}»"


@mcp.tool()
async def captura(x0: int = 0, y0: int = 0, x1: int = 1512, y1: int = 806, calidad: int = 55) -> Image:
    """Captura JPEG de una región. Úsala solo cuando el texto de estado() no baste (fotos, arrastres)."""
    page = await _pagina()
    return await _jpeg(page, {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}, calidad)


@mcp.tool()
async def js(codigo: str) -> str:
    """Ejecuta JavaScript en la página activa (expresión o función async) y devuelve el resultado como texto."""
    page = await _pagina()
    try:
        return str(await _eval(page, codigo, intentos=1))[:4000]  # sin reintentos: repetiría clics
    except Exception as e:  # solo el mensaje, sin traceback
        if "context was destroyed" in str(e):
            await _esperar_carga(page, 1500)
            return f"La página navegó a {page.url.split('?')[0]}"
        return "Error: " + str(e).splitlines()[0][:500]


@mcp.tool()
async def usar_pestana(indice: int = -1) -> str:
    """Cambia la página activa (por defecto la última abierta)."""
    global _page
    await _pagina()
    vivas = [p for p in _ctx.pages if not p.is_closed()]
    _page = vivas[indice]
    await _page.bring_to_front()
    return f"{len(vivas)} pestañas; activa: {_page.url.split('?')[0]}"


# ---------------------------------------------------------------- audio

def _descargar(url: str) -> Path:
    AUDIO.mkdir(parents=True, exist_ok=True)
    ruta = AUDIO / (hashlib.md5(url.encode()).hexdigest() + ".mp3")
    if not ruta.exists():
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://ed.engdis.com/"})
        with urllib.request.urlopen(req) as r:
            ruta.write_bytes(r.read())
    return ruta


def _transcribir(url: str, inicio, fin, vad: bool) -> str:
    global _modelo
    import numpy as np
    from faster_whisper import WhisperModel

    if not url.startswith(HOSTS_AUDIO):
        return f"URL no permitida: {url}"
    try:
        ruta = _descargar(url)
    except Exception as e:  # p. ej. 404
        return f"No se pudo descargar ({e})"
    cmd = [FFMPEG, "-nostdin", "-loglevel", "error"]
    if inicio is not None:
        cmd += ["-ss", str(inicio)]
    if fin is not None:
        cmd += ["-to", str(fin)]
    cmd += ["-i", str(ruta), "-af", "dynaudnorm", "-f", "f32le", "-ac", "1", "-ar", "16000", "-"]
    cache = ruta.with_suffix(f".{inicio}-{fin}-{int(vad)}.txt")
    if cache.exists():
        return cache.read_text()
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    if _modelo is None:
        _modelo = WhisperModel(MODELO_WHISPER, device="cpu", compute_type="int8")

    def pasada(y, con_vad, desde=0.0):
        segs, _ = _modelo.transcribe(
            y, beam_size=5, condition_on_previous_text=False, vad_filter=con_vad,
            no_speech_threshold=None, log_prob_threshold=None, chunk_length=15,
        )
        return [(s.start + desde, s.end + desde, s.text.strip()) for s in segs if s.text.strip()]

    segmentos = pasada(x, vad)
    # whisper a veces se salta frases en los cortes de bloque: se retranscribe cada hueco largo con voz
    dur = len(x) / 16000
    bordes = [0.0] + [v for s in segmentos for v in s[:2]] + [dur]
    for a, b in list(zip(bordes[::2], bordes[1::2])):
        if b - a < 2.5:
            continue
        tramo = x[int(a * 16000):int(b * 16000)]
        if np.sqrt((tramo ** 2).mean()) < 0.01:  # silencio
            continue
        segmentos += pasada(tramo, False, a)
    segmentos.sort()
    desfase = float(inicio or 0)
    texto = "\n".join(f"[{a + desfase:5.1f}-{b + desfase:5.1f}] {t}" for a, b, t in segmentos)
    cache.write_text(texto)
    return texto


@mcp.tool()
async def transcribir(url: str, inicio: float | None = None, fin: float | None = None, vad: bool = True) -> str:
    """Transcribe un audio de engdis/ETS con faster-whisper. Si se salta frases, usa vad=False o un tramo inicio-fin (segundos)."""
    return await asyncio.to_thread(_transcribir, url, inicio, fin, vad)


@mcp.tool()
async def escuchar() -> str:
    """Transcribe todos los audios de la página activa (lo mismo que estado() + transcribir() en una llamada)."""
    page = await _pagina()
    urls = await _eval(page, """() => [...new Set([...document.querySelectorAll('audio,source,video')]
      .map(a => a.currentSrc || a.src).filter(s => s && !/soundTest/i.test(s)))]""")
    if not urls:
        return "No hay audios en la página"
    partes = []
    for u in urls:
        partes.append(f"=== {u.split('?')[0].rsplit('/', 1)[-1]}\n" + await asyncio.to_thread(_transcribir, u, None, None, True))
    return "\n".join(partes)


# ---------------------------------------------------------------- práctica

JS_PRACTICA_BTN = """([x, y]) => {
  // los botones de la barra cambian según el ítem: se buscan por su etiqueta y, si no, por posición
  const etiquetas = {1177: /^(Clear|Reset|Start Over)/i, 1263: /^See Answer/i, 1345: /^Check/i};
  const li = [...document.querySelectorAll('.practiceTools li')]
    .find(l => l.offsetParent && etiquetas[x] && etiquetas[x].test(l.textContent.trim()));
  if (li) { (li.querySelector('a,button') || li).click(); return true; }
  if (document.querySelector('.practiceTools li')) return false;  // la barra existe pero no tiene ese botón
  let e = document.elementFromPoint(x, y);
  while (e && e.tagName !== 'LI' && e.parentElement) e = e.parentElement;
  if (!e || e.tagName !== 'LI') return false;
  (e.querySelector('a,button') || e).click();
  return true;
}"""

@mcp.tool()
async def practica_boton(boton: str) -> str:
    """Pulsa un botón de la barra de práctica: "reiniciar", "ver" (See Answer), "check" o "siguiente"."""
    page = await _pagina()
    await _eval(page, JS_CERRAR_CAPA)
    if boton == "siguiente":
        ok = await _eval(page, "() => { window.__mcpBancos = null; const a = document.querySelector('a.learning__nextItemLink'); if (a) a.click(); return !!a; }")
    elif boton in PRACTICA_X:
        ok = await _eval(page, JS_PRACTICA_BTN, [PRACTICA_X[boton], PRACTICA_Y])
    else:
        return "Botón desconocido"
    await page.wait_for_timeout(1500 if boton == "siguiente" else 800)
    return "ok" if ok else "no encontrado"


# foto de las respuestas visibles del ítem: desplegables, huecos de arrastre, radios/casillas y campos de texto
# el banco de fichas se marca de varias formas según la plantilla; si ninguna aplica, vale lo que
# registró JS_MARCAR_BANCO (las zonas con fichas justo después de reiniciar)
JS_ES_BANCO = ("const _exp = z => z.getAttribute('ng-reflect-q-index') === 'bank' || /bank/i.test(z.className)"
               " || (() => { let p = z.parentElement; for (let i = 0; i < 4 && p; i++, p = p.parentElement)"
               " if (/wordsBank/i.test(p.className)) return true; return false; })();"
               "const esBanco = z => _exp(z) || (window.__mcpBancos || []).includes(z.getAttribute('ng-reflect-container-id'));")

JS_MARCAR_BANCO = """() => {
  """ + JS_ES_BANCO + """
  const zs = [...document.querySelectorAll('ed-la-dndzone')];
  window.__mcpBancos = zs.some(_exp) ? [] : zs.filter(z => z.querySelector('.dnditem'))
    .map(z => z.getAttribute('ng-reflect-container-id'));
  return window.__mcpBancos;
}"""

JS_FOTO = """() => {
  """ + JS_ES_BANCO + """
  const vis = e => e.offsetParent !== null;
  const dd = [...document.querySelectorAll('ed-select-options')]
    .map(d => d.querySelector('.DDLOptions__selected')?.textContent.trim() ?? '');
  const zonas = [...document.querySelectorAll('ed-la-dndzone')]
    .filter(z => !esBanco(z) && vis(z))
    .map(z => [...z.querySelectorAll('.dnditem')].map(i => i.textContent.trim()));
  // motor antiguo: la ficha queda encima del hueco (.droptarget) con posición absoluta
  const centro = e => { const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; };
  const fichas = [...document.querySelectorAll('.wordsBankTable .draggable, .draggable.ui-draggable')].filter(vis);
  const huecos = [...document.querySelectorAll('.droptarget')].filter(vis).map(h => {
    const r = h.getBoundingClientRect();
    return fichas.filter(f => { const [x, y] = centro(f); return x >= r.left - 4 && x <= r.right + 4 && y >= r.top - 6 && y <= r.bottom + 6; })
      .map(f => f.textContent.trim());
  });
  const marcas = [...document.querySelectorAll('input[type=radio],input[type=checkbox]')]
    .filter(vis).map(i => i.checked || !!i.closest('[class*=answers--correct]'));  // el ojo marca la correcta con una clase
  const textos = [...document.querySelectorAll('input[type=text],textarea')].filter(vis).map(i => i.value);
  // opciones que son imágenes: no tienen radio, se marcan con un clic en la imagen
  const fotos = [...document.querySelectorAll('.prMCQ__answers')].filter(o => vis(o) && !o.querySelector('input'))
    .map(o => /prMCQ__answers--(checked|correct)/.test(o.className));
  // "Select Text": palabras marcables del texto (el ojo usa --seeAnswer y la selección --selected)
  const palabras = [...document.querySelectorAll('.learning__selectTxt_st')].filter(vis)
    .map(w => /selectTxt_st--(seeAnswer|selected)/.test(w.className));
  const m = document.body.innerText.match(/(\\d+) \\/ (\\d+)/);
  return {dd, zonas, huecos, marcas, textos, fotos, palabras, pos: m ? +m[1] : null, total: m ? +m[2] : null};
}"""


async def _arrastrar(page, texto: str, zona: int, antiguo: bool = False) -> bool:
    # arrastre real con el mouse (dragula y jQuery UI no responden a eventos sintéticos)
    js_pos = """([texto, zona, antiguo]) => {
      """ + JS_ES_BANCO + """
      const c = e => { const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; };
      let ficha, z;
      if (antiguo) {
        ficha = [...document.querySelectorAll('.wordsBankTable .draggable')].find(i => i.offsetParent && i.textContent.trim() === texto);
        z = [...document.querySelectorAll('.droptarget')].filter(h => h.offsetParent)[zona];
      } else {
        const banco = [...document.querySelectorAll('ed-la-dndzone')].filter(z => esBanco(z));
        ficha = banco.flatMap(b => [...b.querySelectorAll('.dnditem')]).find(i => i.textContent.trim() === texto);
        z = [...document.querySelectorAll('ed-la-dndzone')]
          .filter(z => !esBanco(z) && z.offsetParent)[zona];
        z = z && (z.querySelector('.dndZone') || z);
      }
      if (!ficha || !z) return null;
      window.getSelection().removeAllRanges();  // con texto seleccionado Chrome arrastra el texto, no la ficha
      // paneles con barra propia (mCustomScrollbar) u overflow que pueden tapar el hueco o la ficha
      const caja = e => {
        let k = e.closest('.mCustomScrollBox');
        if (!k) { k = e.parentElement;
          while (k && !(k.scrollHeight > k.clientHeight + 5 && /(auto|scroll)/.test(getComputedStyle(k).overflowY))) k = k.parentElement; }
        const r = k ? k.getBoundingClientRect() : {top: 0, bottom: innerHeight, x: 0, width: innerWidth};
        return [Math.max(r.top, 60), Math.min(r.bottom, innerHeight - 50), r.x + r.width / 2];
      };
      // el hueco puede estar dentro de su panel pero tapado por otra capa (p. ej. el banco de fichas
      // encima de la parte baja del texto): se mira qué elemento hay de verdad en su centro
      const [zx, zy] = c(z), arriba = document.elementFromPoint(zx, zy);
      const tapado = !!arriba && !z.contains(arriba) && !arriba.contains(z);
      return [c(ficha), c(z), caja(ficha), caja(z), tapado];
    }"""
    y_previo = None
    for _ in range(10):
        pos = await _eval(page, js_pos, [texto, zona, antiguo])
        if not pos:
            return False
        (x0, y0), (x1, y1), caja_f, caja_z, tapado = pos
        # primero que se vea la ficha y luego el hueco; la rueda sobre su panel los acerca
        for y, (arriba, abajo, cx) in ((y0, caja_f), (y1, caja_z)):
            if not arriba + 12 < y < abajo - 12:
                await page.mouse.move(cx, (arriba + abajo) / 2)
                await page.mouse.wheel(0, y - (arriba + abajo) / 2)
                await page.wait_for_timeout(500)
                break
        else:
            if tapado and y1 - (caja_z[0] + 40) > 5 and y1 != y_previo:  # si la rueda ya no lo mueve, se suelta igual
                y_previo = y1
                # hueco tapado: se sube hasta la parte alta de su panel, que es la que queda libre
                await page.mouse.move(caja_z[2], caja_z[0] + 20)
                await page.mouse.wheel(0, y1 - (caja_z[0] + 40))
                await page.wait_for_timeout(500)
                continue
            break
    # si tras 10 intentos el hueco sigue sin quedar del todo a la vista, se suelta igual: el
    # arrastre recalcula su posición por el camino
    js_zona = """([zona, antiguo]) => {
      """ + JS_ES_BANCO + """
      let z = antiguo ? [...document.querySelectorAll('.droptarget')].filter(h => h.offsetParent)[zona]
        : [...document.querySelectorAll('ed-la-dndzone')].filter(z => !esBanco(z) && z.offsetParent)[zona];
      z = z && !antiguo ? (z.querySelector('.dndZone') || z) : z;
      if (!z) return null;
      const r = z.getBoundingClientRect();
      return [r.x + r.width / 2, r.y + r.height / 2];
    }"""
    await page.mouse.move(x0, y0)
    await page.mouse.down()
    # dragula necesita movimientos espaciados; con steps de golpe solo selecciona texto.
    # Al pasar por el borde de un panel con scroll, este se desplaza solo (autoscroll) y el hueco
    # cambia de sitio: se recalcula su posición durante el arrastre y antes de soltar
    x, y = x0, y0
    for i in range(1, 21):
        if i in (8, 14, 20):
            nuevo = await _eval(page, js_zona, [zona, antiguo])
            if nuevo:
                x1, y1 = nuevo
        f = 1 / (21 - i)  # lo que falta del camino, repartido entre los pasos que quedan
        x, y = x + (x1 - x) * f, y + (y1 - y) * f
        await page.mouse.move(x, y)
        await page.wait_for_timeout(20)
    for _ in range(3):  # se espera a que el panel deje de moverse y se ajusta al hueco
        await page.wait_for_timeout(200)
        nuevo = await _eval(page, js_zona, [zona, antiguo])
        if not nuevo or (abs(nuevo[0] - x) < 3 and abs(nuevo[1] - y) < 3):
            break
        x, y = nuevo
        await page.mouse.move(x, y, steps=4)
    await page.wait_for_timeout(150)
    await page.mouse.up()
    await page.wait_for_timeout(400)
    return True


async def _aplicar(page, foto) -> str | None:
    # reproduce la foto tomada con el ojo; devuelve un error o None
    for antiguo, clave in ((False, "zonas"), (True, "huecos")):
        for zona, textos in enumerate(foto[clave]):
            for t in textos:
                if not await _arrastrar(page, t, zona, antiguo):
                    return f"no pude arrastrar «{t}»"
    if foto["dd"]:
        await _eval(page, """async (resp) => {
          const s = ms => new Promise(r => setTimeout(r, ms));
          const dd = document.querySelectorAll('ed-select-options');
          for (let i = 0; i < resp.length && i < dd.length; i++) {
            dd[i].querySelector('.DDLOptions__selected').click(); await s(300);
            const o = [...dd[i].querySelectorAll('.DDLOptions__listItem')].find(x => x.textContent.trim() === resp[i]);
            if (o) o.click(); await s(300);
          }
        }""", foto["dd"])
    if any(foto["marcas"]):
        await _eval(page, """(m) => {
          const l = [...document.querySelectorAll('input[type=radio],input[type=checkbox]')].filter(e => e.offsetParent);
          m.forEach((v, i) => { if (l[i] && l[i].checked !== v) (l[i].closest('label') || l[i]).click(); });
        }""", foto["marcas"])
    if any(foto.get("fotos", [])):
        await _eval(page, """(m) => {
          const l = [...document.querySelectorAll('.prMCQ__answers')].filter(o => o.offsetParent && !o.querySelector('input'));
          m.forEach((v, i) => { if (l[i] && v !== l[i].className.includes('--checked')) (l[i].querySelector('img') || l[i]).click(); });
        }""", foto["fotos"])
    if any(foto.get("palabras", [])):
        await _eval(page, """(m) => {
          const l = [...document.querySelectorAll('.learning__selectTxt_st')].filter(w => w.offsetParent);
          m.forEach((v, i) => { if (l[i] && v !== l[i].className.includes('--selected')) l[i].click(); });
        }""", foto["palabras"])
    if any(foto["textos"]):
        # se teclea de verdad: con fill() el valor queda puesto y Check lo da por bueno, pero los
        # campos abiertos (prOpenEnded) no marcan el ítem como hecho si no hubo pulsaciones
        campos = page.locator("input[type=text]:visible, textarea:visible")
        for i, v in enumerate(foto["textos"]):
            if v:
                await campos.nth(i).click(timeout=5000)
                await page.keyboard.press("Meta+A")
                await page.keyboard.press("Backspace")
                await page.keyboard.type(v, delay=30)
    return None


JS_ITEM_HECHO = """() => {
  const m = document.body.innerText.match(/(\\d+) \\/ (\\d+)/);
  const t = m && document.querySelectorAll('.learning__tasksNav_task')[+m[1] - 1];
  return !!t && t.className.includes('--done');
}"""

# juego de memoria: cada tarjeta guarda en su componente Angular el id de su pareja (answerListId)
JS_MEMORIA_DATOS = """() => {
  const datos = c => { try { return (window.ng.getComponent ? ng.getComponent(c) : ng.probe(c).componentInstance).card; }
                       catch (e) { return null; } };
  return [...document.querySelectorAll('card')].map(c => {
    const d = datos(c), r = c.getBoundingClientRect();
    return d && {par: d.answerListId, fin: !!d.end, x: r.x + r.width / 2, y: r.y + r.height / 2};
  });
}"""


async def _memoria(page) -> str:
    cartas = await _eval(page, JS_MEMORIA_DATOS)
    if not cartas or None in cartas:
        return "no puedo leer las tarjetas"
    grupos = {}
    for c in cartas:
        grupos.setdefault(c["par"], []).append(c)
    pares = 0
    for g in grupos.values():
        if len(g) != 2:
            continue
        for c in g:  # el clic sintético no voltea la tarjeta: hace falta el mouse
            await page.mouse.click(c["x"], c["y"])
            await page.wait_for_timeout(700)
        await page.wait_for_timeout(900)
        pares += 1
    # las banderas del componente no se actualizan; el paginador sí marca el ítem como hecho
    await page.wait_for_timeout(1000)
    hecho = await _eval(page, JS_ITEM_HECHO)
    return f"OK ({pares} pares)" if hecho else f"{pares} pares volteados, pero el ítem no quedó como hecho"


# "What's the best response...?" con micrófono: se elige la respuesta diciéndola. La opción correcta
# ya viene marcada en el DOM con la clase `c` (div.lessonMultipleAnswer.c), sin pulsar el ojo.
JS_HABLA_CORRECTA = """() => {
  // solo si el ítem tiene micrófono (botón Start / reconocimiento de voz); si no, son casillas
  const t = document.body.innerText;
  const mic = /Speech Recognition|record the correct answer|Speak now|Try again/.test(t)
    || [...document.querySelectorAll('a,button,div,span')].some(e => e.offsetParent && e.children.length === 0 && e.textContent.trim() === 'Start');
  const c = [...document.querySelectorAll('.lessonMultipleAnswer.c')].find(e => e.offsetParent);
  return mic && c ? (c.querySelector('.multiTextInline') || c).textContent.trim().replace(/\\s+/g, ' ') : null; }"""

# casillas "Which facts are true...?" (div.lessonMultipleAnswer, sin input): las correctas traen la clase `c`
# y la seleccionada añade `check`. Se marcan con clics reales sobre su texto.
JS_CASILLAS = """() => [...document.querySelectorAll('.lessonMultipleAnswer')].filter(e => e.offsetParent).map(e => {
  const r = (e.querySelector('.multiTextInline') || e).getBoundingClientRect();
  return {c: /(^|\\s)c(\\s|$)/.test(e.className), sel: /(^|\\s)check(\\s|$)/.test(e.className),
          x: r.left + 10, y: r.top + r.height / 2, t: e.textContent.trim().replace(/\\s+/g, ' ').slice(0, 50)};
})"""


async def _casillas(page) -> str:
    for _ in range(2):
        ops = await _eval(page, JS_CASILLAS)
        malas = [o for o in ops if o["c"] != o["sel"]]
        if not malas:
            break
        for o in malas:
            await page.mouse.click(o["x"], o["y"])
            await page.wait_for_timeout(300)
    ops = await _eval(page, JS_CASILLAS)
    if any(o["c"] != o["sel"] for o in ops):
        return "DISTINTO: no pude dejar marcadas solo las correctas"
    await _eval(page, JS_PRACTICA_BTN, [PRACTICA_X["check"], PRACTICA_Y])
    await page.wait_for_timeout(900)
    return f"OK ({sum(o['c'] for o in ops)} correctas)"


async def _responder_hablando(page) -> str:
    frase = await _eval(page, JS_HABLA_CORRECTA)
    for intento in range(4):
        for _ in range(30):  # la sesión de reconocimiento de voz tarda en establecerse
            t = await _eval(page, "() => document.body.innerText")
            if "Please wait while" not in t:
                break
            await page.wait_for_timeout(1000)
        await speaking_frase(frase)
        boton = "Try again" if "Try again" in t else "Start"
        try:
            await page.get_by_text(boton, exact=True).first.click(timeout=5000)
        except Exception:
            pass
        for _ in range(25):
            await page.wait_for_timeout(1000)
            if await _eval(page, JS_ITEM_HECHO):
                await speaking_apagar()
                return f"OK «{frase}»"
    await speaking_apagar()
    return f"no se marcó como hecho tras 4 intentos («{frase}»)"


# ordenar (p. ej. "put these newspaper headlines into the right order"): cada ed-la-dndzone tiene una
# ficha y no hay banco; se ordena arrastrando cada ficha a su posición.
JS_ZONAS_TODAS = """() => [...document.querySelectorAll('ed-la-dndzone')].filter(z => z.offsetParent).map(z => {
  const r = (z.querySelector('.dndZone') || z).getBoundingClientRect();
  return {fichas: [...z.querySelectorAll('.dnditem')].map(i => i.textContent.trim()), x: r.left + r.width / 2, y: r.top + r.height / 2};
})"""
JS_ES_ORDENAR = """() => { const z = [...document.querySelectorAll('ed-la-dndzone')].filter(z => z.offsetParent);
  return z.length > 1 && z.every(x => x.querySelectorAll('.dnditem').length === 1); }"""


async def _arrastre_raton(page, x0, y0, x1, y1):
    await _eval(page, "() => window.getSelection().removeAllRanges()")
    await page.mouse.move(x0, y0)
    await page.mouse.down()
    for i in range(1, 21):  # dragula necesita movimientos espaciados
        await page.mouse.move(x0 + (x1 - x0) * i / 20, y0 + (y1 - y0) * i / 20)
        await page.wait_for_timeout(20)
    await page.wait_for_timeout(150)
    await page.mouse.up()
    await page.wait_for_timeout(500)


async def _ordenar(page, objetivo: list[str]) -> bool:
    for _ in range(len(objetivo) * 3):
        zonas = await _eval(page, JS_ZONAS_TODAS)
        actual = [z["fichas"][0] if z["fichas"] else None for z in zonas]
        if actual == objetivo:
            return True
        i = next(k for k, (a, b) in enumerate(zip(actual, objetivo)) if a != b)
        j = actual.index(objetivo[i]) if objetivo[i] in actual else None
        if j is None:
            return False
        await _arrastre_raton(page, zonas[j]["x"], zonas[j]["y"], zonas[i]["x"], zonas[i]["y"])
    return [z["fichas"][0] if z["fichas"] else None for z in await _eval(page, JS_ZONAS_TODAS)] == objetivo


JS_ES_ESCRITURA = """() => /Type your answer in the box and click Done/.test(document.body.innerText)
  && !![...document.querySelectorAll('td.mceIframeContainer iframe, textarea')].find(e => e.offsetParent)"""


@mcp.tool()
async def practica_escribir(texto: str) -> str:
    """Ítem de escritura libre ("Type your answer in the box and click Done"): escribe `texto` en el
    editor (TinyMCE dentro de un iframe, o textarea) con el teclado real y pulsa Done.
    El texto lo redacta el agente según la consigna del ítem (léela con estado())."""
    page = await _pagina()
    editor = page.locator("td.mceIframeContainer iframe:visible").first
    if not await editor.count():
        editor = page.locator("textarea:visible").first
    if not await editor.count():
        return "no encuentro el recuadro de escritura"
    # clic real de ratón en el centro del recuadro: el clic del localizador sobre el iframe
    # a veces no deja el foco dentro del editor y las teclas se pierden
    caja = await editor.bounding_box()
    if caja:
        await page.mouse.click(caja["x"] + caja["width"] / 2, caja["y"] + min(40, caja["height"] / 2))
    else:
        await editor.click(timeout=5000)
    await page.wait_for_timeout(300)
    await page.keyboard.press("Meta+A")
    await page.keyboard.press("Backspace")
    await page.keyboard.type(texto, delay=5)
    await page.wait_for_timeout(500)
    puesto = await _eval(page, """() => { const f = [...document.querySelectorAll('td.mceIframeContainer iframe')].find(e => e.offsetParent);
      return f ? f.contentDocument.body.innerText.trim().length : (document.activeElement.value || '').length }""")
    if not puesto:
        return "no entró el texto en el editor (revisa con captura; prueba escribir(texto, x, y) en el recuadro y luego clic('Done'))"
    await page.get_by_text("Done", exact=True).first.click(timeout=5000)
    for _ in range(10):
        await page.wait_for_timeout(1000)
        if await _eval(page, JS_ITEM_HECHO):
            return "escrito y enviado; ítem hecho"
    return "escrito y enviado, pero el ítem no aparece como hecho (revisa con estado/captura)"


@mcp.tool()
async def practica_resolver(max_items: int = 40, max_segundos: int = 45, saltar_desconocidos: bool = True) -> list[str]:
    """Resuelve los ítems de práctica desde el actual: ojo → leer respuestas → reiniciar → ponerlas
    (desplegables, arrastres, opciones o texto) → Check → siguiente. Se detiene al terminar o en un
    ítem de tipo desconocido (con `saltar_desconocidos` lo anota y sigue con los demás), y devuelve una línea por ítem. Para no pasar el límite de tiempo
    de los clientes MCP se pausa a los `max_segundos`; basta con volver a llamarla."""
    page = await _pagina()
    await _cerrar_aviso(page, "Stay in this step")  # un aviso abierto taparía los arrastres
    await _eval(page, JS_CERRAR_CAPA)
    log = []
    t0 = time.monotonic()
    for _ in range(max_items):
        if log and time.monotonic() - t0 > max_segundos:
            log.append(f"pausa por tiempo ({max_segundos} s): llama otra vez para seguir desde el ítem actual")
            break
        # con red lenta el ítem tarda en pintarse; se espera a que haya algo que responder
        try:
            await page.wait_for_function("""() => [...document.querySelectorAll(
              'ed-select-options, ed-la-dndzone, .droptarget, card, .prMCQ__answers, .lessonMultipleAnswer, .learning__selectTxt_st, input[type=radio], input[type=checkbox], input[type=text], textarea')]
              .some(e => e.offsetParent)""", timeout=15000)
        except Exception:
            pass
        antes = await _eval(page, JS_FOTO)
        pos = f"{antes['pos']}/{antes['total']}" if antes["pos"] else "?"
        if await _eval(page, "() => [...document.querySelectorAll('.lessonMultipleAnswer.c')].some(e => e.offsetParent)") \
                and not await _eval(page, JS_HABLA_CORRECTA):
            r = await _casillas(page)
            log.append(f"{pos}: casillas {r}")
            if antes["pos"] and antes["pos"] == antes["total"]:
                if r.startswith("OK"):
                    log.append("fin de la práctica")
                break
            if not await _eval(page, "() => { window.__mcpBancos = null; const a = document.querySelector('a.learning__nextItemLink'); if (a) a.click(); return !!a; }"):
                log.append("no hay botón siguiente")
                break
            await page.wait_for_timeout(2000)
            continue
        if await _eval(page, JS_HABLA_CORRECTA):
            r = await _responder_hablando(page)
            log.append(f"{pos}: respuesta hablada {r}")
            if antes["pos"] and antes["pos"] == antes["total"]:
                if r.startswith("OK"):
                    log.append("fin de la práctica")
                break
            # si no se reconoció, se sigue con los demás ítems (queda en pendientes)
            if not await _eval(page, "() => { window.__mcpBancos = null; const a = document.querySelector('a.learning__nextItemLink'); if (a) a.click(); return !!a; }"):
                log.append("no hay botón siguiente")
                break
            await page.wait_for_timeout(2000)
            continue
        if await _eval(page, "() => !!document.querySelector('card .memoG__cardIW')"):
            r = await _memoria(page)
            log.append(f"{pos}: memoria {r}")
            if not r.startswith("OK") or (antes["pos"] and antes["pos"] == antes["total"]):
                break
            if not await _eval(page, "() => { window.__mcpBancos = null; const a = document.querySelector('a.learning__nextItemLink'); if (a) a.click(); return !!a; }"):
                log.append("no hay botón siguiente")
                break
            await page.wait_for_timeout(2000)
            continue
        if await _eval(page, "() => !!document.querySelector('ed-la-dndzone')"):
            # tras reiniciar todas las fichas están en el banco: así se sabe cuál es
            await _eval(page, JS_PRACTICA_BTN, [PRACTICA_X["reiniciar"], PRACTICA_Y])
            await page.wait_for_timeout(900)
            await _eval(page, JS_MARCAR_BANCO)
        if await _eval(page, JS_ES_ESCRITURA):
            log.append(f"{pos}: escritura libre: redacta según la consigna y usa practica_escribir(texto)")
            if antes["pos"] and antes["pos"] == antes["total"]:
                break
            if not await _eval(page, "() => { window.__mcpBancos = null; const a = document.querySelector('a.learning__nextItemLink'); if (a) a.click(); return !!a; }"):
                break
            await page.wait_for_timeout(2000)
            continue
        if await _eval(page, JS_ES_ORDENAR):
            await _eval(page, JS_PRACTICA_BTN, [PRACTICA_X["reiniciar"], PRACTICA_Y])
            await page.wait_for_timeout(900)
            await _eval(page, JS_PRACTICA_BTN, [PRACTICA_X["ver"], PRACTICA_Y])
            await page.wait_for_timeout(900)
            objetivo = [z["fichas"][0] for z in await _eval(page, JS_ZONAS_TODAS)]
            await _eval(page, JS_PRACTICA_BTN, [PRACTICA_X["reiniciar"], PRACTICA_Y])
            await page.wait_for_timeout(900)
            ok = await _ordenar(page, objetivo)
            await _eval(page, JS_PRACTICA_BTN, [PRACTICA_X["check"], PRACTICA_Y])
            await page.wait_for_timeout(900)
            log.append(f"{pos}: ordenar " + ("OK" if ok else f"DISTINTO, quería {objetivo}"))
            if not ok or (antes["pos"] and antes["pos"] == antes["total"]):
                if ok:
                    log.append("fin de la práctica")
                if antes["pos"] and antes["pos"] == antes["total"]:
                    break
            if not await _eval(page, "() => { window.__mcpBancos = null; const a = document.querySelector('a.learning__nextItemLink'); if (a) a.click(); return !!a; }"):
                log.append("no hay botón siguiente")
                break
            await page.wait_for_timeout(2000)
            continue
        await _eval(page, JS_PRACTICA_BTN, [PRACTICA_X["ver"], PRACTICA_Y])
        await page.wait_for_timeout(900)
        foto = await _eval(page, JS_FOTO)
        if not any(any(foto[k]) for k in ("dd", "zonas", "huecos", "marcas", "textos", "fotos", "palabras")):
            diag = await _eval(page, """() => ({zonas: document.querySelectorAll('ed-la-dndzone').length,
              fichas: document.querySelectorAll('.dnditem, .draggable').length,
              desplegables: document.querySelectorAll('ed-select-options').length,
              inputs: document.querySelectorAll('input:not([type=hidden]), textarea').length,
              bancos: window.__mcpBancos})""")
            await _eval(page, JS_PRACTICA_BTN, [PRACTICA_X["reiniciar"], PRACTICA_Y])
            if not saltar_desconocidos or (antes["pos"] and antes["pos"] == antes["total"]):
                log.append(f"{pos}: tipo de ítem no reconocido, me detengo (usa estado/captura). Elementos: {diag}")
                break
            # se anota y se sigue con el resto del paso, para que un ítem raro no deje los demás sin hacer
            log.append(f"{pos}: tipo de ítem no reconocido, lo salto (resuélvelo a mano). Elementos: {diag}")
            if not await _eval(page, "() => { window.__mcpBancos = null; const a = document.querySelector('a.learning__nextItemLink'); if (a) a.click(); return !!a; }"):
                break
            await page.wait_for_timeout(2000)
            continue
        claves = ("dd", "zonas", "huecos", "marcas", "textos", "fotos", "palabras")
        # en las columnas de clasificación el orden de las fichas no importa
        norm = lambda f, k: [sorted(x) for x in f[k]] if k in ("zonas", "huecos") else f[k]
        for intento in range(2):  # un arrastre que no entra suele entrar al repetir el ítem desde cero
            await _eval(page, JS_PRACTICA_BTN, [PRACTICA_X["reiniciar"], PRACTICA_Y])
            await page.wait_for_timeout(900)
            if intento and await _eval(page, "() => !!document.querySelector('ed-la-dndzone')"):
                await _eval(page, JS_MARCAR_BANCO)
            error = await _aplicar(page, foto)
            puesto = await _eval(page, JS_FOTO)
            igual = all(norm(puesto, k) == norm(foto, k) for k in claves)
            if igual and not error:
                break
        await _eval(page, JS_PRACTICA_BTN, [PRACTICA_X["check"], PRACTICA_Y])
        await page.wait_for_timeout(900)
        detalle = "; ".join(f"{k}: quería {foto[k]} y quedó {puesto[k]}" for k in claves if norm(puesto, k) != norm(foto, k))
        log.append(f"{pos}: " + (error or ("OK" if igual else "DISTINTO, " + detalle)))
        ultimo = foto["pos"] and foto["pos"] == foto["total"]
        if ultimo or ((error or not igual) and not saltar_desconocidos):
            if ultimo and igual and not error:
                log.append("fin de la práctica")
            break
        # si el ítem quedó mal se anota (saldrá en pendientes) y se sigue con los demás
        if not await _eval(page, "() => { window.__mcpBancos = null; const a = document.querySelector('a.learning__nextItemLink'); if (a) a.click(); return !!a; }"):
            log.append("no hay botón siguiente")
            break
        await page.wait_for_timeout(2000)
    return log


@mcp.tool()
async def practica_opciones() -> list[list[str]]:
    """Lista las opciones de cada desplegable del ítem actual."""
    page = await _pagina()
    return await _eval(page, """async () => {
      const sleep = ms => new Promise(r => setTimeout(r, ms)); const out = [];
      for (const d of [...document.querySelectorAll('ed-select-options')].filter(d => d.offsetParent)) {
        d.querySelector('.DDLOptions__selected').click(); await sleep(300);
        out.push([...d.querySelectorAll('.DDLOptions__listItem')].map(x => x.textContent.trim()));
        d.querySelector('.DDLOptions__selected').click(); await sleep(200);
      }
      return out;
    }""")


@mcp.tool()
async def practica_elegir(respuestas: list[str]) -> list[str]:
    """Elige en orden una opción por desplegable (texto exacto). Devuelve lo que quedó seleccionado."""
    page = await _pagina()
    return await _eval(page, """async (resp) => {
      const sleep = ms => new Promise(r => setTimeout(r, ms));
      const dd = [...document.querySelectorAll('ed-select-options')].filter(d => d.offsetParent);
      for (let i = 0; i < resp.length && i < dd.length; i++) {
        dd[i].querySelector('.DDLOptions__selected').click(); await sleep(300);
        const o = [...dd[i].querySelectorAll('.DDLOptions__listItem')].find(x => x.textContent.trim() === resp[i]);
        if (o) o.click(); await sleep(300);
      }
      return [...document.querySelectorAll('ed-select-options')].filter(d => d.offsetParent)
        .map(d => d.querySelector('.DDLOptions__selected').textContent.trim());
    }""", respuestas)


@mcp.tool()
async def arrastrar(texto: str, hueco: int) -> str:
    """Arrastra la ficha del banco con ese texto al hueco número `hueco` (1 = el primero de la página).
    Sirve para los tests de lección, donde no hay botón de ver respuesta."""
    page = await _pagina()
    antiguo = await _eval(page, "() => !document.querySelector('ed-la-dndzone') && !!document.querySelector('.droptarget')")
    if not antiguo and not await _eval(page, "() => (window.__mcpBancos || []).length > 0"):
        await _eval(page, JS_MARCAR_BANCO)  # válido si se llama antes del primer arrastre del ítem
    ok = await _arrastrar(page, texto, hueco - 1, antiguo)
    if not ok:
        return f"No encontré la ficha «{texto}» o el hueco {hueco}"
    foto = await _eval(page, JS_FOTO)
    return f"huecos: {foto['huecos'] if antiguo else foto['zonas']}"


@mcp.tool()
async def explorar(max_items: int = 20, max_segundos: int = 45) -> list[str]:
    """Paso Explore: desde el ítem actual, reproduce completo el audio o video de cada ítem
    (necesario para que cuente) y avanza. Devuelve una línea por ítem."""
    page = await _pagina()
    await _eval(page, JS_CERRAR_CAPA)
    log = []
    t0 = time.monotonic()
    for _ in range(max_items):
        if log and time.monotonic() - t0 > max_segundos:
            log.append(f"pausa por tiempo ({max_segundos} s): llama otra vez para seguir desde el ítem actual")
            break
        # el progreso lo registra el reproductor de la plataforma (CTracker) al pulsar su botón de
        # play con un clic real: basta con que empiece a sonar, no hace falta oírlo entero.
        # Con play() por JS el audio suena completo pero el ítem no cuenta.
        boton = page.locator(".layout__mediaPlayPause--play:visible, .CTrackerPlayBtnD:visible").first
        if await boton.count():
            pos = await _eval(page, "() => (document.body.innerText.match(/(\\d+) \\/ (\\d+)/) || [])[0] || '?'")
            await boton.click(timeout=5000)
            hecho = False
            for _ in range(30):
                await page.wait_for_timeout(500)
                if await _eval(page, JS_ITEM_HECHO):
                    hecho = True
                    break
            await _eval(page, "() => document.querySelectorAll('audio,video').forEach(a => a.pause())")
            r = f"{pos}: reproductor de la plataforma, " + ("marcado como visto" if hecho else "NO se marcó como visto")
        else:
            r = None
        if r is None and await _eval(page, JS_ITEM_HECHO):
            # lecturas: el ítem se marca al abrirlo; no hace falta oír la narración entera
            pos = await _eval(page, "() => (document.body.innerText.match(/(\\d+) \\/ (\\d+)/) || [])[0] || '?'")
            r = f"{pos}: ya estaba visto, sin reproducir"
        if r is None:
            r = await _eval(page, """async () => {
          const m = [...document.querySelectorAll('audio,video')].filter(e => e.currentSrc || e.src);
          const pos = (document.body.innerText.match(/(\\d+) \\/ (\\d+)/) || [])[0] || '?';
          let seg = 0;
          for (const e of m) {
            if (!isFinite(e.duration)) await new Promise(r => { e.addEventListener('loadedmetadata', r, {once: true}); setTimeout(r, 5000); });
            e.currentTime = 0; e.muted = false;
            try { await e.play(); } catch (x) { return pos + ': no pude reproducir (' + x.name + ')'; }
            // se corta en cuanto la plataforma marca el ítem como hecho
            const hecho = () => { const m = document.body.innerText.match(/(\\d+) \\/ (\\d+)/);
              const t = m && document.querySelectorAll('.learning__tasksNav_task')[+m[1] - 1];
              return !!t && t.className.includes('--done'); };
            await new Promise(r => { e.addEventListener('ended', r, {once: true}); setTimeout(r, (e.duration + 5) * 1000);
              const iv = setInterval(() => { if (hecho()) { clearInterval(iv); e.pause(); r(); } }, 1000); });
            seg += e.duration || 0;
          }
          return pos + ': ' + (m.length ? m.length + ' medio(s), ' + Math.round(seg) + ' s' : 'sin audio');
        }""")
        log.append(r)
        m = re.match(r"(\d+) / (\d+)", r)
        if m and m.group(1) == m.group(2):
            log.append("fin de Explore")
            break
        if not await _eval(page, "() => { window.__mcpBancos = null; const a = document.querySelector('a.learning__nextItemLink'); if (a) a.click(); return !!a; }"):
            log.append("no hay botón siguiente")
            break
        await _esperar_carga(page, 1500)
    return log


@mcp.tool()
async def prueba_iniciar() -> str:
    """Test de lección (paso 3): pulsa Start Test y devuelve el primer ítem."""
    page = await _pagina()
    await _eval(page, JS_CERRAR_CAPA)
    await page.mouse.click(*PRUEBA_START)
    await _esperar_carga(page, 2000)
    return (await estado(900))["texto"]


JS_PRUEBA_ITEM = """() => {
  const t = document.body.innerText;
  const pos = (t.match(/(\\d+) \\/ (\\d+)/) || [])[0] || '?';
  const audios = [...new Set([...document.querySelectorAll('audio,source,video')]
    .map(a => a.currentSrc || a.src).filter(s => s && !/soundTest/i.test(s)))];
  const imgs = [...document.querySelectorAll('.learning__main img')].filter(i => i.offsetParent && i.width > 120);
  let caja = null;
  // rectángulo que envuelve las imágenes grandes que no salieron en ítems anteriores
  const nuevas = imgs.filter(i => !(window.__mcpVistas ||= new Set()).has(i.src));
  const base = nuevas.length ? nuevas : imgs;
  nuevas.forEach(i => window.__mcpVistas.add(i.src));
  if (base.length) {
    const r = base.map(i => i.getBoundingClientRect());
    caja = [Math.min(...r.map(b => b.left)) - 40, Math.min(...r.map(b => b.top)) - 15,
            Math.max(...r.map(b => b.right)) + 5, Math.max(...r.map(b => b.bottom)) + 5].map(Math.round);
  }
  // arrastre: fichas del banco y número de huecos (al empezar, todas las fichas están en el banco)
  const vis = e => e.offsetParent;
  const zonas = [...document.querySelectorAll('ed-la-dndzone')].filter(vis);
  let fichas = [], huecos = 0;
  if (zonas.length) {
    const llenas = zonas.filter(z => z.querySelector('.dnditem'));
    const banco = llenas.sort((a, b) => b.querySelectorAll('.dnditem').length - a.querySelectorAll('.dnditem').length)[0];
    fichas = banco ? [...banco.querySelectorAll('.dnditem')].map(i => i.textContent.trim()) : [];
    huecos = zonas.length - 1;
  } else if (document.querySelector('.droptarget')) {
    fichas = [...document.querySelectorAll('.draggable')].filter(vis).map(e => e.textContent.trim());
    huecos = [...document.querySelectorAll('.droptarget')].filter(vis).length;
  }
  // un solo ítem con varias preguntas: radios question-K_answer-M
  const preguntas = new Set([...document.querySelectorAll('input[type=radio]')].filter(vis)
    .map(r => (r.name.match(/^question-(\\d+)_/) || [])[1]).filter(Boolean)).size;
  // desplegables (ed-select-options): opciones de cada uno
  const desplegables = [...document.querySelectorAll('ed-select-options')].filter(vis)
    .map(d => [...d.querySelectorAll('.DDLOptions__listItem')].map(o => o.textContent.trim()));
  return {pos, audios, caja, nuevas: nuevas.length, fichas, huecos, preguntas, desplegables};
}"""


@mcp.tool()
async def prueba_leer(max_items: int = 20, imagenes: bool = True, max_segundos: int = 45) -> list:
    """Test de lección ya iniciado: recorre todos los ítems desde el actual y devuelve, por ítem,
    el texto, la transcripción de sus audios y (si tiene) una captura de sus imágenes.
    Después responde con prueba_responder()."""
    page = await _pagina()
    salida, oidos, anterior = [], set(), ""
    await _eval(page, "() => { window.__mcpVistas = new Set(); }")
    t0 = time.monotonic()
    for _ in range(max_items):
        if salida and time.monotonic() - t0 > max_segundos:
            salida.append(f"pausa por tiempo ({max_segundos} s): llama otra vez para seguir desde el ítem actual")
            break
        await _eval(page, "() => document.querySelector('.prMCQ__speakerPlayBtnW_question')?.click()")
        await page.wait_for_timeout(800)
        await _eval(page, "() => document.querySelectorAll('audio,video').forEach(a => a.pause())")
        info = await _eval(page, JS_PRUEBA_ITEM)
        # texto completo: con lecturas largas (correos, artículos) la pregunta y las opciones van al
        # final, y recortar a 1200 caracteres las dejaba fuera
        texto = (await estado(12000))["texto"]
        texto = re.split(r"Let.s try some|Take the quiz to test", texto)[0]
        texto = re.sub(r"^Lesson \d+:.*\nStep \d+:.*\n", "", texto).strip()
        # si el pasaje es el mismo del ítem anterior, se muestra solo lo que cambia (pregunta y opciones)
        comun = len(os.path.commonprefix([texto, anterior]))
        anterior = texto
        if comun > 300:
            corte = texto.rfind("\n", 0, comun) + 1
            texto = "(mismo pasaje que el ítem anterior)\n" + texto[corte:]
        partes = [f"### {info['pos']}\n{texto}"]
        if info.get("huecos"):
            partes.append(f"ARRASTRE: {info['huecos']} hueco(s); fichas: {info['fichas']} "
                          f"→ responde con una lista en orden de huecos, p. ej. {{\"n\": [\"ficha1\", \"ficha2\"]}}")
        if info.get("desplegables"):
            # las opciones no están en el DOM hasta abrir el desplegable: se abren uno a uno
            if not any(info["desplegables"]):
                info["desplegables"] = [o for o in await practica_opciones() if o]
            partes.append(f"DESPLEGABLES: {info['desplegables']} → responde con el texto de la opción, "
                          f"o una lista si hay varios en el ítem: {{\"n\": \"opción\"}}")
        if info.get("preguntas", 0) > 1:
            partes.append(f"VARIAS PREGUNTAS en este ítem ({info['preguntas']}) → responde con claves "
                          f"\"ítem.pregunta\", p. ej. {{\"1.1\": \"A\", \"1.2\": \"C\"}}")
        for u in info["audios"]:  # los <audio> de ítems anteriores siguen en la página
            if u not in oidos:
                oidos.add(u)
                txt = await asyncio.to_thread(_transcribir, u, None, None, True)
                # en algunos videos el VAD descarta casi todo y whisper inventa "Thank you." o
                # "Thanks for watching!": si sale casi vacío, se repite sin VAD
                # también si la transcripción cubre mucho menos que la duración que muestra el reproductor
                dur = re.search(r"00:00/(\d+):(\d+)", texto)
                fin = [float(x) for x in re.findall(r"-\s*([\d.]+)\]", txt)]
                corto = bool(dur and fin and max(fin) < 0.6 * (int(dur.group(1)) * 60 + int(dur.group(2))))
                if len(txt) < 80 or corto:
                    txt = await asyncio.to_thread(_transcribir, u, None, None, False)
                partes.append("audio: " + txt)
        salida.append("\n".join(partes))
        # captura si las opciones son imágenes o si aparece una imagen nueva (lista de precios, horario...)
        if imagenes and info["caja"] and (re.search(r"^\(A\)$", texto, re.M) or info["nuevas"]):
            x0, y0, x1, y1 = info["caja"]
            salida.append(await _jpeg(page, {"x": max(x0, 0), "y": max(y0, 0), "width": x1 - max(x0, 0),
                                             "height": y1 - max(y0, 0)}, 45))
        m = re.match(r"(\d+) / (\d+)", info["pos"])
        if not m or m.group(1) == m.group(2):
            break
        if not await _eval(page, "() => { const a = document.querySelector('a.learning__nextItemLink'); if (a) a.click(); return !!a; }"):
            break
        await _esperar_carga(page, 1500)
    return salida


@mcp.tool()
async def prueba_responder(respuestas: dict[str, str | list[str]]) -> dict:
    """Test de lección: marca las respuestas yendo a cada ítem y devuelve lo que quedó marcado.
    - Opción múltiple: {"1": "B"} (letra o texto de la opción). Ojo: las opciones se barajan cada
      vez que se abre el test, así que una letra solo vale si se leyó en ESTA misma apertura;
      con respuestas preparadas antes (o de datos/quiz/) usa siempre el texto de la opción.
    - Arrastre: {"2": ["were", "replaced"]} (fichas en el orden de los huecos).
    - Un ítem con varias preguntas (radios question-K): {"1.1": "A", "1.2": "C"} (ítem.pregunta).
    - Desplegables: {"3": "in shape"} o, con varios en el ítem, {"3": ["opción1", "opción2"]}.
    Luego llama a prueba_enviar()."""
    page = await _pagina()
    marcado = {}
    clave = lambda k: tuple(int(x) for x in k.split("."))
    actual = None
    for k, r in sorted(respuestas.items(), key=lambda kv: clave(kv[0])):
        n = clave(k)[0]
        if n != actual:
            if (await ir_item(n)).startswith(("No existe", "El paginador")):
                marcado[k] = "no existe el ítem"
                continue
            actual = n
            await _eval(page, "() => { window.__mcpBancos = null; }")
        if await _eval(page, "() => [...document.querySelectorAll('ed-select-options')].some(d => d.offsetParent)"):
            marcado[k] = await practica_elegir(r if isinstance(r, list) else [r])
            continue
        if isinstance(r, list):  # arrastre, hueco por hueco
            res = ""
            for h, ficha in enumerate(r, 1):
                res = await arrastrar(ficha, h)
                if res.startswith("No encontré"):
                    break
            marcado[k] = res
            continue
        if "." in k:  # varias preguntas en el mismo ítem
            q = clave(k)[1]
            marcado[k] = await _eval(page, """([q, r]) => {
              const l = [...document.querySelectorAll('input[type=radio]')].filter(e => e.offsetParent && e.name.startsWith('question-' + q + '_'));
              const txt = e => (e.closest('label,li,div') || e).textContent.trim().replace(/\\s+/g, ' ');
              const limpio = s => s.replace(/^(\\([A-D]\\)|[A-D][.)])\\s*/, '').toLowerCase();  // solo "A." "A)" "(A)", no la inicial de una palabra
              const o = /^[A-D]$/i.test(r) ? l['ABCD'.indexOf(r.toUpperCase())] : l.find(e => limpio(txt(e)) === r.trim().toLowerCase());
              if (!o) return 'opción no encontrada (' + l.length + ' radios)';
              (o.closest('label') || o).click(); if (!o.checked) o.click();
              return o.checked ? txt(o).slice(0, 60) : 'no se marcó';
            }""", [q, r])
            continue
        marcado[k] = await _eval(page, """(r) => {
          const ops = [...document.querySelectorAll('.prMCQ__answers')].filter(o => o.offsetParent);
          const limpio = s => s.trim().replace(/^(\\([A-D]\\)|[A-D][.)])\\s*/, '').replace(/\\s+/g, ' ').toLowerCase();  // solo "A." "A)" "(A)", no la inicial de una palabra
          const o = /^[A-D]$/i.test(r) ? ops['ABCD'.indexOf(r.toUpperCase())]
                  : ops.find(o => limpio(o.textContent) === r.trim().toLowerCase());
          if (!o) return 'opción no encontrada';
          (o.querySelector('label') || o.querySelector('input')).click();
          return o.querySelector('input')?.checked ? o.textContent.trim().replace(/\\s+/g, ' ').slice(0, 60) : 'no se marcó';
        }""", r)
    return marcado


@mcp.tool()
async def prueba_enviar() -> str:
    """Test de lección: pulsa Submit (acepta la confirmación) y devuelve el resultado."""
    page = await _pagina()
    await _eval(page, "() => { window.confirm = () => true; }")
    # el botón verde solo es Submit en el último ítem; en los demás es Next y solo avanza
    for _ in range(30):
        m = re.search(r"(\d+) / (\d+)", await _eval(page, "() => document.body.innerText"))
        if not m or m.group(1) == m.group(2):
            break
        if not await _eval(page, "() => { const a = document.querySelector('a.learning__nextItemLink'); if (a) a.click(); return !!a; }"):
            break
        await _esperar_carga(page, 1200)
    await page.mouse.click(*PRUEBA_SUBMIT)
    await _esperar_carga(page, 2500)
    return _compactar(_limpiar_leccion(await _eval(page, "() => document.body.innerText")), 600)


# ---------------------------------------------------------------- speaking

JS_SPEAKING = """async (b64) => {
  if (!window.__fakeInstalled) {
    window.__fake = {src: null, rates: {}, pos: 0, active: false, seen: new WeakMap()};
    const d = Object.getOwnPropertyDescriptor(AudioProcessingEvent.prototype, 'inputBuffer');
    Object.defineProperty(AudioProcessingEvent.prototype, 'inputBuffer', {configurable: true, get() {
      const b = d.get.call(this), F = window.__fake;
      if (!F.active || !F.src || F.seen.has(this)) return b;
      F.seen.set(this, 1);
      let s = F.rates[b.sampleRate];
      if (!s) {  // remuestreo lineal a la frecuencia del contexto
        const r = F.src.rate / b.sampleRate, n = Math.floor(F.src.data.length / r);
        s = new Float32Array(n);
        for (let i = 0; i < n; i++) { const x = i * r, j = Math.floor(x), f = x - j;
          s[i] = (F.src.data[j] || 0) * (1 - f) + (F.src.data[j + 1] || 0) * f; }
        F.rates[b.sampleRate] = s;
      }
      const chunk = new Float32Array(b.length);
      chunk.set(s.subarray(F.pos, F.pos + b.length)); F.pos += b.length;
      for (let c = 0; c < b.numberOfChannels; c++) b.copyToChannel(chunk, c);
      if (F.pos > s.length + b.sampleRate * 4) F.active = false;
      return b;
    }});
    window.__fakeInstalled = true;
  }
  const u = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const a = await new OfflineAudioContext(1, 1, 16000).decodeAudioData(u.buffer);
  Object.assign(window.__fake, {src: {data: a.getChannelData(0), rate: a.sampleRate}, rates: {}, active: false, done: false});
  clearInterval(window.__iv);
  window.__iv = setInterval(() => {  // reproduce la frase cuando el ejercicio dice "Speak now"
    const sp = document.body.innerText.includes('Speak now'), F = window.__fake;
    if (sp && !F.active && !F.done) Object.assign(F, {pos: 0, active: true, done: true});
    if (!sp) F.done = false;
  }, 100);
  return a.duration;
}"""


def _tts_b64(frase: str, voz: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        aiff, mp3 = Path(tmp, "f.aiff"), Path(tmp, "f.mp3")
        subprocess.run(["say", "-v", voz, "-r", "165", "-o", str(aiff), frase], check=True)
        subprocess.run([FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-i", str(aiff),
                        "-af", "silenceremove=start_periods=1:start_threshold=-50dB,adelay=300",
                        "-ac", "1", "-ar", "16000", "-b:a", "16k", str(mp3)], check=True)
        return base64.b64encode(mp3.read_bytes()).decode()


@mcp.tool()
async def speaking_frase(frase: str, voz: str = "Samantha") -> str:
    """Genera la frase con la voz del sistema (macOS `say`) y la deja lista para el próximo "Speak now".
    Llámala con la línea exacta que toca decir; cada línea nueva necesita su llamada."""
    page = await _pagina()
    b64 = await asyncio.to_thread(_tts_b64, frase, voz)
    dur = await _eval(page, JS_SPEAKING, b64)
    return f"frase cargada ({dur:.1f} s)"


@mcp.tool()
async def speaking_apagar() -> str:
    """Detiene la inyección de audio en la página."""
    page = await _pagina()
    await _eval(page, "() => { clearInterval(window.__iv); if (window.__fake) window.__fake.active = false; }")
    return "ok"


AVISOS_HABLA = ("Speak now.", "Prepare to speak...", "Click 'Try again' to repeat your response.")
ATASCO_HABLA = 30  # segundos en "Prepare to speak..." a partir de los cuales se da el speaking por atascado


@mcp.tool()
async def speaking_conversacion(personaje: str = "derecha", max_segundos: int = 240) -> list[str]:
    """Hace sola una conversación de speaking (paso Interact): elige el personaje (`derecha` o `izquierda`
    de la foto), pulsa Start y, en cada turno, carga con TTS la línea que toca decir; si sale "Try again",
    la vuelve a cargar y reintenta. En las conversaciones ramificadas ("Click 'Start' to begin the
    conversation", Interact 2) no hay personaje: dice la primera respuesta ofrecida en cada turno.
    Termina cuando el ítem queda hecho y la conversación acaba."""
    page = await _pagina()
    log, t0 = [], time.monotonic()
    texto = ""
    for _ in range(40):  # la sesión de reconocimiento de voz tarda en establecerse
        texto = (await estado(1500))["texto"]
        if "Click on the arrow" in texto or "begin the conversation" in texto or any(a in texto for a in AVISOS_HABLA):
            break
        await page.wait_for_timeout(1000)
    # conversación ramificada (Interact 2): sin personajes; vale cualquiera de las respuestas
    # (div.recordingPanelSentence), así que digo la primera que sale debajo del botón
    ramificada = "branching conversation" in texto or "begin the conversation" in texto
    if "begin the conversation" in texto:
        await page.get_by_text("Start", exact=True).first.click(timeout=5000)
        log.append("conversación ramificada: Start")
        await page.wait_for_timeout(1500)
    if "Click on the arrow" in texto:
        # las flechas de elegir personaje son a.chooseCaracterArrow (la foto es un fondo CSS)
        flechas = await _eval(page, """() => [...document.querySelectorAll('a.chooseCaracterArrow')].filter(e => e.offsetParent)
          .map(e => { const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })
          .sort((a, b) => a[0] - b[0])""")
        if not flechas:
            return ["no encuentro las flechas de los personajes (a.chooseCaracterArrow); elige con clic() y vuelve a llamar"]
        x, y = flechas[-1] if personaje.startswith("d") else flechas[0]
        await page.mouse.click(x, y)
        await page.wait_for_timeout(800)
        await page.get_by_text("Start", exact=True).first.click(timeout=5000)
        log.append(f"personaje: {personaje}")
    ultima, intentos, quietos = None, 0, 0
    preparando = None  # desde cuándo está en "Prepare to speak..." sin pasar a "Speak now."
    while time.monotonic() - t0 < max_segundos:
        lineas = [l.strip() for l in (await estado(1500))["texto"].split("\n")]
        boton = next((b for b in ("Start", "Try again") if b in lineas), None)
        aviso = next((a for a in AVISOS_HABLA if a in lineas), None)
        # a veces la sesión de reconocimiento de voz se queda en "Prepare to speak..." para siempre
        # (ni suena el otro personaje ni llega "Speak now."); hay que volver a entrar en el paso
        if aviso == "Prepare to speak...":
            preparando = preparando or time.monotonic()
            if time.monotonic() - preparando > ATASCO_HABLA:
                log.append(f"atascado en 'Prepare to speak...' más de {ATASCO_HABLA} s")
                break
        else:
            preparando = None
        if boton and aviso:
            quietos = 0
            i = lineas.index(boton)
            mia = lineas[i + (1 if ramificada else 2)] if len(lineas) > i + 2 else ""
            if ramificada:
                mia = mia.lstrip("-").strip()
            if mia and (mia != ultima or boton == "Try again"):
                await speaking_frase(mia)
                log.append(f"frase: {mia}")
                ultima = mia
                if boton == "Try again":
                    intentos += 1
                    if intentos > 5:
                        log.append("demasiados Try again; me detengo")
                        break
                    await page.get_by_text("Try again", exact=True).first.click(timeout=5000)
        elif ultima:
            quietos += 1
            if quietos >= 3 and await _eval(page, JS_ITEM_HECHO):
                log.append("fin: ítem hecho")
                break
        await page.wait_for_timeout(2000)
    else:
        log.append(f"pausa por tiempo ({max_segundos} s)")
    await speaking_apagar()
    return log


# ---------------------------------------------------------------- pasada completa

JS_ES_PRACTICA = "() => !![...document.querySelectorAll('.practiceTools li')].some(l => l.offsetParent)"


async def _pendientes_leccion(log: list, etiqueta: str) -> list[str]:
    """Vuelve a entrar en cada paso de la lección abierta y anota los ítems pendientes de verdad
    (la marca de hecho a veces se actualiza al salir del ítem)."""
    page = await _pagina()
    malos = []
    for paso in await _eval(page, JS_PASOS):
        txt = await ir_paso(paso.split(".")[0])
        if re.search(r"test|quiz", paso, re.I) or "Start Test" in txt or "Your test score" in txt:
            continue
        await page.wait_for_timeout(1500)
        pend = (await estado(50)).get("items_pendientes")
        if pend not in (None, "ninguno", []):
            malos.append(f"{paso}: {pend}")
    log.append(f"{etiqueta} pendientes: " + ("; ".join(malos) if malos else "ninguno"))
    return malos


@mcp.tool()
async def leccion_completa(unidad: int, leccion: int, curso: str = "", tests: str = "leer") -> list[str]:
    """Pasada completa de una lección: Explore (explorar), Practice (practica_resolver, salta ítems raros),
    speaking (speaking_conversacion) y, según `tests`: "leer" inicia el test y lo deja leído en
    datos/quiz/<curso>_U<u>L<l>.txt (luego prueba_responder + prueba_enviar), o "no" para saltarlo.
    Al final revisa los pendientes reales de cada paso. Puede tardar minutos: con clientes con límite
    de tiempo usa barrido_iniciar()."""
    return await _leccion_completa(unidad, leccion, curso, tests, [])


async def _leccion_completa(unidad: int, leccion: int, curso: str, tests: str, log: list) -> list[str]:
    # `log` se va llenando en vivo (barrido_estado lo lee mientras corre)
    et = f"U{unidad}L{leccion}"
    r = await ir_leccion(unidad, leccion, curso)
    page = await _pagina()
    if "learningArea" not in page.url:
        log.append(f"{et}: no abre: {_compactar(r, 200)}")
        return log
    # el nombre del curso es la segunda línea de la cabecera de la lección (p. ej. "UCB Intermediate 2")
    lineas = [x.strip() for x in str(r).splitlines() if x.strip()]
    nombre_curso = lineas[1] if len(lineas) > 1 and not curso else curso
    for paso in await _eval(page, JS_PASOS):
        try:
            txt = await ir_paso(paso.split(".")[0])
            if re.search(r"test|quiz", paso, re.I):
                for _ in range(6):  # la portada del test tarda en pintarse
                    if "Start Test" in txt or "Your test score" in txt:
                        break
                    await page.wait_for_timeout(2000)
                    txt = (await estado(600))["texto"]
            if "Start Test" in txt or "Your test score" in txt:
                if tests != "leer":
                    log.append(f"{et} {paso}: test sin hacer")
                    continue
                await prueba_iniciar()
                salida = await prueba_leer(20, True, 900)
                carpeta = DATOS / "quiz"
                carpeta.mkdir(parents=True, exist_ok=True)
                nombre = re.sub(r"\W+", "_", nombre_curso or "curso").strip("_")
                base = f"{nombre}_{et}"
                partes = []
                for i, x in enumerate(salida):
                    if isinstance(x, str):
                        partes.append(x)
                    else:
                        f = carpeta / f"{base}_{i}.jpg"
                        f.write_bytes(x.data)
                        partes.append(f"<imagen {f}>")
                (carpeta / f"{base}.txt").write_text("\n".join(partes))
                log.append(f"{et} {paso}: test leído → {carpeta / (base + '.txt')}")
                continue
            await ir_item(1)
            if "Select the speaker" in txt or "Speech Recognition" in txt or re.search(r"interact", paso, re.I):
                res = await speaking_conversacion()
                for _ in range(2):  # si se atascó, se vuelve a entrar en el paso y se reintenta
                    if not any("atascado" in x for x in res) or await _eval(page, JS_ITEM_HECHO):
                        break
                    log.append(f"{et} {paso}: speaking atascado; reintento")
                    await ir_paso(paso.split(".")[0])
                    await ir_item(1)
                    res = await speaking_conversacion()
                log.append(f"{et} {paso}: speaking · {res[-1] if res else ''}")
            elif await _eval(page, JS_ES_PRACTICA):
                res = await practica_resolver(80, 900)
                raros = [x for x in res if "OK" not in x and "fin" not in x]
                log.append(f"{et} {paso}: práctica " + (" | ".join(raros) if raros else f"{len(res)} líneas ok"))
            else:
                res = await explorar(20, 900)
                log.append(f"{et} {paso}: explore · " + " | ".join(x for x in res if "fin" not in x))
        except Exception as e:
            log.append(f"{et} {paso}: EXCEPCIÓN {type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}")
    await _pendientes_leccion(log, et)
    return log


@mcp.tool()
async def pendientes(lecciones: list[str], curso: str = "") -> list[str]:
    """Revisa sin tocar nada los ítems pendientes de cada paso (no de los tests) de las lecciones
    indicadas como "unidad:lección", p. ej. ["2:1", "2:3"]."""
    log = []
    for ul in lecciones:
        u, l = map(int, ul.split(":"))
        await ir_leccion(u, l, curso)
        await _pendientes_leccion(log, f"U{u}L{l}")
    return log


_barrido = {"tarea": None, "log": [], "inicio": 0.0}
LIMITE_LECCION = 1500  # segundos máximos por lección en el barrido


@mcp.tool()
async def barrido_iniciar(lecciones: list[str], curso: str = "", tests: str = "leer") -> str:
    """Lanza en segundo plano leccion_completa() para cada "unidad:lección" (p. ej. ["2:1", "2:2"]).
    Devuelve enseguida; consulta con barrido_estado(). No uses otras herramientas del navegador
    mientras corre (comparten la misma página)."""
    if _barrido["tarea"] and not _barrido["tarea"].done():
        return "ya hay un barrido en marcha; mira barrido_estado() o detenlo con barrido_parar()"
    _barrido["log"] = []
    _barrido["inicio"] = time.monotonic()

    async def correr():
        for ul in lecciones:
            u, l = map(int, ul.split(":"))
            t = time.monotonic()
            try:
                await asyncio.wait_for(_leccion_completa(u, l, curso, tests, _barrido["log"]), LIMITE_LECCION)
            except asyncio.TimeoutError:
                _barrido["log"].append(f"U{u}L{l}: EXCEPCIÓN se pasó de {LIMITE_LECCION} s (¿se colgó o se suspendió el Mac?); sigo con la siguiente")
            except Exception as e:
                _barrido["log"].append(f"U{u}L{l}: EXCEPCIÓN {type(e).__name__}: {str(e)[:200]}")
            _barrido["log"].append(f"U{u}L{l} terminada ({time.monotonic() - t:.0f} s)")
        _barrido["log"].append("BARRIDO TERMINADO")

    _barrido["tarea"] = asyncio.create_task(correr())
    return f"barrido iniciado: {len(lecciones)} lecciones"


@mcp.tool()
async def barrido_estado(desde: int = 0) -> dict:
    """Estado del barrido en segundo plano: si sigue corriendo y las líneas del log desde la número `desde`."""
    t = _barrido["tarea"]
    return {"corriendo": bool(t and not t.done()), "segundos": round(time.monotonic() - _barrido["inicio"]),
            "lineas": len(_barrido["log"]), "log": _barrido["log"][desde:]}


@mcp.tool()
async def barrido_parar() -> str:
    """Detiene el barrido en segundo plano."""
    t = _barrido["tarea"]
    if not t or t.done():
        return "no hay barrido en marcha"
    t.cancel()
    return "barrido detenido"


# ---------------------------------------------------------------- Unit Test

@mcp.tool()
async def test_iniciar() -> str:
    """Desde la página del Unit Test ya abierta (ventana de bienvenida), pulsa Start y pasa la primera pantalla."""
    page = await _pagina()
    await page.mouse.click(*TEST_START)
    await page.wait_for_timeout(3000)
    return _compactar(await _eval(page, "() => document.body.innerText"), 600)


@mcp.tool()
async def test_responder(respuestas: dict[str, str]) -> dict:
    """Marca respuestas del Unit Test, p. ej. {"22": "A", "23": "D"}. Devuelve lo que quedó marcado."""
    page = await _pagina()
    return await _eval(page, """(resp) => {
      const out = {};
      for (const [n, letra] of Object.entries(resp)) {
        const l = [...document.querySelectorAll('input[name=r' + n + ']')];
        const i = 'ABCD'.indexOf(letra.trim().toUpperCase());
        if (l[i]) l[i].click();
        out[n] = 'ABCD'[l.findIndex(r => r.checked)] || (l.length ? '-' : 'no está en esta pantalla');
      }
      return out;
    }""", respuestas)


@mcp.tool()
async def test_siguiente(max_chars: int = 1500) -> dict:
    """Pulsa Next en el Unit Test y devuelve el estado de la nueva pantalla."""
    page = await _pagina()
    await page.mouse.click(*TEST_NEXT)
    await page.wait_for_timeout(2500)
    return await estado(max_chars)


@mcp.tool()
async def test_pasaje(max_capturas: int = 4) -> list:
    """Devuelve el texto de lectura del panel izquierdo del Unit Test.
    Si el pasaje es una imagen, recorre el panel y devuelve capturas JPEG por tramos."""
    page = await _pagina()
    info = await _eval(page, """([x, y]) => {
      let e = document.elementFromPoint(x, y);
      while (e && !(e.scrollHeight > e.clientHeight + 5 && /(auto|scroll)/.test(getComputedStyle(e).overflowY))) e = e.parentElement;
      if (!e) return null;
      document.querySelectorAll('[data-mcp-pasaje]').forEach(n => n.removeAttribute('data-mcp-pasaje'));
      e.setAttribute('data-mcp-pasaje', '1'); e.scrollTop = 0;
      const r = e.getBoundingClientRect();
      return {texto: e.innerText, imgs: e.querySelectorAll('img').length,
              x: r.x, y: r.y, w: r.width, h: e.clientHeight, total: e.scrollHeight};
    }""", list(TEST_PASAJE))
    if not info:
        return ["No encontré un panel con desplazamiento; usa captura()"]
    salida = []
    texto = _compactar(info["texto"], 4000)
    if texto and info["imgs"] == 0:
        return [texto]
    if texto:
        salida.append(texto)
    clip = {"x": info["x"], "y": info["y"], "width": info["w"], "height": info["h"]}
    paso = max(info["h"] - 40, 100)
    desp = 0
    for _ in range(max_capturas):
        await page.wait_for_timeout(300)
        salida.append(await _jpeg(page, clip, 60))
        desp += paso
        if desp >= info["total"] - 5:
            break
        await _eval(page, "(d) => { document.querySelector('[data-mcp-pasaje]').scrollTop = d; }", desp)
    return salida


@mcp.tool()
async def test_enviar() -> str:
    """Envía el Unit Test (Submit Test → Yes) y devuelve el texto con la nota. Revisa antes que todo esté respondido."""
    page = await _pagina()
    for _ in range(2):  # el primer clic puede solo cerrar el panel de Item Review
        await page.mouse.click(*TEST_SUBMIT)
        await page.wait_for_timeout(1500)
        if "Are you sure" in await _eval(page, "() => document.body.innerText"):
            break
    await page.mouse.click(*TEST_YES)
    await page.wait_for_timeout(4000)
    texto = await _eval(page, "() => document.body.innerText")
    m = re.search(r"Score is:\s*(\d+)", texto)
    return f"Nota: {m.group(1)}" if m else _compactar(texto, 500)


def _servir_http(puerto: int):
    """Transporte HTTP para clientes que solo aceptan una URL (ChatGPT, por ejemplo).
    La ruta lleva un token secreto guardado en datos/token.txt: quien no lo sepa no llega al navegador."""
    import secrets
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings
    ficha = DATOS / "token.txt"
    if not ficha.exists():
        DATOS.mkdir(parents=True, exist_ok=True)
        ficha.write_text(secrets.token_urlsafe(24))
    token = ficha.read_text().strip()
    ruta = f"/{token}/mcp"
    # detrás de un túnel el Host es el dominio público; la protección la da la ruta secreta
    app = mcp.streamable_http_app(streamable_http_path=ruta, transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False))
    print(f"MCP engdis por HTTP en http://127.0.0.1:{puerto}{ruta}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=puerto, log_level="warning")


if __name__ == "__main__":
    import sys
    if "--http" in sys.argv:
        i = sys.argv.index("--http")
        _servir_http(int(sys.argv[i + 1]) if len(sys.argv) > i + 1 and sys.argv[i + 1].isdigit() else 8765)
    else:
        mcp.run()

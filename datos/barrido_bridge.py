# barrido de prueba en TOEIC Bridge: hace todos los pasos menos responder los quizzes,
# que deja leídos en datos/quiz/UxLy.txt (+ capturas) para responderlos aparte
import asyncio, sys, time, json
from pathlib import Path
sys.path.insert(0, "/Users/luisdanielrojascaceres/Downloads/proyecto_ingles/engdis_mcp")
import server as S

CURSO = "TOEIC Bridge"
DIR = Path(S.__file__).parent / "datos" / "quiz"

async def main():
    DIR.mkdir(exist_ok=True)
    pares = [tuple(map(int, a.split(":"))) for a in sys.argv[1:]] or [(u, l) for u in (1, 2, 3) for l in range(1, 7)]
    for u, l in pares:
        t = time.time()
        try:
            await S.ir_leccion(u, l, CURSO)
            pasos = await S._eval(await S._pagina(), S.JS_PASOS)
        except Exception as e:
            print(f"U{u}L{l}: EXCEPCIÓN al abrir {e}", flush=True); continue
        for paso in pasos:
            n = paso.split(".")[0]
            try:
                txt = await S.ir_paso(n)
                if "Start Test" in txt or "Your test score" in txt:
                    if "Your test score" in txt:
                        r = "quiz ya hecho"
                    else:
                        await S.prueba_iniciar()
                        salida = await S.prueba_leer(20, True, 900)
                        textos = []
                        for i, x in enumerate(salida):
                            if isinstance(x, str):
                                textos.append(x)
                            else:
                                f = DIR / f"U{u}L{l}_{i}.jpg"; f.write_bytes(x.data); textos.append(f"<imagen {f.name}>")
                        (DIR / f"U{u}L{l}.txt").write_text("\n".join(textos))
                        r = f"quiz leído ({len(salida)} partes)"
                else:
                    await S.ir_item(1)
                    pg = await S._pagina()
                    practica = await S._eval(pg, "() => !![...document.querySelectorAll('.practiceTools li')].some(l => l.offsetParent)")
                    log = await (S.practica_resolver(60, 900) if practica else S.explorar(20, 900))
                    malos = [x for x in log if "OK" not in x and "fin" not in x and ("medio" not in x and "sin audio" not in x)]
                    r = ("práctica " if practica else "explore ") + (" | ".join(malos) if malos else f"{len(log)} líneas ok")
                    pend = (await S.estado(50)).get("items_pendientes")
                    if pend not in (None, "ninguno"):
                        r += f" · PENDIENTES {pend}"
            except Exception as e:
                r = f"EXCEPCIÓN {type(e).__name__}: {str(e).splitlines()[0][:200]}"
            print(f"U{u}L{l} {paso}: {r}", flush=True)
        print(f"U{u}L{l} ({time.time()-t:.0f}s)", flush=True)
    await S._ctx.close()

asyncio.run(main())

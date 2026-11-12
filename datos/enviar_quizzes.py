# envía los quizzes de datos/quiz/respuestas.json con las herramientas del MCP
import asyncio, sys, json, re
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))  # carpeta del proyecto
import server as S

async def main():
    resp = json.load(open(S.DATOS / "quiz" / "respuestas.json"))
    for clave, r in resp.items():
        u, l = map(int, re.findall(r"\d+", clave))
        try:
            await S.ir_leccion(u, l, "TOEIC Bridge")
            await S.ir_paso("quiz")
            await S.prueba_iniciar()
            marcado = await S.prueba_responder(r)
            malos = {k: v for k, v in marcado.items() if v.startswith(("no ", "opción"))}
            nota = await S.prueba_enviar()
            m = re.search(r"Your test score is:\s*(\d+%)", nota)
            print(clave, m.group(1) if m else nota[:200].replace("\n", " | "), malos or "", flush=True)
        except Exception as e:
            print(clave, "EXCEPCIÓN", str(e).splitlines()[0][:200], flush=True)
    await S._ctx.close()

asyncio.run(main())

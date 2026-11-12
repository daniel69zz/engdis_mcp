# barrido de prueba: corre practica_resolver en todas las prácticas de las unidades 1-6
import asyncio, sys, time
sys.path.insert(0, "/Users/luisdanielrojascaceres/Downloads/proyecto_ingles/engdis_mcp")
import server as S

async def main():
    # argumentos opcionales "U:L" para probar solo esas lecciones
    pares = [tuple(map(int, a.split(":"))) for a in sys.argv[1:]] or [(u, l) for u in range(1, 7) for l in range(1, 7)]
    for u, l in pares:
        if True:
            t = time.time()
            try:
                await S.ir_leccion(u, l)
                r = await S.ir_paso("practice")
                if r.startswith("No encuentro") or r.startswith("El paso"):
                    print(f"U{u}L{l}: sin práctica ({r})", flush=True); continue
                await S.ir_item(1)
                log = await S.practica_resolver(60, 900)
            except Exception as e:
                log = [f"EXCEPCIÓN {type(e).__name__}: {str(e).splitlines()[0][:200]}"]
            malos = [x for x in log if "OK" not in x and "fin" not in x]
            print(f"U{u}L{l} ({time.time()-t:.0f}s): {sum('OK' in x for x in log)} OK; " + (" | ".join(malos) if malos else "completa"), flush=True)
            if malos:
                pg = await S._pagina()
                await pg.screenshot(path=f"/Users/luisdanielrojascaceres/Downloads/proyecto_ingles/engdis_mcp/datos/barrido_U{u}L{l}.jpg", type="jpeg", quality=50)
    await S._ctx.close()

asyncio.run(main())

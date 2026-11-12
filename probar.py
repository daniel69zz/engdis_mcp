#!/usr/bin/env python3
# Cliente de prueba: arranca server.py por stdio y llama herramientas.
# uso: probar.py listar | probar.py <herramienta> '<json de argumentos>' [<herramienta> '<json>' ...]
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BASE = Path(__file__).resolve().parent


async def main(args):
    params = StdioServerParameters(command=sys.executable, args=[str(BASE / "server.py")])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        if args[:1] == ["listar"]:
            for t in (await s.list_tools()).tools:
                print(f"- {t.name}: {(t.description or '').splitlines()[0]}")
            return
        if args[:1] == ["login"]:  # abre el navegador y espera hasta 15 min a que el usuario inicie sesión
            print((await s.call_tool("abrir", {})).content[0].text, flush=True)
            for _ in range(180):
                await asyncio.sleep(5)
                t = (await s.call_tool("js", {"codigo": "document.body.innerText.includes('Hello')"})).content[0].text
                if t == "True":
                    print("sesión iniciada", flush=True)
                    return
            print("tiempo agotado")
            return
        for nombre, arg in zip(args[::2], args[1::2]):
            res = await s.call_tool(nombre, json.loads(arg))
            print(f"=== {nombre} (error={getattr(res, "is_error", getattr(res, "isError", None))})")
            for c in res.content:
                if c.type == "text":
                    print(c.text)
                elif c.type == "image":
                    import base64
                    out = BASE / "datos" / f"prueba_{nombre}_{len(list((BASE / 'datos').glob('prueba_*')))}.jpg"
                    out.write_bytes(base64.b64decode(c.data))
                    print(f"<imagen guardada en {out}>")
                else:
                    print(f"<{c.type} {getattr(c, "mime_type", getattr(c, "mimeType", ""))} b64>")


asyncio.run(main(sys.argv[1:]))

# Uso del servidor MCP de Kaggle en PI

## Resumen
El servidor MCP de Kaggle permite acceder a los recursos de Kaggle (competitions, datasets, kernels, modelos, etc.) a través del protocolo Model Context Protocol (MCP) desde cualquier cliente compatible, incluido el entorno de desarrollo PI.

## Configuración en PI
El servidor ya está configurado en `~/.pi/agent/mcp.json`:

```json
{
  "mcpServers": {
    "kaggle": {
      "httpUrl": "https://www.kaggle.com/mcp",
      "env": {
        "KAGGLE_API_TOKEN": "<YOUR_KAGGLE_API_TOKEN>"
      }
    }
  }
}
```

> Nota: El token de API se carga desde la variable de entorno `KAGGLE_API_TOKEN`. Asegúrate de que el archivo `~/.kaggle/access_token` contenga el token válido.

## Requisitos previos
- Tener un token de API de Kaggle válido (ya presente en `~/.kaggle/access_token`).
- Contar con un cliente MCP compatible instalado y configurado para apuntar al servidor `kaggle` definido arriba.

## Cómo usarlo desde PI
PI expone las herramientas MCP a través de su capa de agentes. Para invocar una herramienta de Kaggle, puedes usar el subagente `gentle-ai-worker` o cualquier otro que tenga acceso a las herramientas MCP.

Ejemplo de llamada mediante bash (para probar directamente):
```bash
# Listar los datasets disponibles (ejemplo genérico)
mcp call kaggle list_datasets
```

Dado que la sintaxis exacta depende del cliente MCP que uses, consulta la documentación del cliente para conocer el formato de llamada.

## Endpoints disponibles
El servidor MCP oficial de Kaggle expone aproximadamente 96 herramientas organizadas en categorías:

- **Competitions**: listar, buscar, obtener detalles, descargar archivos, enviar predicciones.
- **Datasets**: listar, buscar, obtener detalles, descargar, crear, actualizar, versionar.
- **Kernels (Notebooks)**: listar, buscar, obtener detalles, descargar código, ejecutar.
- **Models**: listar, buscar, obtener detalles, descargar, versionar.
- **Benchmarks**: ejecutar benchmarks de modelos.
- **Discussions**: listar, buscar, obtener hilos y comentarios.
- **Workflow utilities**: gestionar credenciales, configuración, etc.

Para ver la lista completa de herramientas, puedes usar un cliente MCP que soporte la introspección, por ejemplo:

```bash
mcp list kaggle
```

## Solución de problemas
- **Error de autenticación (401/403)**: Verifica que el token en `~/.kaggle/access_token` sea correcto y no haya expirado.
- **Conexión fallida**: Asegúrate de que la URL `https://www.kaggle.com/mcp` sea accesible desde tu red y que no haya proxies bloqueando el tráfico.
- **Herramienta no encontrada**: Revisa que el cliente MCP esté actualizado y que el servidor haya sido recargado después de modificar la configuración.

## Referencias
- Documentación oficial de Kaggle MCP: https://www.kaggle.com/docs/mcp
- Repositorio del servidor MCP de Kaggle (ejemplo de implementación): https://github.com/tripathysagar/kaggle-mcp-server
- Especificación del Model Context Protocol: https://modelcontextprotocol.io/

---
*Documentación generada el 2026-09-22*
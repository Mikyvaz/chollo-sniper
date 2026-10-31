# Entorno Remoto (Samba + SSH)

    Este directorio es un montaje en red (Samba SMB) correspondiente al servidor Linux remoto `miky-p6657es`
    (`100.114.8.117`).

    ## Reglas Obligatorias de Operación

    1. **PROHIBIDO ejecutar herramientas de ejecución/build en Windows local:**
       - No ejecutes `npm`, `pnpm`, `pip`, `python`, `pytest`, `cargo`, `docker`, etc. directamente en el
       PowerShell/CMD local de Windows. Las dependencias y el runtime residen en el servidor Linux.

    2. **Ejecución remota vía SSH:**
       - Cualquier comando de consola (instalar paquetes, correr tests, compilar, arrancar scripts o contenedores)
       debe ejecutarse a través de SSH con el alias `servidor`:
         `ssh servidor "cd <ruta_en_linux> && <comando>"`

    3. **Traducción de Rutas (Path Translation):**
       - Windows `Z:\`  --> Linux `/home/miky/`
       - Windows `Z:\opt_servidor\`  --> Linux `/opt/`
       - *Ejemplo:* Si estás editando `Z:\mi-proyecto\index.js`, para ejecutarlo usa:
         `ssh servidor "cd /home/miky/mi-proyecto && node index.js"`

    4. **Edición de archivos:**
       - La lectura y edición de archivos (`read_file`, `edit_file`, `write_file`) sí se realiza directamente sobre
       las rutas `Z:\...` de Windows a través del recurso compartido de Samba.

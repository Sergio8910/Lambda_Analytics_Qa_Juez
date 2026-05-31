"""Verificador post-ejecución de artefactos de flows IA.

Producto SEPARADO del Juez. Audita artefactos reales (PDFs, imágenes, etc.)
generados por flows productivos contra lo que la BD del cliente dice que
deberían contener. No bloquea producción, no toca código del Juez.

Ver el README de esta carpeta para arquitectura y restricciones operativas.
"""

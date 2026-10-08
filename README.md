# DecaFirma para Odoo

Módulos de Odoo para emitir con [DecaFirma](https://decafirma.com) el documento electrónico de control
(DeCA), obligatorio en el transporte de mercancías por carretera en España desde el 5 de octubre de 2026
(Orden FOM/2861/2012 y Resolución de 5 de junio de 2026), y el albarán que el cliente firma en su móvil.

| Módulo | Nombre en la tienda | Qué hace |
|---|---|---|
| `decafirma` | DecaFirma Connector | Conector: clave de la API, avisos al momento (webhook firmado), envíos y documentos (QR, versión, PDF, firma), corregir, anular y mandar al conductor. Permisos «DecaFirma / Usuario» (emite, corrige, manda) y «DecaFirma / Responsable» (además anula). |
| `decafirma_stock` | DecaFirma Inventory | DeCA y albarán desde el albarán de salida de Inventario; agrupaciones de albaranes como rutas de reparto (un DeCA para todas las paradas); papeles del porte (foto, número y peso) listos para facturar. |
| `decafirma_fleet` | DecaFirma Fleet | El camión y el remolque de Flota: su matrícula y su conductor van al DeCA. Se instala solo con Inventario y Flota. |
| `decafirma_stock_batch` | DecaFirma Delivery Routes | (Odoo 17 a 19) Las agrupaciones de albaranes como rutas; en Odoo 20 ya va dentro de `decafirma_stock`. |

Una rama por versión de Odoo: `20.0`, `19.0`, `18.0`, `17.0`. Necesita una cuenta de DecaFirma con la API
(planes de pago); los módulos son gratis. API: https://decafirma.com/desarrolladores

## Probar en local

```bash
docker compose -f dev/docker-compose.yml up -d     # Odoo de esta rama en http://localhost:8169
docker compose -f dev/docker-compose.yml exec odoo odoo -d prueba -i decafirma_stock --with-demo --stop-after-init
docker compose -f dev/docker-compose.yml exec odoo odoo -d prueba -u decafirma_stock --test-tags /decafirma_stock --stop-after-init --http-port 8170
```

Autor: Francodesystems. Licencia OPL-1.

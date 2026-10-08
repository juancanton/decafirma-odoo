import base64
import logging
from urllib.parse import quote

import requests

from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TIMEOUT = 25
# Lo que acepta la API del PDF de su programa («albaranPdf»): cada uno, y entre todos los de una llamada de rutas.
MAX_PDF_PROPIO = 5 * 1024 * 1024
MAX_PDFS_RUTA = 40 * 1024 * 1024


class DecafirmaError(Exception):
    """DecaFirma contestó con un error. Su mensaje va en castellano y se puede enseñar tal cual."""


class DecafirmaClient:
    """Cliente de la API v1 de DecaFirma (/api/v1, «Authorization: Bearer dfk_…»).

    La API contesta siempre en JSON ({error, detalles?} si algo falla). Un 422
    no es un fallo de la petición: el envío existe pero el documento no se ha
    podido emitir o corregir, y viene el estado con el motivo en «error».
    Documentación: https://decafirma.com/desarrolladores
    """

    def __init__(self, company):
        # Quién emite en Odoo, antes del sudo (que no cambia el usuario, pero por claridad):
        # DecaFirma lo enseña en el historial, «María Pérez (desde Odoo)».
        user = company.env.user
        self.usuario = user.name if user and not user._is_superuser() and not user._is_public() else None
        company = company.sudo()
        self.base_url = (company.decafirma_url or '').rstrip('/')
        self.api_key = company.decafirma_api_key
        if not self.base_url or not self.api_key:
            raise UserError(_('Falta conectar DecaFirma: Ajustes > DecaFirma > clave de la API.'))

    def _headers(self):
        h = {'Authorization': f'Bearer {self.api_key}', 'Accept': 'application/json', 'X-DecaFirma-Programa': 'Odoo'}
        if self.usuario:
            # Una cabecera solo lleva bytes: con %-escapes, «María» llega entera.
            h['X-DecaFirma-Usuario'] = quote(self.usuario[:80], safe=' ')
        return h

    def _request(self, method, path, payload=None, params=None):
        url = f'{self.base_url}/api/v1{path}'
        try:
            resp = requests.request(method, url, json=payload, params=params, headers=self._headers(), timeout=TIMEOUT)
        except requests.RequestException as e:
            _logger.warning('DecaFirma %s %s: %s', method, path, e)
            raise UserError(_('No se ha podido conectar con DecaFirma (%s). Prueba otra vez en un momento.', e)) from e
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code in (200, 201, 422):
            return resp.status_code, data
        mensaje = data.get('error') or resp.reason
        detalles = data.get('detalles')
        if isinstance(detalles, list):
            mensaje = f"{mensaje} {' '.join(map(str, detalles))}"
        raise DecafirmaError(_('DecaFirma: %(msg)s (%(code)s)', code=resp.status_code, msg=mensaje))

    # Envíos y documentos
    def crear_envio(self, payload):
        return self._request('POST', '/envios', payload)

    def corregir(self, envio_id, cambios):
        return self._request('PATCH', f'/envios/{envio_id}', cambios)

    def emitir(self, envio_id, tipo, serie=None, numero=None, pdf=None):
        cuerpo = {'tipo': tipo}
        if numero:
            cuerpo['numeroAlbaran'] = numero
        elif serie:
            cuerpo['serie'] = serie
        if pdf and tipo == 'albaran':
            cuerpo['albaranPdf'] = self.pdf_base64(pdf)
        return self._request('POST', f'/envios/{envio_id}/emitir', cuerpo)

    @staticmethod
    def pdf_base64(pdf):
        """El albarán de Odoo como lo pide la API («albaranPdf»): se firma ese y no uno de DecaFirma."""
        return base64.b64encode(pdf).decode()

    def estado(self, envio_id):
        return self._request('GET', f'/envios/{envio_id}')

    def cambiar_vehiculo(self, envio_id, matricula, remolque, motivo):
        return self._request('PATCH', f'/envios/{envio_id}/vehiculo',
                             {'matricula': matricula, 'remolque': remolque or None, 'motivo': motivo})

    def anular(self, documento_id, motivo):
        return self._request('POST', f'/documentos/{documento_id}/anular', {'motivo': motivo})

    def enviar(self, documento_id, correo, con_el_otro=True):
        return self._request('POST', f'/documentos/{documento_id}/enviar', {'correo': correo, 'conElOtro': con_el_otro})

    def documentos_desde(self, desde, limite=200):
        return self._request('GET', '/documentos', params={'desde': desde, 'limite': limite})

    def series(self):
        return self._request('GET', '/series')

    def pdf(self, pdf_url):
        try:
            resp = requests.get(pdf_url, headers=self._headers(), timeout=TIMEOUT)
        except requests.RequestException as e:
            raise UserError(_('No se ha podido descargar el archivo de DecaFirma (%s).', e)) from e
        if resp.status_code != 200:
            raise DecafirmaError(_('DecaFirma no ha dado el archivo (%s).', resp.status_code))
        return resp.content

    # Papeles del porte (la foto de la carta de porte o del ticket de báscula)
    def archivo(self, url):
        """Un archivo de la API (la foto de un papel): la ruta que da DecaFirma o la dirección entera."""
        if url.startswith('/'):
            url = self.base_url + url
        return self.pdf(url)

    def marcar_papel(self, papel_id, facturado):
        return self._request('POST', f'/papeles/{papel_id}/facturado', {'facturado': bool(facturado)})

    def papeles_desde(self, desde, limite=200):
        return self._request('GET', '/papeles', params={'desde': desde, 'limite': limite})

    # Rutas
    def crear_ruta(self, payload):
        return self._request('POST', '/rutas', payload)

    def ruta(self, ruta_id):
        return self._request('GET', f'/rutas/{ruta_id}')

    def anadir_paradas(self, ruta_id, payload):
        return self._request('POST', f'/rutas/{ruta_id}/paradas', payload)

    def quitar_parada(self, ruta_id, envio_id):
        return self._request('DELETE', f'/rutas/{ruta_id}/paradas/{envio_id}')

    def albaranes_ruta(self, ruta_id, serie=None, numeros=None):
        cuerpo = {}
        if numeros:
            cuerpo['numeros'] = numeros
        elif serie:
            cuerpo['serie'] = serie
        return self._request('POST', f'/rutas/{ruta_id}/albaranes', cuerpo)

    # Avisos
    def conectar_webhook(self, url):
        return self._request('POST', '/webhook', {'url': url})

    def probar_webhook(self):
        return self._request('POST', '/webhook/prueba')

    def quitar_webhook(self):
        return self._request('DELETE', '/webhook')

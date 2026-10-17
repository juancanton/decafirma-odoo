import base64
import logging
from urllib.parse import quote

import requests

from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TIMEOUT = 25
# Lo que acepta la API del PDF de su programa («deliveryNotePdf»): cada uno, y entre todos los de una llamada de rutas.
MAX_PDF_PROPIO = 5 * 1024 * 1024
MAX_PDFS_RUTA = 40 * 1024 * 1024


# Los tipos de documento: en Odoo, 'deca' y 'albaran'; en la API, 'deca' y 'delivery_note'.
TIPO_API = {'deca': 'deca', 'albaran': 'delivery_note'}


def mensaje_de_error(data):
    """El texto del error que manda DecaFirma ({"error": {"code", "message", "details"}}), o None."""
    error = data.get('error') if isinstance(data, dict) else None
    if isinstance(error, dict):
        return error.get('message') or error.get('code') or None
    return error or None


class DecafirmaError(Exception):
    """DecaFirma contestó con un error. Su mensaje va en castellano y se puede enseñar tal cual."""


class DecafirmaClient:
    """Cliente de la API v1 de DecaFirma (/api/v1, «Authorization: Bearer dfk_…»).

    La API contesta siempre en JSON ({error: {code, message, details?}} si algo
    falla). Un 422 de un envío (crear, corregir, emitir, cambiar el vehículo) no
    es un fallo de la petición: el envío existe pero el documento no se ha podido
    emitir o corregir, y viene el envío con el motivo en «error» ({code, message}).
    Otro 422 (anular, una ruta) es un error como los demás, con su «error».
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
        h = {'Authorization': f'Bearer {self.api_key}', 'Accept': 'application/json', 'X-DecaFirma-Client': 'Odoo'}
        if self.usuario:
            # Una cabecera solo lleva bytes: con %-escapes, «María» llega entera.
            h['X-DecaFirma-User'] = quote(self.usuario[:80], safe=' ')
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
        if resp.status_code in (200, 201) or (resp.status_code == 422 and isinstance(data, dict) and data.get('id')):
            return resp.status_code, data
        mensaje = mensaje_de_error(data) or resp.reason
        error = data.get('error') if isinstance(data, dict) else None
        detalles = error.get('details') if isinstance(error, dict) else None
        if isinstance(detalles, list):
            mensaje = f"{mensaje} {' '.join(map(str, detalles))}"
        raise DecafirmaError(_('DecaFirma: %(msg)s (%(code)s)', code=resp.status_code, msg=mensaje))

    # Envíos y documentos
    def crear_envio(self, payload):
        return self._request('POST', '/shipments', payload)

    def corregir(self, envio_id, cambios):
        return self._request('PATCH', f'/shipments/{envio_id}', cambios)

    def emitir(self, envio_id, tipo, serie=None, numero=None, pdf=None):
        # «tipo» es el de Odoo ('deca' o 'albaran'); a la API va en inglés ('deca' o 'delivery_note').
        cuerpo = {'type': TIPO_API.get(tipo, tipo)}
        if numero:
            cuerpo['deliveryNoteNumber'] = numero
        elif serie:
            cuerpo['seriesId'] = serie
        if pdf and tipo == 'albaran':
            cuerpo['deliveryNotePdf'] = self.pdf_base64(pdf)
        return self._request('POST', f'/shipments/{envio_id}/issue', cuerpo)

    @staticmethod
    def pdf_base64(pdf):
        """El albarán de Odoo como lo pide la API («deliveryNotePdf»): se firma ese y no uno de DecaFirma."""
        return base64.b64encode(pdf).decode()

    def estado(self, envio_id):
        return self._request('GET', f'/shipments/{envio_id}')

    def cambiar_vehiculo(self, envio_id, matricula, remolque, motivo):
        return self._request('PATCH', f'/shipments/{envio_id}/vehicle',
                             {'plate': matricula, 'trailerPlate': remolque or None, 'reason': motivo})

    def anular(self, documento_id, motivo):
        return self._request('POST', f'/documents/{documento_id}/void', {'reason': motivo})

    def enviar(self, documento_id, correo, con_el_otro=True):
        return self._request('POST', f'/documents/{documento_id}/send', {'email': correo, 'includeRelated': con_el_otro})

    def documentos_desde(self, desde, limite=200):
        return self._request('GET', '/documents', params={'since': desde, 'limit': limite})

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
        return self._request('POST', f'/attachments/{papel_id}/invoiced', {'invoiced': bool(facturado)})

    def papeles_desde(self, desde, limite=200):
        return self._request('GET', '/attachments', params={'since': desde, 'limit': limite})

    # Rutas
    def crear_ruta(self, payload):
        return self._request('POST', '/routes', payload)

    def ruta(self, ruta_id):
        return self._request('GET', f'/routes/{ruta_id}')

    def anadir_paradas(self, ruta_id, payload):
        return self._request('POST', f'/routes/{ruta_id}/stops', payload)

    def quitar_parada(self, ruta_id, envio_id):
        return self._request('DELETE', f'/routes/{ruta_id}/stops/{envio_id}')

    def albaranes_ruta(self, ruta_id, serie=None, numeros=None):
        cuerpo = {}
        if numeros:
            cuerpo['numbers'] = numeros
        elif serie:
            cuerpo['seriesId'] = serie
        return self._request('POST', f'/routes/{ruta_id}/delivery-notes', cuerpo)

    # Avisos
    def conectar_webhook(self, url):
        return self._request('POST', '/webhook', {'url': url})

    def probar_webhook(self):
        return self._request('POST', '/webhook/test')

    def quitar_webhook(self):
        return self._request('DELETE', '/webhook')

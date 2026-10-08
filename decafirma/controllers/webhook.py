import hashlib
import hmac
import json
import logging

from odoo import SUPERUSER_ID, http
from odoo.http import request

_logger = logging.getLogger(__name__)


class DecafirmaWebhook(http.Controller):
    """Los avisos de DecaFirma (ver decafirma.com/desarrolladores, «Avisos a tu programa»).

    Lo primero es comprobar la firma (HMAC-SHA256 del cuerpo con el secreto de
    la empresa): sin eso, cualquiera podría marcar un albarán como firmado. Con
    la firma buena, se lleva el envío a Odoo como si se hubiera pedido.
    """

    @http.route('/decafirma/webhook/<int:company_id>', type='http', auth='public', methods=['POST'], csrf=False, save_session=False)
    def aviso(self, company_id, **_kw):
        cuerpo = request.httprequest.get_data()
        company = request.env['res.company'].sudo().browse(company_id).exists()
        secreto = company.decafirma_webhook_secret if company else None
        firma = request.httprequest.headers.get('X-DecaFirma-Firma', '')
        esperada = 'sha256=' + hmac.new((secreto or '').encode(), cuerpo, hashlib.sha256).hexdigest()
        if not secreto or not hmac.compare_digest(firma, esperada):
            return request.make_json_response({'error': 'firma'}, status=401)
        try:
            datos = json.loads(cuerpo)
        except ValueError:
            return request.make_json_response({'error': 'json'}, status=400)
        envio = datos.get('envio') or {}
        if envio.get('id'):
            # Como OdooBot, no como «Public user»: es quien firma lo que se escribe en el chatter.
            registro = request.env['decafirma.shipment'].with_user(SUPERUSER_ID).search(
                [('company_id', '=', company.id), ('deca_id', '=', envio['id'])], limit=1)
            if registro:
                registro._aplicar(envio)
        return request.make_json_response({'ok': True})

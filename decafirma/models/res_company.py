from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    decafirma_url = fields.Char('Dirección de DecaFirma', default='https://decafirma.com')
    decafirma_api_key = fields.Char('Clave de la API de DecaFirma', groups='base.group_system',
                                    help='Se crea en DecaFirma: Configuración > Conectar tu programa (API). Empieza por dfk_.')
    decafirma_webhook_secret = fields.Char('Secreto de los avisos', groups='base.group_system', copy=False,
                                           help='Lo da DecaFirma al conectar los avisos; con él se comprueba que vienen de DecaFirma.')
    decafirma_webhook_url = fields.Char('Avisos conectados a', readonly=True, copy=False)
    decafirma_last_sync = fields.Datetime('Última puesta al día', readonly=True, copy=False)
    decafirma_last_sync_papeles = fields.Datetime('Última puesta al día de los papeles', readonly=True, copy=False)
    decafirma_numero_odoo = fields.Boolean(
        'Albarán con el número de Odoo',
        help='El albarán de DecaFirma lleva el número del albarán de Odoo (p. ej. WH/OUT/00012). '
             'Si no, lleva la numeración de DecaFirma.')
    decafirma_pdf_odoo = fields.Boolean(
        'Firmar el albarán de Odoo',
        help='Se firma el albarán que hace Odoo, el PDF de siempre: DecaFirma le pone detrás su hoja con el QR y, '
             'al firmar, la conformidad (nombre, DNI, fecha y hora, reservas, firma y fotos). El PDF firmado vuelve '
             'solo y queda adjunto. Lleva el número del albarán de Odoo.')

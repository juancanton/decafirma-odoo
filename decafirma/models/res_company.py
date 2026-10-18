import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


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
    decafirma_formato_albaran = fields.Char(
        'Formato del número del albarán',
        help='La numeración de los albaranes de DecaFirma (su serie por defecto), como en su Configuración: [YY] o [YYYY] es el año y '
             'cada % una cifra (ALB[YY]%%%%% da ALB2600001). Vacío: la de DecaFirma (ALB-2026-0001). '
             'Va a DecaFirma con la puesta a punto.')
    decafirma_pdf_odoo = fields.Boolean(
        'Firmar el albarán de Odoo',
        help='Se firma el albarán que hace Odoo, el PDF de siempre: DecaFirma le pone detrás su hoja con el QR y, '
             'al firmar, la conformidad (nombre, DNI, fecha y hora, reservas, firma y fotos). El PDF firmado vuelve '
             'solo y queda adjunto. Lleva el número del albarán de Odoo.')

    def _decafirma_error_formato(self, formato):
        """Lo que no entiende DecaFirma de un formato de albarán (ALB[YY]%%%%% → ALB2600001), o None si vale."""
        f = (formato or '').strip().upper()
        cifras = f.count('%')
        if not cifras:
            return _('Falta el número. Pon un % por cada cifra: %%%% son cuatro.')
        if cifras > 9:
            return _('Nueve cifras es el máximo.')
        if not f.endswith('%' * cifras):
            return _('El número va al final y todos los % juntos: ALB[YY]%%%%%')
        plantilla = f[:-cifras]
        if len(re.findall(r'\[YY\]|\[YYYY\]', plantilla)) > 1:
            return _('El año solo puede salir una vez.')
        if re.search(r'[^A-Z0-9\-/_.]', re.sub(r'\[YY\]|\[YYYY\]', '', plantilla)):
            return _('En el texto usa solo letras, números, guiones, barras y puntos; el año, [YY] o [YYYY].')
        return None

    @api.constrains('decafirma_formato_albaran')
    def _check_decafirma_formato_albaran(self):
        for c in self.filtered('decafirma_formato_albaran'):
            error = c._decafirma_error_formato(c.decafirma_formato_albaran)
            if error:
                raise ValidationError(_('Formato del número del albarán: %s', error))

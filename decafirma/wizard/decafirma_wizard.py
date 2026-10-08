from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.client import DecafirmaClient, DecafirmaError
from ..models.decafirma_mixin import GRUPO_RESPONSABLE, GRUPO_USUARIO, comprobar_permiso


class DecafirmaWizard(models.TransientModel):
    """Anular un documento (con su motivo) o mandárselo al conductor por correo."""
    _name = 'decafirma.wizard'
    _description = 'DecaFirma: anular o mandar un documento'

    accion = fields.Selection([('anular', 'Anular'), ('enviar', 'Mandar al conductor')], required=True)
    document_id = fields.Many2one('decafirma.document', 'Documento', required=True)
    motivo = fields.Char('Motivo', help='Sale en el documento anulado: quien escanee el QR lo ve.')
    correo = fields.Char('Correo del conductor')
    con_el_otro = fields.Boolean('Con el otro papel del mismo porte', default=True,
                                 help='DeCA y albarán en el mismo correo: el que conduce necesita los dos.')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        doc = self.env['decafirma.document'].browse(res.get('document_id'))
        origen = doc.shipment_id._origen() if doc else None
        conductor = getattr(origen, 'decafirma_driver_id', False) if origen else False
        if conductor and conductor.email and 'correo' in fields_list:
            res['correo'] = conductor.email
        return res

    def action_confirmar(self):
        self.ensure_one()
        comprobar_permiso(self.env, GRUPO_RESPONSABLE if self.accion == 'anular' else GRUPO_USUARIO)
        envio = self.document_id.shipment_id
        client = DecafirmaClient(envio.company_id)
        try:
            if self.accion == 'anular':
                if not (self.motivo or '').strip():
                    raise UserError(_('Pon el motivo de la anulación.'))
                code, data = client.anular(self.document_id.deca_id, self.motivo.strip())
                if code == 422:
                    raise UserError(data.get('error') or _('DecaFirma no lo ha anulado.'))
                envio._aplicar(data)
                return {'type': 'ir.actions.client', 'tag': 'soft_reload'}
            if not (self.correo or '').strip():
                raise UserError(_('Pon el correo del conductor.'))
            client.enviar(self.document_id.deca_id, self.correo.strip(), self.con_el_otro)
        except DecafirmaError as e:
            raise UserError(str(e)) from e
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('DecaFirma'), 'message': _('Mandado a %s.', self.correo.strip()), 'type': 'success'}}

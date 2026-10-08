from odoo import _, fields, models
from odoo.exceptions import UserError

from .client import DecafirmaClient, DecafirmaError
from .decafirma_mixin import GRUPO_RESPONSABLE, comprobar_permiso


class DecafirmaDocument(models.Model):
    _name = 'decafirma.document'
    _description = 'Documento de DecaFirma (DeCA o albarán)'
    _order = 'kind, version desc, id desc'

    shipment_id = fields.Many2one('decafirma.shipment', 'Envío', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='shipment_id.company_id', store=True)
    deca_id = fields.Char('Id en DecaFirma', required=True, index=True)
    kind = fields.Selection([('deca', 'DeCA'), ('albaran', 'Albarán')], 'Tipo', required=True)
    reference = fields.Char('Número')
    version = fields.Integer('Versión')
    state = fields.Selection([('vigente', 'Vigente'), ('anulado', 'Anulado')], 'Estado', default='vigente')
    void_reason = fields.Char('Motivo de la anulación')
    is_demo = fields.Boolean('Demo', help='Emitido durante la prueba: sin validez en un control.')
    url = fields.Char('Enlace del QR')
    sign_url = fields.Char('Enlace de firma')
    pdf_url = fields.Char('PDF')
    issued_at = fields.Datetime('Emitido el')
    signed_at = fields.Datetime('Firmado el')
    signed_by = fields.Char('Firmado por')
    signed_vat = fields.Char('DNI de quien firma')
    signed_remarks = fields.Text('Observaciones al firmar')
    attachment_id = fields.Many2one('ir.attachment', 'PDF descargado', ondelete='set null')
    attachment_version = fields.Integer('Versión descargada', help='La versión del documento que tiene el PDF adjunto.')

    _sql_constraints = [('deca_id_uniq', 'UNIQUE(shipment_id, deca_id)', 'Ese documento ya está en el envío.')]

    def _adjunto_al_dia(self):
        """El PDF adjunto si es el de la última versión (el firmado, si ya se firmó)."""
        self.ensure_one()
        return self.attachment_id if self.attachment_id and self.attachment_version == self.version else None

    def _adjuntar(self):
        """Baja la última versión del PDF y la deja adjunta en el registro de origen.

        Firmado, el nombre lo dice: es el que se guarda junto al albarán de Odoo.
        """
        self.ensure_one()
        if not self.pdf_url:
            raise UserError(_('Ese documento no tiene PDF.'))
        try:
            content = DecafirmaClient(self.company_id).pdf(self.pdf_url)
        except DecafirmaError as e:
            raise UserError(str(e)) from e
        origen = self.shipment_id._origen()
        papel = 'DeCA' if self.kind == 'deca' else 'Albaran'
        firmado = ' firmado' if self.signed_at else ''
        name = f'{papel} {self.reference or self.deca_id}{firmado} v{self.version}.pdf'
        attachment = self.env['ir.attachment'].sudo().create({
            'name': name.replace('/', '-'),
            'raw': content,
            'mimetype': 'application/pdf',
            'res_model': origen._name if origen else self.shipment_id._name,
            'res_id': origen.id if origen else self.shipment_id.id,
        })
        self.sudo().write({'attachment_id': attachment.id, 'attachment_version': self.version})
        return attachment

    def action_download_pdf(self):
        self.ensure_one()
        attachment = self._adjunto_al_dia() or self._adjuntar()
        return {'type': 'ir.actions.act_url', 'url': f'/web/content/{attachment.id}?download=true', 'target': 'self'}

    def action_anular(self):
        comprobar_permiso(self.env, GRUPO_RESPONSABLE)
        self.ensure_one()
        return self.shipment_id._wizard('anular', self)

    def action_enviar(self):
        comprobar_permiso(self.env)
        self.ensure_one()
        return self.shipment_id._wizard('enviar', self)

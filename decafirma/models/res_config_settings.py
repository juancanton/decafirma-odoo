from odoo import fields, models, _
from odoo.exceptions import UserError

from .client import DecafirmaClient, DecafirmaError


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    decafirma_url = fields.Char(related='company_id.decafirma_url', readonly=False)
    decafirma_api_key = fields.Char(related='company_id.decafirma_api_key', readonly=False, groups='base.group_system')
    decafirma_webhook_url = fields.Char(related='company_id.decafirma_webhook_url')
    decafirma_last_sync = fields.Datetime(related='company_id.decafirma_last_sync')
    decafirma_numero_odoo = fields.Boolean(related='company_id.decafirma_numero_odoo', readonly=False)
    decafirma_pdf_odoo = fields.Boolean(related='company_id.decafirma_pdf_odoo', readonly=False)

    def _decafirma_aviso(self, titulo, mensaje, tipo='success'):
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': titulo, 'message': mensaje, 'type': tipo, 'sticky': tipo != 'success'}}

    def action_decafirma_probar(self):
        """Comprueba la clave pidiendo las series del albarán (no crea nada)."""
        self.ensure_one()
        self.execute()
        try:
            _code, data = DecafirmaClient(self.company_id).series()
        except DecafirmaError as e:
            raise UserError(str(e)) from e
        return self._decafirma_aviso(_('DecaFirma'), _('Conectado. El próximo albarán sería el %s.', data.get('siguientePorDefecto') or '—'))

    def action_decafirma_conectar_avisos(self):
        """Apunta esta base de Odoo en DecaFirma para recibir los avisos al momento."""
        self.ensure_one()
        self.execute()
        # get_base_url: en todas las versiones (17 a 20), con el dominio del sitio web si lo hay.
        base = (self.company_id.get_base_url() or '').rstrip('/')
        if not base:
            raise UserError(_('Falta la dirección de esta base de Odoo (parámetro web.base.url).'))
        # Con la base de datos: en un servidor con varias, una llamada de fuera no sabría a cuál va.
        url = f'{base}/decafirma/webhook/{self.company_id.id}?db={self.env.cr.dbname}'
        try:
            _code, data = DecafirmaClient(self.company_id).conectar_webhook(url)
        except DecafirmaError as e:
            raise UserError(str(e)) from e
        self.company_id.sudo().write({'decafirma_webhook_secret': data.get('secreto'), 'decafirma_webhook_url': url})
        # El aviso de prueba llega en otra petición, que tiene que ver ya el
        # secreto guardado: por eso se confirma antes de pedirlo.
        self.env.cr.commit()
        try:
            _code, prueba = DecafirmaClient(self.company_id).probar_webhook()
        except DecafirmaError as e:
            prueba = {'ok': False, 'detalle': str(e)}
        if prueba.get('ok'):
            return self._decafirma_aviso(_('DecaFirma'), _('Avisos conectados: DecaFirma ha llegado hasta Odoo.'))
        return self._decafirma_aviso(
            _('DecaFirma'),
            _('Apuntado, pero el aviso de prueba no ha llegado (%(detalle)s). DecaFirma tiene que poder llegar a %(url)s '
              'desde internet (y, si el servidor tiene varias bases de datos, con dbfilter por dominio). Si no, '
              'la tarea de cada 15 minutos lo pone al día igual.',
              detalle=prueba.get('detalle'), url=url),
            'warning')

    def action_decafirma_quitar_avisos(self):
        self.ensure_one()
        try:
            DecafirmaClient(self.company_id).quitar_webhook()
        except DecafirmaError as e:
            raise UserError(str(e)) from e
        self.company_id.sudo().write({'decafirma_webhook_secret': False, 'decafirma_webhook_url': False})

    def action_decafirma_poner_al_dia(self):
        self.ensure_one()
        self.env['decafirma.shipment']._cron_poner_al_dia(companies=self.company_id)
        return self._decafirma_aviso(_('DecaFirma'), _('Puesto al día.'))

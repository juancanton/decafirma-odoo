import re

from odoo import _, models

# Lo que DecaFirma entiende en el formato de una serie: el año ([YY] o [YYYY]) y un % por cifra al final.
ANOS = {'%(year)s': '[YYYY]', '%(y)s': '[YY]'}


class DecafirmaSyncWizard(models.TransientModel):
    """Con Inventario, la puesta a punto añade los almacenes (como lugares), las series de los albaranes de salida
    y, como clientes, a quien se le entrega."""
    _inherit = 'decafirma.sync.wizard'

    def _sync_clientes_candidatos(self):
        candidatos = super()._sync_clientes_candidatos()
        entregados = self.env['stock.picking'].sudo().search([
            ('picking_type_code', '=', 'outgoing'), ('partner_id', '!=', False),
            ('company_id', '=', self.company_id.id), ('state', '!=', 'cancel')]).partner_id.commercial_partner_id
        return candidatos | entregados

    def _sync_lugares(self, datos):
        super()._sync_lugares(datos)
        for wh in self.env['stock.warehouse'].sudo().search([('company_id', '=', self.company_id.id)]):
            self._sync_lugar(datos, wh, wh.name, wh.partner_id or self.company_id.partner_id)

    @staticmethod
    def _sync_formato(secuencia):
        """El formato de DecaFirma de una secuencia de Odoo (WH/OUT/ y 5 cifras → WH/OUT/%%%%%), o (None, motivo)."""
        if secuencia.suffix:
            return None, _('Lleva sufijo (%s): en DecaFirma el número va al final.', secuencia.suffix)
        prefijo = secuencia.prefix or ''
        for odoo, deca in ANOS.items():
            prefijo = prefijo.replace(odoo, deca)
        if '%(' in prefijo:
            return None, _('El prefijo lleva fechas que DecaFirma no entiende (solo el año).')
        if re.search(r'[^A-Za-z0-9\-/_.\[\]]', prefijo.replace('[YYYY]', '').replace('[YY]', '')):
            return None, _('El prefijo solo puede llevar letras, números, guiones, barras y puntos.')
        cifras = min(max(secuencia.padding or 1, 1), 9)
        return prefijo + '%' * cifras, None

    def _sync_series(self, datos):
        """Las series de los albaranes de salida de Odoo, con su formato y su contador."""
        super()._sync_series(datos)
        tipos = self.env['stock.picking.type'].sudo().search([
            ('code', '=', 'outgoing'), ('company_id', '=', self.company_id.id), ('sequence_id', '!=', False)])
        for t in tipos:
            formato, motivo = self._sync_formato(t.sequence_id)
            if not formato:
                self._sync_fuera(datos, 'series', t.display_name, motivo)
                continue
            # El contador, por donde va en Odoo: DecaFirma solo lo sube (nunca repite números).
            self._sync_anadir(datos, 'series', t, {'name': t.display_name, 'format': formato,
                                                   'nextNumber': t.sequence_id.number_next_actual or None})

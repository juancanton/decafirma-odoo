from odoo import fields, models

# Lo que se manda en la puesta a punto (POST /api/v1/sync), en el orden en que DecaFirma lo aplica:
# un vehículo puede apuntar a una persona de la misma llamada.
TIPOS = ('series', 'customers', 'places', 'people', 'vehicles')


class DecafirmaSyncLink(models.Model):
    """Lo que ya está en DecaFirma: su id y lo último que se mandó.

    Sirve para saber qué se archiva (lo que se mandó y en Odoo se ha archivado o
    borrado) y para que repetir la puesta a punto no toque nada en Odoo.
    """
    _name = 'decafirma.sync.link'
    _description = 'DecaFirma: lo mandado en la puesta a punto'
    _rec_name = 'external_id'

    company_id = fields.Many2one('res.company', required=True, index=True, ondelete='cascade')
    tipo = fields.Selection([(t, t) for t in TIPOS], required=True)
    external_id = fields.Char('Referencia', required=True, index=True)
    deca_id = fields.Char('Id en DecaFirma')
    ultimo = fields.Json('Lo último mandado')

    def _registro(self):
        """El registro de Odoo de «odoo:modelo:id», o un vacío si ya no está (o no es de Odoo)."""
        self.ensure_one()
        partes = (self.external_id or '').split(':')
        if len(partes) < 3 or partes[0] != 'odoo' or partes[1] not in self.env or not partes[2].isdigit():
            return None
        return self.env[partes[1]].sudo().with_context(active_test=False).browse(int(partes[2])).exists()
